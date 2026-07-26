from sqlalchemy import select

from app.core.database import get_default_org_and_user
from app.models.audit import AuditEvent
from app.models.document import Document, DocumentPage
from app.models.evidence import EvidenceLink
from app.models.job import ProcessingJob
from app.models.project import ProposalProject
from app.models.requirement import Requirement
from app.models.response import DraftResponse
from app.models.review import ReviewTask
from app.services.ingestion_state import IngestionStatus
from tests.integration.test_projects import create_test_pdf


def test_knowledge_upload_route_reaches_scanning_and_forces_pending(client, db):
    """A5b: the knowledge upload route routes through quarantine-first
    ingestion and stops at SCANNING/REJECTED_TYPE. It must never trust a
    client-submitted approval_status - every newly uploaded knowledge
    document is forced to PENDING regardless of what the form claims, and
    no legacy document_processing job or DocumentPage is created here."""
    org_id, user_id = get_default_org_and_user(db)

    project = ProposalProject(
        organization_id=org_id,
        created_by_id=user_id,
        name="Knowledge Base Bid",
        client_name="Oscorp",
        status="draft",
    )
    db.add(project)
    db.commit()

    pdf_content = create_test_pdf(
        [
            "We support SSO authentication protocols including SAML and OAuth.",
            "System authentication details page.",
        ]
    )

    payload = {
        "owner_name": "Security Team",
        "tags": "sso, auth",
        "approval_status": "APPROVED",  # forged; must be ignored
        "version": "2.1",
        "review_date": "2026-12-31",
    }
    upload_file = {"file": ("kb_approved.pdf", pdf_content, "application/pdf")}

    upload_resp = client.post(
        f"/projects/{project.id}/knowledge",
        data=payload,
        files=upload_file,
        follow_redirects=False,
    )
    assert upload_resp.status_code == 303

    doc = db.scalars(
        select(Document).where(
            Document.project_id == project.id,
            Document.doc_role == "knowledge_base",
        )
    ).one()
    assert doc.owner_name == "Security Team"
    assert doc.tags == "sso, auth"
    assert doc.ingestion_status == IngestionStatus.SCANNING
    # The forged "APPROVED" form value must never be trusted.
    assert doc.approval_status == "PENDING"

    # No parsing/pages and no legacy processing job happen at this phase.
    pages = db.scalars(
        select(DocumentPage).where(DocumentPage.document_id == doc.id)
    ).all()
    assert pages == []
    assert db.scalar(
        select(ProcessingJob).where(ProcessingJob.document_id == doc.id)
    ) is None

    # Verify audit event for the quarantine write.
    audit = db.scalars(
        select(AuditEvent).where(
            AuditEvent.action == "document_upload_quarantined",
            AuditEvent.entity_id == doc.id,
        )
    ).first()
    assert audit is not None


def test_knowledge_base_flow(client, db):
    """Evidence linking, FTS retrieval, and AI drafting against knowledge
    documents. Parsing (DocumentPage creation) and approval review are a
    later phase (A5c+) than the quarantine-first upload route covered by
    A5b, so this test builds already-parsed/approved rows directly rather
    than going through the upload route - the route's own behavior is
    covered separately by
    test_knowledge_upload_route_reaches_scanning_and_forces_pending."""
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
        mandatory=False,
        status="NOT_STARTED",
    )
    db.add(req)
    db.commit()

    # 2. Directly construct an APPROVED, already-parsed knowledge document
    # (post-quarantine state, out of A5b's scope to produce via the route).
    doc = Document(
        project_id=project.id,
        name="kb_approved.pdf",
        file_path="mock_quarantine_path.pdf",
        file_type="application/pdf",
        doc_role="knowledge_base",
        processing_status="completed",
        owner_name="Security Team",
        tags="sso, auth",
        approval_status="APPROVED",
        version="2.1",
        created_by_id=user_id,
    )
    db.add(doc)
    db.commit()
    db.refresh(doc)

    page1 = DocumentPage(
        document_id=doc.id,
        page_number=1,
        content="We support SSO authentication protocols including SAML and OAuth.",
    )
    page2 = DocumentPage(
        document_id=doc.id,
        page_number=2,
        content="System authentication details page.",
    )
    db.add_all([page1, page2])
    db.commit()

    pages = db.scalars(
        select(DocumentPage).where(DocumentPage.document_id == doc.id)
    ).all()
    assert len(pages) == 2
    assert "SSO authentication" in pages[0].content

    # 3. Directly construct a PENDING (unapproved), already-parsed
    # knowledge document.
    doc_unapproved = Document(
        project_id=project.id,
        name="kb_unapproved.pdf",
        file_path="mock_quarantine_path_2.pdf",
        file_type="application/pdf",
        doc_role="knowledge_base",
        processing_status="completed",
        owner_name="Drafting Team",
        tags="sso, draft",
        approval_status="PENDING",
        version="1.0",
        created_by_id=user_id,
    )
    db.add(doc_unapproved)
    db.commit()
    db.refresh(doc_unapproved)

    db.add(
        DocumentPage(
            document_id=doc_unapproved.id,
            page_number=1,
            content="Draft detail: SSO authentication might be supported later.",
        )
    )
    db.commit()
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
