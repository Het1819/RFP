import uuid

import pytest
from fastapi import HTTPException

from app.core.database import get_default_org_and_user
from app.core.security import get_current_org_and_user
from app.models.document import Document, DocumentPage
from app.models.project import ProposalProject
from app.models.requirement import Requirement


def test_production_app_env_blocks_default_user(db, monkeypatch):
    """Proves that a production APP_ENV does not silently fallback/create
    default org/user."""
    from fastapi import Request

    from app.core.config import settings

    monkeypatch.setattr(settings, "APP_ENV", "production")
    mock_request = Request(scope={"type": "http", "session": {}})
    with pytest.raises(HTTPException) as exc_info:
        get_current_org_and_user(mock_request, db)
    assert exc_info.value.status_code == 401
    assert "Authentication required" in exc_info.value.detail


def test_cross_project_evidence_linking_blocked(client, db):
    """Proves evidence from Project A cannot be linked to requirement in Project B."""
    org_id, user_id = get_default_org_and_user(db)

    # 1. Setup Project A and a knowledge document
    proj_a = ProposalProject(
        organization_id=org_id,
        created_by_id=user_id,
        name="Project A",
        client_name="Client A",
        status="draft",
    )
    db.add(proj_a)
    db.commit()

    doc_a = Document(
        id=uuid.uuid4(),
        project_id=proj_a.id,
        name="Doc A",
        file_path="./data/doc_a.pdf",
        file_type="application/pdf",
        doc_role="knowledge_base",
        processing_status="completed",
        approval_status="APPROVED",
        created_by_id=user_id,
        content="This is secret source snippet text from Project A.",
    )
    db.add(doc_a)

    page_a = DocumentPage(
        document_id=doc_a.id,
        page_number=1,
        content="This is secret source snippet text from Project A.",
    )
    db.add(page_a)
    db.commit()

    # 2. Setup Project B and a requirement
    proj_b = ProposalProject(
        organization_id=org_id,
        created_by_id=user_id,
        name="Project B",
        client_name="Client B",
        status="draft",
    )
    db.add(proj_b)
    db.commit()

    req_b = Requirement(
        project_id=proj_b.id,
        original_text="Requirement in project B",
        status="NOT_STARTED",
    )
    db.add(req_b)
    db.commit()

    # 3. Attempt to link doc_a (Project A) to req_b (Project B)
    link_payload = {
        "document_id": str(doc_a.id),
        "snippet": "This is secret source snippet text from Project A.",
        "page_number": 1,
        "score": 0.9,
    }
    response = client.post(
        f"/requirements/{req_b.id}/evidence/link",
        data=link_payload,
        follow_redirects=False,
    )
    # Must fail — 400 or 404 (service prevents leaking project existence)
    assert response.status_code in (400, 404)
    # Snippet must not be linked


def test_foreign_document_id_rejected(client, db):
    """Proves document from a foreign organization cannot be retrieved or linked."""
    org_id_own, user_id_own = get_default_org_and_user(db)

    # Create foreign organization
    from app.models.organization import Organization
    from app.models.user import User

    foreign_org = Organization(id=uuid.uuid4(), name="Foreign Org")
    db.add(foreign_org)
    db.commit()

    foreign_user = User(
        id=uuid.uuid4(),
        organization_id=foreign_org.id,
        email="foreign@rfparchitect.com",
        hashed_password="...",
        full_name="Foreigner",
    )
    db.add(foreign_user)
    db.commit()

    # Create foreign project and document
    proj_foreign = ProposalProject(
        organization_id=foreign_org.id,
        created_by_id=foreign_user.id,
        name="Foreign Project",
        client_name="Client Foreign",
    )
    db.add(proj_foreign)
    db.commit()

    doc_foreign = Document(
        id=uuid.uuid4(),
        project_id=proj_foreign.id,
        name="Foreign Doc",
        file_path="./data/foreign.pdf",
        file_type="application/pdf",
        doc_role="knowledge_base",
        processing_status="completed",
        approval_status="APPROVED",
        created_by_id=foreign_user.id,
    )
    db.add(doc_foreign)
    db.commit()

    # Create own project & requirement
    proj_own = ProposalProject(
        organization_id=org_id_own,
        created_by_id=user_id_own,
        name="Own Project",
        client_name="Client Own",
    )
    db.add(proj_own)
    db.commit()

    req_own = Requirement(
        project_id=proj_own.id,
        original_text="Own requirement",
    )
    db.add(req_own)
    db.commit()

    # Link attempt
    link_payload = {
        "document_id": str(doc_foreign.id),
        "snippet": "Foreign text",
        "score": 0.5,
    }
    response = client.post(
        f"/requirements/{req_own.id}/evidence/link",
        data=link_payload,
        follow_redirects=False,
    )
    # Must fail with 404 (or 403) to prevent leaking existence of foreign document
    assert response.status_code == 404


