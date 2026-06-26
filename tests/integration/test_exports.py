from app.core.database import get_default_org_and_user
from app.models.project import ProposalProject
from app.models.requirement import Requirement
from app.models.response import DraftResponse


def test_xlsx_and_docx_exports(client, db):
    org_id, user_id = get_default_org_and_user(db)

    # 1. Setup project
    project = ProposalProject(
        organization_id=org_id,
        created_by_id=user_id,
        name="Export Test Bid",
        client_name="Starfleet",
        status="draft",
    )
    db.add(project)
    db.commit()

    # 2. Add two requirements
    req_approved = Requirement(
        project_id=project.id,
        original_text="SSO support is required.",
        source_section="Section 1",
        source_page=1,
        status="APPROVED",
    )
    req_unapproved = Requirement(
        project_id=project.id,
        original_text="Backup power is required.",
        source_section="Section 2",
        source_page=2,
        status="DRAFTED",
    )
    db.add_all([req_approved, req_unapproved])
    db.commit()

    # 3. Add approved draft response
    draft = DraftResponse(
        requirement_id=req_approved.id,
        content="Approved answer for SSO.",
        status="approved",
    )
    # Add unapproved draft response
    draft_unapproved = DraftResponse(
        requirement_id=req_unapproved.id,
        content="Draft answer for backup.",
        status="draft",
    )
    db.add_all([draft, draft_unapproved])
    db.commit()

    # 4. Test XLSX Compliance Matrix Export
    xlsx_resp = client.get(f"/projects/{project.id}/export/matrix")
    assert xlsx_resp.status_code == 200
    assert xlsx_resp.headers["content-type"] == (
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    # ZIP file magic number check
    assert xlsx_resp.content.startswith(b"PK\x03\x04")

    # 5. Test DOCX Proposal Draft Export
    docx_resp = client.get(f"/projects/{project.id}/export/proposal")
    assert docx_resp.status_code == 200
    assert docx_resp.headers["content-type"] == (
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    )
    assert docx_resp.content.startswith(b"PK\x03\x04")

    # Verify that only the approved answer is present in exported docx
    # We can parse the document from the response stream
    import io

    from docx import Document as DocxDocument

    doc = DocxDocument(io.BytesIO(docx_resp.content))

    text_content = []
    for p in doc.paragraphs:
        if p.text:
            text_content.append(p.text)

    # Combine paragraphs to search text
    full_text = "\n".join(text_content)
    assert "Approved answer for SSO." in full_text
    assert "Draft answer for backup." not in full_text
