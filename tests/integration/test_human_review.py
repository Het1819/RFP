import uuid

from sqlalchemy import select

from app.core.database import get_default_org_and_user
from app.models.audit import AuditEvent
from app.models.comment import RequirementComment
from app.models.project import ProposalProject
from app.models.requirement import Requirement
from app.models.response import DraftResponse
from tests.integration.test_csrf import extract_csrf_token


def test_unauthenticated_assignment_fails(client, db, monkeypatch):
    """Proves that unauthenticated access is blocked when APP_ENV is production."""
    from app.core.config import settings

    monkeypatch.setattr(settings, "APP_ENV", "production")
    monkeypatch.setattr(settings, "AUTH_MODE", "production")

    response = client.post(
        f"/requirements/{uuid.uuid4()}/assign",
        data={"assigned_to_user_id": str(uuid.uuid4())},
        follow_redirects=False,
    )
    assert response.status_code == 401


def test_assignment_requires_csrf(client, db):
    """Proves that assignment POST routes require CSRF validation."""
    org_id, user_id = get_default_org_and_user(db)
    proj = ProposalProject(
        organization_id=org_id,
        created_by_id=user_id,
        name="CSRF Assign Proj",
        client_name="Acme",
    )
    db.add(proj)
    db.commit()

    req = Requirement(project_id=proj.id, original_text="CSRF Req")
    db.add(req)
    db.commit()

    response = client.post(
        f"/requirements/{req.id}/assign",
        data={"assigned_to_user_id": str(user_id)},
        headers={"X-Test-Enforce-CSRF": "true"},
        follow_redirects=False,
    )
    assert response.status_code == 403


def test_reviewer_must_belong_to_same_org(client, db):
    """Proves assignment to a non-existent or foreign-org user fails closed (404)."""
    org_id, user_id = get_default_org_and_user(db)

    # Create foreign organization and user
    from app.models.organization import Organization
    from app.models.user import User

    foreign_org = Organization(id=uuid.uuid4(), name="Foreign Org")
    db.add(foreign_org)
    db.commit()

    foreign_user = User(
        id=uuid.uuid4(),
        organization_id=foreign_org.id,
        email="foreign_reviewer@test.com",
        hashed_password="...",
        full_name="Foreign Reviewer",
    )
    db.add(foreign_user)

    proj = ProposalProject(
        organization_id=org_id,
        created_by_id=user_id,
        name="Org Scope Proj",
        client_name="Acme",
    )
    db.add(proj)
    db.commit()

    req = Requirement(project_id=proj.id, original_text="Org Scope Req")
    db.add(req)
    db.commit()

    # Get CSRF token
    get_resp = client.get("/projects")
    csrf_token = extract_csrf_token(get_resp.text)

    # Post assignment to foreign reviewer
    response = client.post(
        f"/requirements/{req.id}/assign",
        data={"assigned_to_user_id": str(foreign_user.id), "csrf_token": csrf_token},
        headers={"X-Test-Enforce-CSRF": "true"},
        follow_redirects=False,
    )
    assert response.status_code == 404


def test_valid_reviewer_assignment_succeeds(client, db):
    """Proves assignment to same-org user works and logs correct audit events."""
    org_id, user_id = get_default_org_and_user(db)
    proj = ProposalProject(
        organization_id=org_id,
        created_by_id=user_id,
        name="Valid Assign Proj",
        client_name="Acme",
    )
    db.add(proj)
    db.commit()

    req = Requirement(project_id=proj.id, original_text="Valid Assign Req")
    db.add(req)
    db.commit()

    get_resp = client.get("/projects")
    csrf_token = extract_csrf_token(get_resp.text)

    response = client.post(
        f"/requirements/{req.id}/assign",
        data={"assigned_to_user_id": str(user_id), "csrf_token": csrf_token},
        headers={"X-Test-Enforce-CSRF": "true"},
        follow_redirects=False,
    )
    assert response.status_code == 303

    db.refresh(req)
    assert req.assigned_to_user_id == user_id
    assert req.status == "NEEDS_REVIEW"

    # Verify audit event
    audit = db.scalars(
        select(AuditEvent).where(
            AuditEvent.action == "REVIEW_ASSIGNED", AuditEvent.entity_id == req.id
        )
    ).first()
    assert audit is not None
    assert audit.details["reviewer_user_id"] == str(user_id)


def test_request_changes_requires_reason(client, db):
    """Proves changes endpoint validates for non-empty reason and comment."""
    org_id, user_id = get_default_org_and_user(db)
    proj = ProposalProject(
        organization_id=org_id,
        created_by_id=user_id,
        name="Changes Proj",
        client_name="Acme",
    )
    db.add(proj)
    db.commit()

    req = Requirement(project_id=proj.id, original_text="Changes Req")
    db.add(req)
    db.commit()

    get_resp = client.get("/projects")
    csrf_token = extract_csrf_token(get_resp.text)

    # Empty reason fails
    response = client.post(
        f"/requirements/{req.id}/review/changes-requested",
        data={"reason": "   ", "csrf_token": csrf_token},
        headers={"X-Test-Enforce-CSRF": "true"},
        follow_redirects=False,
    )
    assert response.status_code == 400

    # Valid reason succeeds
    response2 = client.post(
        f"/requirements/{req.id}/review/changes-requested",
        data={"reason": "Rewrite section 2.", "csrf_token": csrf_token},
        headers={"X-Test-Enforce-CSRF": "true"},
        follow_redirects=False,
    )
    assert response2.status_code == 303

    db.refresh(req)
    assert req.status == "CHANGES_REQUESTED"

    comment = db.scalars(
        select(RequirementComment).where(RequirementComment.requirement_id == req.id)
    ).first()
    assert comment is not None
    assert comment.content == "Rewrite section 2."
    assert comment.decision_type == "CHANGES_REQUESTED"


