from sqlalchemy import select

from app.core.database import get_default_org_and_user
from app.models.audit import AuditEvent
from app.models.document import Document, DocumentPage
from app.models.evidence import EvidenceLink
from app.models.project import ProposalProject
from app.models.requirement import Requirement
from app.models.response import DraftResponse
from app.models.review import ReviewTask
from tests.integration.test_projects import create_test_pdf


def test_knowledge_base_flow(client, db):
    org_id, user_id = get_default_org_and_user(db)

    # 1. Setup Project & Requirement
    project = ProposalProject(
        organization_id=org_id,
        created_by_id=user_id,
        name="Knowledge Base Bid",
        client_name="Oscorp",
        status="draft",
    )
    db.add(project)
    db.commit()

    req = Requirement(
        project_id=project.id,
        original_text="The system must support SSO authentication.",
        source_section="Sec 3.1",
        source_page=2,
        requirement_type="Technical",
        mandatory=True,
        status="NOT_STARTED",
    )
    db.add(req)
    db.commit()

    # 2. Generate PDF file for APPROVED knowledge base document
    pdf_content = create_test_pdf(
        [
            "We support SSO authentication protocols including SAML and OAuth.",
            "System authentication details page.",
        ]
    )

    payload = {
        "owner_name": "Security Team",
        "tags": "sso, auth",
        "approval_status": "APPROVED",
        "version": "2.1",
        "review_date": "2026-12-31",
    }
    upload_file = {"file": ("kb_approved.pdf", pdf_content, "application/pdf")}

    # 3. Upload APPROVED knowledge base document
    upload_resp = client.post(
        f"/projects/{project.id}/knowledge",
        data=payload,
        files=upload_file,
        follow_redirects=False,
    )
    assert upload_resp.status_code == 303

    # Verify document and page created
    doc = db.scalars(
        select(Document).where(
            Document.project_id == project.id,
            Document.doc_role == "knowledge_base",
            Document.approval_status == "APPROVED",
        )
    ).first()
    assert doc is not None
    assert doc.owner_name == "Security Team"
    assert doc.tags == "sso, auth"
    assert doc.processing_status == "completed"

    pages = db.scalars(
        select(DocumentPage).where(DocumentPage.document_id == doc.id)
    ).all()
    assert len(pages) == 2
    assert "SSO authentication" in pages[0].content

    # Verify audit event for upload
    audit = db.scalars(
        select(AuditEvent).where(
            AuditEvent.action == "knowledge_upload",
            AuditEvent.entity_id == doc.id,
        )
    ).first()
    assert audit is not None

    # 4. Upload UNAPPROVED knowledge base document
    pdf_content_unapproved = create_test_pdf(
        [
            "Draft detail: SSO authentication might be supported later.",
        ]
    )
    payload_unapproved = {
        "owner_name": "Drafting Team",
        "tags": "sso, draft",
        "approval_status": "PENDING",
        "version": "1.0",
    }
    upload_resp_unapproved = client.post(
        f"/projects/{project.id}/knowledge",
        data=payload_unapproved,
        files={
            "file": ("kb_unapproved.pdf", pdf_content_unapproved, "application/pdf")
        },
        follow_redirects=False,
    )
    assert upload_resp_unapproved.status_code == 303

    doc_unapproved = db.scalars(
        select(Document).where(
            Document.project_id == project.id,
            Document.doc_role == "knowledge_base",
            Document.approval_status == "PENDING",
        )
    ).first()
    assert doc_unapproved is not None

    # 5. Access Workspace GET - verify FTS retrieves APPROVED but NOT UNAPPROVED text
    ws_resp = client.get(f"/requirements/{req.id}/workspace?q=SSO")
    assert ws_resp.status_code == 200
    assert "We support SSO authentication" in ws_resp.text
    assert "kb_approved.pdf" in ws_resp.text
    # Should NOT find the pending document text
    assert "Draft detail: SSO authentication" not in ws_resp.text

    # 6. Link Evidence Link
    link_payload = {
        "document_id": str(doc.id),
        "snippet": "We support SSO authentication protocols including SAML and OAuth.",
        "page_number": 1,
        "score": 0.99,
    }
    link_resp = client.post(
        f"/requirements/{req.id}/evidence/link",
        data=link_payload,
        follow_redirects=False,
    )
    assert link_resp.status_code == 303

    # Assert evidence link is in database
    ev_link = db.scalars(
        select(EvidenceLink).where(EvidenceLink.requirement_id == req.id)
    ).first()
    assert ev_link is not None
    assert ev_link.snippet == link_payload["snippet"]
    assert ev_link.document_id == doc.id

    # Verify audit event for link
    link_audit = db.scalars(
        select(AuditEvent).where(AuditEvent.action == "evidence_link")
    ).first()
    assert link_audit is not None

    # 7. AI Drafting (with evidence)
    draft_resp = client.post(
        f"/requirements/{req.id}/draft",
        follow_redirects=False,
    )
    assert draft_resp.status_code == 303

    db.expire_all()
    draft = db.scalars(
        select(DraftResponse)
        .where(DraftResponse.requirement_id == req.id)
        .order_by(DraftResponse.version.desc())
    ).first()
    assert draft is not None
    assert draft.needs_evidence is False
    assert "SSO authentication" in draft.content
    assert draft.confidence == 0.85

    # 8. AI Drafting (without evidence)
    db.delete(ev_link)
    db.commit()

    draft_empty_resp = client.post(
        f"/requirements/{req.id}/draft",
        follow_redirects=False,
    )
    assert draft_empty_resp.status_code == 303

    db.expire_all()
    draft_empty = db.scalars(
        select(DraftResponse)
        .where(DraftResponse.requirement_id == req.id)
        .order_by(DraftResponse.version.desc())
    ).first()
    assert draft_empty is not None
    assert draft_empty.needs_evidence is True
    assert draft_empty.content == "NEEDS_EVIDENCE"

    # 9. Edit draft manually
    edit_payload = {"content": "SSO is fully supported via Okta."}
    edit_resp = client.post(
        f"/requirements/{req.id}/draft/edit",
        data=edit_payload,
        follow_redirects=False,
    )
    assert edit_resp.status_code == 303

    db.expire_all()
    assert draft_empty.content == "SSO is fully supported via Okta."

    # 10. Approve draft response
    approve_resp = client.post(
        f"/requirements/{req.id}/draft/approve",
        follow_redirects=False,
    )
    assert approve_resp.status_code == 303
    db.expire_all()
    assert draft_empty.status == "approved"
    assert req.status == "APPROVED"

    # 11. Reject draft response
    reject_resp = client.post(
        f"/requirements/{req.id}/draft/reject",
        follow_redirects=False,
    )
    assert reject_resp.status_code == 303
    db.expire_all()
    assert draft_empty.status == "rejected"
    assert req.status == "REJECTED"

    # 12. Assign Reviewer
    assign_resp = client.post(
        f"/requirements/{req.id}/assign",
        data={"reviewer_name": "Bruce Banner"},
        follow_redirects=False,
    )
    assert assign_resp.status_code == 303
    db.expire_all()
    assert req.owner_name == "Bruce Banner"
    assert req.status == "NEEDS_REVIEW"

    # Check ReviewTask created
    task = db.scalars(
        select(ReviewTask).where(ReviewTask.requirement_id == req.id)
    ).first()
    assert task is not None
    assert task.status == "open"
    assert "Bruce Banner" in task.reviewer_notes
