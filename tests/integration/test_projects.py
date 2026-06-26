import io
from urllib.parse import unquote

from reportlab.pdfgen import canvas
from sqlalchemy import select

from app.core.database import get_default_org_and_user
from app.models.audit import AuditEvent
from app.models.document import Document, DocumentPage
from app.models.project import ProposalProject


def create_test_pdf(pages_text: list[str]) -> bytes:
    """Helper to generate an in-memory PDF."""
    buffer = io.BytesIO()
    c = canvas.Canvas(buffer)
    for text in pages_text:
        c.drawString(100, 750, text)
        c.showPage()
    c.save()
    buffer.seek(0)
    return buffer.getvalue()


def test_project_creation_and_listing(client, db):
    # Retrieve default org/user to verify relationships
    org_id, user_id = get_default_org_and_user(db)

    # 1. Post to create a project
    payload = {
        "name": "Acme Security RFP",
        "client_name": "Acme Corp",
        "due_date": "2026-12-31",
    }
    response = client.post("/projects", data=payload, follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/projects"

    # 2. Verify project in database
    project = db.scalars(
        select(ProposalProject).where(ProposalProject.name == "Acme Security RFP")
    ).first()
    assert project is not None
    assert project.client_name == "Acme Corp"
    assert project.status == "draft"
    assert project.organization_id == org_id
    assert project.created_by_id == user_id

    # 3. Verify audit event
    audit = db.scalars(
        select(AuditEvent).where(
            AuditEvent.action == "project_create", AuditEvent.entity_id == project.id
        )
    ).first()
    assert audit is not None
    assert audit.organization_id == org_id

    # 4. Verify project listed in UI
    list_response = client.get("/projects")
    assert list_response.status_code == 200
    assert "Acme Security RFP" in list_response.text
    assert "Acme Corp" in list_response.text


def test_project_detail_and_rfp_upload_flow(client, db, tmp_path):
    org_id, user_id = get_default_org_and_user(db)

    # 1. Create a project directly in DB
    project = ProposalProject(
        organization_id=org_id,
        created_by_id=user_id,
        name="Security Platform bid",
        client_name="Stark Industries",
        status="draft",
    )
    db.add(project)
    db.commit()

    # 2. View detail page (no RFP yet)
    detail_resp = client.get(f"/projects/{project.id}")
    assert detail_resp.status_code == 200
    assert "Security Platform bid" in detail_resp.text
    assert "No RFP document has been uploaded" in detail_resp.text

    pdf_content = create_test_pdf(
        [
            "Requirement 1: System must be secure.",
            "Requirement 2: Must support multi-tenant login.",
        ]
    )

    # 4. Upload RFP document
    upload_file = {"file": ("rfp.pdf", pdf_content, "application/pdf")}
    upload_resp = client.post(
        f"/projects/{project.id}/upload", files=upload_file, follow_redirects=False
    )
    assert upload_resp.status_code == 303
    assert upload_resp.headers["location"] == f"/projects/{project.id}"

    # 5. Verify document in DB (FastAPI TestClient runs BackgroundTasks synchronously)
    doc = db.scalars(
        select(Document).where(
            Document.project_id == project.id, Document.doc_role == "rfp"
        )
    ).first()
    assert doc is not None
    assert doc.name == "rfp.pdf"
    assert doc.file_type == "application/pdf"
    assert doc.processing_status == "completed"

    # 6. Verify DocumentPages
    pages = db.scalars(
        select(DocumentPage)
        .where(DocumentPage.document_id == doc.id)
        .order_by(DocumentPage.page_number.asc())
    ).all()
    assert len(pages) == 2
    assert pages[0].page_number == 1
    assert "Requirement 1" in pages[0].content
    assert pages[1].page_number == 2
    assert "Requirement 2" in pages[1].content

    # 7. Verify audit events (document_upload & document_extraction_success)
    upload_audit = db.scalars(
        select(AuditEvent).where(
            AuditEvent.action == "document_upload", AuditEvent.entity_id == doc.id
        )
    ).first()
    assert upload_audit is not None

    success_audit = db.scalars(
        select(AuditEvent).where(
            AuditEvent.action == "document_extraction_success",
            AuditEvent.entity_id == doc.id,
        )
    ).first()
    assert success_audit is not None

    # 8. Check project detail page now displays processed document stats
    detail_after_resp = client.get(f"/projects/{project.id}")
    assert detail_after_resp.status_code == 200
    assert "PROCESSED" in detail_after_resp.text
    assert "PAGES EXTRACTED" in detail_after_resp.text
    assert "2" in detail_after_resp.text  # Page count

    # 9. Try uploading a second RFP document (should fail)
    duplicate_resp = client.post(
        f"/projects/{project.id}/upload",
        files={"file": ("rfp2.pdf", pdf_content, "application/pdf")},
        follow_redirects=False,
    )
    assert duplicate_resp.status_code == 303
    assert "already has an RFP document" in unquote(duplicate_resp.headers["location"])