def test_cross_project_merge_blocked(client, db):
    """Proves requirements from Project A cannot be merged through Project B
    endpoint."""
    org_id, user_id = get_default_org_and_user(db)

    proj_a = ProposalProject(
        organization_id=org_id,
        created_by_id=user_id,
        name="Project A",
        client_name="A",
    )
    db.add(proj_a)
    db.commit()

    req_a1 = Requirement(project_id=proj_a.id, original_text="A1")
    req_a2 = Requirement(project_id=proj_a.id, original_text="A2")
    db.add(req_a1)
    db.add(req_a2)
    db.commit()

    proj_b = ProposalProject(
        organization_id=org_id,
        created_by_id=user_id,
        name="Project B",
        client_name="B",
    )
    db.add(proj_b)
    db.commit()

    # Try to merge A1 and A2 using Project B's endpoint
    response = client.post(
        f"/projects/{proj_b.id}/matrix/merge",
        data={"ids": [str(req_a1.id), str(req_a2.id)]},
        follow_redirects=False,
    )
    assert response.status_code == 404


def test_valid_same_project_evidence_linking(client, db):
    """Proves valid evidence linking within the same project/org works and is
    verified."""
    org_id, user_id = get_default_org_and_user(db)

    proj = ProposalProject(
        organization_id=org_id,
        created_by_id=user_id,
        name="Valid Project",
        client_name="Valid Client",
    )
    db.add(proj)
    db.commit()

    doc = Document(
        id=uuid.uuid4(),
        project_id=proj.id,
        name="Valid KB Doc",
        file_path="./data/valid.pdf",
        file_type="application/pdf",
        doc_role="knowledge_base",
        processing_status="completed",
        approval_status="APPROVED",
        created_by_id=user_id,
        content="Exact evidence snippet string.",
    )
    db.add(doc)

    page = DocumentPage(
        document_id=doc.id, page_number=1, content="Exact evidence snippet string."
    )
    db.add(page)
    db.commit()

    req = Requirement(
        project_id=proj.id,
        original_text="Valid requirement",
    )
    db.add(req)
    db.commit()

    # Valid link request
    link_payload = {
        "document_id": str(doc.id),
        "snippet": "Exact evidence snippet string.",
        "page_number": 1,
        "score": 0.8,
    }
    response = client.post(
        f"/requirements/{req.id}/evidence/link",
        data=link_payload,
        follow_redirects=False,
    )
    assert response.status_code == 303

    # Fabricated snippet rejected request
    bad_payload = {
        "document_id": str(doc.id),
        "snippet": "Fabricated fake text not in doc.",
        "page_number": 1,
        "score": 0.8,
    }
    bad_response = client.post(
        f"/requirements/{req.id}/evidence/link",
        data=bad_payload,
        follow_redirects=False,
    )
    assert bad_response.status_code == 400
    assert "snippet not found" in bad_response.text
