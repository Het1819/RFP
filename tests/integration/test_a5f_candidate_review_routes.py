"""Route-level tests for candidate review form endpoints (A5f Pass 2A).

The routes are thin, so these tests target what only the HTTP layer can prove:
CSRF enforcement, that the capability is required through the real request
path, that cross-tenant requests do not disclose existence, and that no
provenance field is mass-assignable from a form body.
"""

from __future__ import annotations

import hashlib
import uuid

import pytest
from sqlalchemy import select

from app.core.database import get_default_org_and_user
from app.models.document import Document, DocumentPage
from app.models.extraction import (
    CANDIDATE_STATUS_APPROVED,
    CANDIDATE_STATUS_PROPOSED,
    RequirementCandidate,
)
from app.models.organization import Organization
from app.models.project import ProposalProject
from app.models.requirement import Requirement
from app.models.user import User
from app.services.candidate_extraction import create_requirement_candidates
from app.services.ingestion_state import IngestionStatus
from app.services.requirement_extractor import FixtureRequirementExtractor

PAGE_TEXT = "The vendor MUST provide 99.9% uptime SLA for all core services."


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _seed_candidate_for_org(db, org_id, user_id, content=PAGE_TEXT):
    project = ProposalProject(
        organization_id=org_id, name="P", client_name="C", created_by_id=user_id
    )
    db.add(project)
    db.flush()

    doc = Document(
        project_id=project.id,
        created_by_id=user_id,
        name="rfp.pdf",
        display_filename="rfp.pdf",
        file_path=f"{uuid.uuid4()}.upload",
        file_type="application/pdf",
        file_size_bytes=1000,
        sha256_digest=_sha256("bytes"),
        ingestion_status=IngestionStatus.COMPLETED,
    )
    db.add(doc)
    db.flush()

    page = DocumentPage(
        document_id=doc.id,
        page_number=1,
        content=content,
        unit_kind="PDF_PAGE",
        source_locator="page_1",
        content_sha256=_sha256(content),
    )
    db.add(page)
    db.commit()

    run = create_requirement_candidates(
        db, doc.id, org_id, FixtureRequirementExtractor()
    )
    candidate = db.scalar(
        select(RequirementCandidate).where(
            RequirementCandidate.extraction_run_id == run.id
        )
    )
    return project, doc, page, run, candidate


@pytest.fixture
def reviewer_candidate(db):
    """Grant the logged-in dev user the capability and seed a candidate."""
    org_id, user_id = get_default_org_and_user(db)
    user = db.get(User, user_id)
    user.can_review_requirements = True
    db.commit()
    _project, _doc, _page, _run, candidate = _seed_candidate_for_org(
        db, org_id, user_id
    )
    return org_id, user, candidate


def _csrf(client):
    import re

    page = client.get("/login")
    match = re.search(r'name="csrf_token"\s+value="([a-f0-9]+)"', page.text)
    if not match:
        match = re.search(r'value="([a-f0-9]+)"\s+name="csrf_token"', page.text)
    assert match is not None
    return match.group(1)


# ---------------------------------------------------------------------------
# CSRF
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("action", ["approve", "reject"])
def test_review_route_requires_csrf(client, db, reviewer_candidate, action):
    _org_id, _user, candidate = reviewer_candidate

    response = client.post(
        f"/compliance/requirement-candidates/{candidate.id}/{action}",
        data={"csrf_token": "wrong-token"},
        headers={"X-Test-Enforce-CSRF": "true"},
    )
    assert response.status_code == 403

    db.refresh(candidate)
    assert candidate.candidate_status == CANDIDATE_STATUS_PROPOSED
    assert db.query(Requirement).count() == 0


def test_edit_route_requires_csrf(client, db, reviewer_candidate):
    _org_id, _user, candidate = reviewer_candidate

    response = client.post(
        f"/compliance/requirement-candidates/{candidate.id}/edit",
        data={"edited_text": "Reviewer text", "csrf_token": "wrong-token"},
        headers={"X-Test-Enforce-CSRF": "true"},
    )
    assert response.status_code == 403
    db.refresh(candidate)
    assert candidate.candidate_status == CANDIDATE_STATUS_PROPOSED


def test_review_route_succeeds_with_valid_csrf(client, db, reviewer_candidate):
    _org_id, _user, candidate = reviewer_candidate
    token = _csrf(client)

    response = client.post(
        f"/compliance/requirement-candidates/{candidate.id}/approve",
        data={"csrf_token": token},
        headers={"X-Test-Enforce-CSRF": "true"},
        follow_redirects=False,
    )
    assert response.status_code in (200, 303)

    db.refresh(candidate)
    assert candidate.candidate_status == CANDIDATE_STATUS_APPROVED
    assert db.query(Requirement).count() == 1