def test_reject_requires_reason(client, db):
    """Proves reject endpoint validates for non-empty reason."""
    org_id, user_id = get_default_org_and_user(db)
    proj = ProposalProject(
        organization_id=org_id,
        created_by_id=user_id,
        name="Reject Proj",
        client_name="Acme",
    )
    db.add(proj)
    db.commit()

    req = Requirement(project_id=proj.id, original_text="Reject Req")
    db.add(req)
    db.commit()

    get_resp = client.get("/projects")
    csrf_token = extract_csrf_token(get_resp.text)

    response = client.post(
        f"/requirements/{req.id}/review/reject",
        data={"reason": "", "csrf_token": csrf_token},
        headers={"X-Test-Enforce-CSRF": "true"},
        follow_redirects=False,
    )
    assert response.status_code in (400, 422)

    response_space = client.post(
        f"/requirements/{req.id}/review/reject",
        data={"reason": "   ", "csrf_token": csrf_token},
        headers={"X-Test-Enforce-CSRF": "true"},
        follow_redirects=False,
    )
    assert response_space.status_code == 400


def test_notes_are_escaped_in_workspace(client, db):
    """Proves comment content is HTML-escaped when rendering workspace."""
    org_id, user_id = get_default_org_and_user(db)
    proj = ProposalProject(
        organization_id=org_id,
        created_by_id=user_id,
        name="Escape Proj",
        client_name="Acme",
    )
    db.add(proj)
    db.commit()

    req = Requirement(project_id=proj.id, original_text="Escape Req")
    db.add(req)
    db.commit()

    comment = RequirementComment(
        requirement_id=req.id,
        author_user_id=user_id,
        content="<script>alert('malicious')</script>",
        decision_type="NOTE",
    )
    db.add(comment)
    db.commit()

    resp = client.get(f"/requirements/{req.id}/workspace")
    assert resp.status_code == 200
    assert "<script>alert" not in resp.text
    assert "&lt;script&gt;alert" in resp.text


def test_review_task_filters(client, db):
    """Proves compliance matrix queue filtering returns correct items."""
    org_id, user_id = get_default_org_and_user(db)
    proj = ProposalProject(
        organization_id=org_id,
        created_by_id=user_id,
        name="Filters Proj",
        client_name="Acme",
    )
    db.add(proj)
    db.commit()

    r1 = Requirement(
        project_id=proj.id, original_text="R1 Needs Evidence", status="NEEDS_EVIDENCE"
    )
    r2 = Requirement(
        project_id=proj.id,
        original_text="R2 Needs Review",
        status="NEEDS_REVIEW",
        assigned_to_user_id=user_id,
    )
    r3 = Requirement(project_id=proj.id, original_text="R3 Approved", status="APPROVED")
    db.add_all([r1, r2, r3])
    db.commit()

    # Filter assigned_to_me
    resp1 = client.get(f"/projects/{proj.id}/matrix?filter=assigned_to_me")
    assert resp1.status_code == 200
    assert "R2 Needs Review" in resp1.text
    assert "R1 Needs Evidence" not in resp1.text
    assert "R3 Approved" not in resp1.text

    # Filter needs_evidence
    resp2 = client.get(f"/projects/{proj.id}/matrix?filter=needs_evidence")
    assert resp2.status_code == 200
    assert "R1 Needs Evidence" in resp2.text
    assert "R2 Needs Review" not in resp2.text


def test_export_preserves_not_approved_marking(client, db):
    """Proves DOCX export clearly flags unapproved/NEEDS_REVIEW items."""
    org_id, user_id = get_default_org_and_user(db)
    proj = ProposalProject(
        organization_id=org_id,
        created_by_id=user_id,
        name="Export Proj",
        client_name="Acme",
    )
    db.add(proj)
    db.commit()

    req = Requirement(
        project_id=proj.id,
        original_text="Grounding req text",
        status="NEEDS_REVIEW",
        source_section="Sect10",
    )
    db.add(req)
    db.commit()

    draft = DraftResponse(
        requirement_id=req.id,
        content="Unapproved draft answer response content.",
        status="needs_review",
        version=1,
        confidence=0.5,
    )
    db.add(draft)
    db.commit()

    resp = client.get(f"/projects/{proj.id}/export/proposal")
    assert resp.status_code == 200

    import io

    from docx import Document as DocxDocument

    doc = DocxDocument(io.BytesIO(resp.content))
    headings = [p.text for p in doc.paragraphs if p.style.name.startswith("Heading")]
    assert any("NEEDS_REVIEW" in h for h in headings) or any(
        "Not Approved" in h for h in headings
    )