# ---------------------------------------------------------------------------
# Capability enforced through the real request path
# ---------------------------------------------------------------------------


def test_route_denies_user_without_capability(client, db):
    org_id, user_id = get_default_org_and_user(db)
    user = db.get(User, user_id)
    user.can_review_requirements = False
    db.commit()

    _project, _doc, _page, _run, candidate = _seed_candidate_for_org(
        db, org_id, user_id
    )

    response = client.post(
        f"/compliance/requirement-candidates/{candidate.id}/approve",
        data={},
    )
    assert response.status_code == 403

    db.refresh(candidate)
    assert candidate.candidate_status == CANDIDATE_STATUS_PROPOSED
    assert db.query(Requirement).count() == 0


def test_route_does_not_disclose_cross_tenant_candidate(client, db):
    """A capable reviewer must not learn that another tenant's candidate exists."""
    _org_id, user_id = get_default_org_and_user(db)
    user = db.get(User, user_id)
    user.can_review_requirements = True
    db.commit()

    other_org = Organization(name="OtherTenant")
    db.add(other_org)
    db.flush()
    other_user = User(
        organization_id=other_org.id,
        email=f"o{uuid.uuid4().hex[:8]}@test.com",
        hashed_password="x",
        full_name="Other",
    )
    db.add(other_user)
    db.commit()

    _project, _doc, _page, _run, foreign_candidate = _seed_candidate_for_org(
        db, other_org.id, other_user.id
    )

    real = client.post(
        f"/compliance/requirement-candidates/{foreign_candidate.id}/approve",
        data={},
    )
    missing = client.post(
        f"/compliance/requirement-candidates/{uuid.uuid4()}/approve",
        data={},
    )

    # Identical response for "exists in another tenant" and "does not exist".
    assert real.status_code == 404
    assert missing.status_code == 404
    assert real.json() == missing.json()

    db.refresh(foreign_candidate)
    assert foreign_candidate.candidate_status == CANDIDATE_STATUS_PROPOSED


# ---------------------------------------------------------------------------
# Mass assignment
# ---------------------------------------------------------------------------


def test_form_cannot_mass_assign_provenance_or_identity(client, db, reviewer_candidate):
    """Provenance and identity fields must not be bindable from the form body."""
    _org_id, user, candidate = reviewer_candidate

    attacker_org = Organization(name="AttackerOrg")
    db.add(attacker_org)
    db.commit()

    original = {
        "organization_id": candidate.organization_id,
        "project_id": candidate.project_id,
        "document_id": candidate.document_id,
        "document_page_id": candidate.document_page_id,
        "extraction_run_id": candidate.extraction_run_id,
        "evidence_text": candidate.evidence_text,
        "evidence_sha256": candidate.evidence_sha256,
        "page_content_sha256": candidate.page_content_sha256,
        "span_start": candidate.span_start,
        "span_end": candidate.span_end,
    }

    response = client.post(
        f"/compliance/requirement-candidates/{candidate.id}/approve",
        data={
            "candidate_status": "SUPERSEDED",
            "reviewed_by": str(uuid.uuid4()),
            "organization_id": str(attacker_org.id),
            "project_id": str(uuid.uuid4()),
            "document_id": str(uuid.uuid4()),
            "extraction_run_id": str(uuid.uuid4()),
            "source_candidate_id": str(uuid.uuid4()),
            "evidence_text": "forged evidence",
            "evidence_sha256": "0" * 64,
            "page_content_sha256": "0" * 64,
            "span_start": "0",
            "span_end": "1",
        },
        follow_redirects=False,
    )
    assert response.status_code in (200, 303)

    db.refresh(candidate)
    # The decision applied; nothing else the client sent had any effect.
    assert candidate.candidate_status == CANDIDATE_STATUS_APPROVED
    assert candidate.reviewed_by == user.id
    for field, value in original.items():
        assert getattr(candidate, field) == value, f"{field} was mass-assigned"

    requirement = db.scalar(
        select(Requirement).where(Requirement.source_candidate_id == candidate.id)
    )
    assert requirement is not None
    assert requirement.project_id == original["project_id"]


def test_replayed_route_approval_creates_one_requirement(
    client, db, reviewer_candidate
):
    _org_id, _user, candidate = reviewer_candidate

    first = client.post(
        f"/compliance/requirement-candidates/{candidate.id}/approve",
        data={},
        follow_redirects=False,
    )
    second = client.post(
        f"/compliance/requirement-candidates/{candidate.id}/approve",
        data={},
        follow_redirects=False,
    )

    assert first.status_code in (200, 303)
    assert second.status_code in (200, 303)
    assert db.query(Requirement).count() == 1
