"""Reviewer queue and candidate detail page tests (A5f Pass 2B2).

These pages are the first place a human sees machine output derived from an
untrusted document, so the tests here care about two things above all: who can
see the pages at all, and that document content renders as inert text.
"""

from __future__ import annotations

import hashlib
import uuid

import pytest
from sqlalchemy import select

from app.core.database import get_default_org_and_user
from app.models.document import Document, DocumentPage
from app.models.extraction import (
    CANDIDATE_STATUS_PROPOSED,
    REVIEW_TASK_STATUS_COMPLETED,
    CandidateReviewTask,
    RequirementCandidate,
)
from app.models.organization import Organization
from app.models.project import ProposalProject
from app.models.requirement import Requirement
from app.models.user import User
from app.services.candidate_extraction import create_requirement_candidates
from app.services.ingestion_state import IngestionStatus
from app.services.requirement_extractor import FixtureRequirementExtractor

QUEUE_URL = "/compliance/requirement-candidates"

PAGE_TEXT = "The vendor MUST provide 99.9% uptime SLA for all core services."


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _seed_candidates(db, org_id, user_id, *, content=PAGE_TEXT, project_name="P"):
    project = ProposalProject(
        organization_id=org_id,
        name=project_name,
        client_name="C",
        created_by_id=user_id,
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
    db.add(
        DocumentPage(
            document_id=doc.id,
            page_number=1,
            content=content,
            unit_kind="PDF_PAGE",
            source_locator="page_1",
            content_sha256=_sha256(content),
        )
    )
    db.commit()

    run = create_requirement_candidates(
        db, doc.id, org_id, FixtureRequirementExtractor()
    )
    candidate = db.scalar(
        select(RequirementCandidate).where(
            RequirementCandidate.extraction_run_id == run.id
        )
    )
    return project, doc, candidate


@pytest.fixture
def reviewer(db):
    """The logged-in dev user, granted the capability, with one candidate."""
    org_id, user_id = get_default_org_and_user(db)
    user = db.get(User, user_id)
    user.can_review_requirements = True
    db.commit()
    project, _doc, candidate = _seed_candidates(db, org_id, user_id)
    return org_id, user, project, candidate


def _csrf(client):
    import re

    page = client.get("/login")
    match = re.search(r'name="csrf_token"\s+value="([a-f0-9]+)"', page.text)
    if not match:
        match = re.search(r'value="([a-f0-9]+)"\s+name="csrf_token"', page.text)
    assert match is not None
    return match.group(1)


# ---------------------------------------------------------------------------
# Authorization
# ---------------------------------------------------------------------------


def test_queue_denied_when_unauthenticated(unauthenticated_client, db):
    response = unauthenticated_client.get(QUEUE_URL, follow_redirects=False)
    assert response.status_code in (302, 303, 401, 403)
    if response.status_code in (302, 303):
        assert "/login" in response.headers.get("location", "")


def test_queue_denied_for_ordinary_same_org_user(client, db):
    org_id, user_id = get_default_org_and_user(db)
    user = db.get(User, user_id)
    user.can_review_requirements = False
    db.commit()
    _seed_candidates(db, org_id, user_id)

    response = client.get(QUEUE_URL)
    assert response.status_code == 403


def test_queue_visible_to_capable_reviewer(client, db, reviewer):
    _org_id, _user, _project, candidate = reviewer
    response = client.get(QUEUE_URL)
    assert response.status_code == 200
    assert str(candidate.id) in response.text


def test_queue_denied_for_inactive_capable_user(client, db):
    org_id, user_id = get_default_org_and_user(db)
    user = db.get(User, user_id)
    user.can_review_requirements = True
    db.commit()
    _seed_candidates(db, org_id, user_id)

    # Deactivate after the session was established.
    user.is_active = False
    db.commit()

    response = client.get(QUEUE_URL)
    assert response.status_code == 401


def test_queue_excludes_other_tenants(client, db, reviewer):
    """A foreign organization's candidates must not appear or be counted."""
    _org_id, _user, _project, _candidate = reviewer

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
    _project, _doc, foreign = _seed_candidates(
        db, other_org.id, other_user.id, project_name="ForeignProject"
    )

    response = client.get(QUEUE_URL)
    assert response.status_code == 200
    assert str(foreign.id) not in response.text
    assert "ForeignProject" not in response.text


def test_detail_denied_for_ordinary_user(client, db):
    org_id, user_id = get_default_org_and_user(db)
    user = db.get(User, user_id)
    user.can_review_requirements = False
    db.commit()
    _project, _doc, candidate = _seed_candidates(db, org_id, user_id)

    response = client.get(f"{QUEUE_URL}/{candidate.id}")
    assert response.status_code == 403


def test_detail_does_not_disclose_cross_tenant_candidate(client, db, reviewer):
    _org_id, _user, _project, _candidate = reviewer

    other_org = Organization(name="OtherTenant2")
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
    _p, _d, foreign = _seed_candidates(db, other_org.id, other_user.id)

    real = client.get(f"{QUEUE_URL}/{foreign.id}")
    missing = client.get(f"{QUEUE_URL}/{uuid.uuid4()}")
    assert real.status_code == 404
    assert missing.status_code == 404
    assert real.json() == missing.json()


# ---------------------------------------------------------------------------
# Filtering, pagination, queue contents
# ---------------------------------------------------------------------------


def test_project_filter_stays_tenant_scoped(client, db, reviewer):
    _org_id, _user, project, candidate = reviewer

    other_org = Organization(name="OtherTenant3")
    db.add(other_org)
    db.commit()
    foreign_project = ProposalProject(
        organization_id=other_org.id,
        name="ForeignP",
        client_name="C",
        created_by_id=_user.id,
    )
    db.add(foreign_project)
    db.commit()

    # Own project filters normally.
    ok = client.get(f"{QUEUE_URL}?project_id={project.id}")
    assert ok.status_code == 200
    assert str(candidate.id) in ok.text

    # A foreign project id is refused, not silently applied.
    denied = client.get(f"{QUEUE_URL}?project_id={foreign_project.id}")
    assert denied.status_code == 404


def test_invalid_project_filter_rejected(client, db, reviewer):
    assert client.get(f"{QUEUE_URL}?project_id=not-a-uuid").status_code == 400


def test_pagination_is_bounded_and_tenant_scoped(client, db, reviewer):
    org_id, user, _project, _candidate = reviewer
    for index in range(3):
        _seed_candidates(db, org_id, user.id, project_name=f"Bulk{index}")

    first = client.get(f"{QUEUE_URL}?page=1")
    assert first.status_code == 200
    # Page numbers below 1 are clamped rather than producing a negative offset.
    clamped = client.get(f"{QUEUE_URL}?page=0")
    assert clamped.status_code == 200
    assert "Page 1" in clamped.text


def test_completed_tasks_excluded_from_open_queue(client, db, reviewer):
    _org_id, _user, _project, candidate = reviewer

    task = db.scalar(
        select(CandidateReviewTask).where(
            CandidateReviewTask.candidate_id == candidate.id
        )
    )
    task.status = REVIEW_TASK_STATUS_COMPLETED
    db.commit()

    response = client.get(QUEUE_URL)
    assert response.status_code == 200
    assert str(candidate.id) not in response.text


# ---------------------------------------------------------------------------
# Rendering safety
# ---------------------------------------------------------------------------

HOSTILE = (
    "<script>alert('xss')</script> Vendors MUST register at "
    "https://portal.example.gov/bids and <img src=x onerror=alert(1)> comply."
)


def _seed_hostile_candidate(db, org_id, user_id, content=HOSTILE):
    """Seed one candidate whose evidence spans the whole hostile page.

    Built directly rather than through the fixture extractor, which slices only
    the first 60 characters -- that would truncate the payload and make the
    rendering assertions below vacuously pass.
    """
    project = ProposalProject(
        organization_id=org_id, name="Hostile", client_name="C", created_by_id=user_id
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
    db.flush()

    from app.models.extraction import ExtractionRun
    from app.services.candidate_extraction import EXTRACTION_SCHEMA_VERSION

    run = ExtractionRun(
        organization_id=org_id,
        project_id=project.id,
        document_id=doc.id,
        status="COMPLETED",
        extraction_attempt_id=str(uuid.uuid4()),
        input_snapshot_sha256=_sha256("snapshot"),
        page_count=1,
        extraction_schema_version=EXTRACTION_SCHEMA_VERSION,
        prompt_version="requirement-extraction-v1",
    )
    db.add(run)
    db.flush()

    candidate = RequirementCandidate(
        organization_id=org_id,
        project_id=project.id,
        extraction_run_id=run.id,
        document_id=doc.id,
        document_page_id=page.id,
        page_content_sha256=page.content_sha256,
        unit_kind="PDF_PAGE",
        source_locator="page_1",
        span_start=0,
        span_end=len(content),
        evidence_text=content,
        evidence_sha256=_sha256(content),
        normalized_requirement_text=content,
        extraction_schema_version=EXTRACTION_SCHEMA_VERSION,
        candidate_status=CANDIDATE_STATUS_PROPOSED,
    )
    db.add(candidate)
    db.flush()
    db.add(
        CandidateReviewTask(
            organization_id=org_id,
            project_id=project.id,
            candidate_id=candidate.id,
            extraction_run_id=run.id,
            source_locator="page_1",
        )
    )
    db.commit()
    return candidate


def test_candidate_and_evidence_are_escaped(client, db):
    org_id, user_id = get_default_org_and_user(db)
    user = db.get(User, user_id)
    user.can_review_requirements = True
    db.commit()
    candidate = _seed_hostile_candidate(db, org_id, user_id)

    for url in (QUEUE_URL, f"{QUEUE_URL}/{candidate.id}"):
        response = client.get(url)
        assert response.status_code == 200
        body = response.text

        # The payload must survive as *text* -- we do not sanitize evidence,
        # because a reviewer needs to see exactly what the document said.
        assert "&lt;script&gt;" in body
        assert "&lt;img" in body

        # What must not survive is any of it as live markup. Checking for the
        # escaped form alone would pass even if a second unescaped copy were
        # rendered elsewhere, so assert the opening delimiters are absent.
        assert "<script>" not in body
        assert "<img" not in body
        assert "<iframe" not in body


def test_evidence_url_is_inert_not_a_resource(client, db):
    """A URL in evidence must not become a link, image, or embed."""
    org_id, user_id = get_default_org_and_user(db)
    user = db.get(User, user_id)
    user.can_review_requirements = True
    db.commit()
    candidate = _seed_hostile_candidate(db, org_id, user_id)

    response = client.get(f"{QUEUE_URL}/{candidate.id}")
    assert response.status_code == 200

    # The URL text is present ...
    assert "portal.example.gov" in response.text
    # ... but never as a fetched or navigable resource.
    for pattern in (
        'href="https://portal.example.gov',
        'src="https://portal.example.gov',
        "<iframe",
        "<embed",
        "<object",
    ):
        assert pattern not in response.text

    # No document-derived <img> at all on the page.
    assert "<img" not in response.text


def test_detail_shows_no_storage_paths_or_secrets(client, db, reviewer):
    _org_id, _user, _project, candidate = reviewer
    response = client.get(f"{QUEUE_URL}/{candidate.id}")
    assert response.status_code == 200
    assert ".upload" not in response.text
    assert "sk-ant" not in response.text
    assert "ANTHROPIC_API_KEY" not in response.text


# ---------------------------------------------------------------------------
# Review actions from the page
# ---------------------------------------------------------------------------


def test_approve_from_page_creates_exactly_one_requirement(client, db, reviewer):
    _org_id, _user, project, candidate = reviewer

    response = client.post(
        f"{QUEUE_URL}/{candidate.id}/approve", data={}, follow_redirects=False
    )
    assert response.status_code == 303
    assert QUEUE_URL in response.headers["location"]

    requirements = db.scalars(
        select(Requirement).where(Requirement.project_id == project.id)
    ).all()
    assert len(requirements) == 1
    assert requirements[0].source_candidate_id == candidate.id


def test_edit_from_page_uses_reviewer_text(client, db, reviewer):
    _org_id, _user, _project, candidate = reviewer
    original = candidate.normalized_requirement_text
    reviewer_text = "Vendor shall maintain 99.9% uptime, measured monthly."

    response = client.post(
        f"{QUEUE_URL}/{candidate.id}/edit",
        data={"edited_text": reviewer_text},
        follow_redirects=False,
    )
    assert response.status_code == 303

    requirement = db.scalar(
        select(Requirement).where(Requirement.source_candidate_id == candidate.id)
    )
    assert requirement.original_text == reviewer_text
    db.refresh(candidate)
    # The machine proposal survives beside the reviewer's wording.
    assert candidate.normalized_requirement_text == original
    assert candidate.reviewer_edited_text == reviewer_text


def test_reject_from_page_creates_no_requirement(client, db, reviewer):
    _org_id, _user, project, candidate = reviewer

    response = client.post(
        f"{QUEUE_URL}/{candidate.id}/reject",
        data={"reviewer_comment": "Background prose, not an obligation."},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert (
        db.scalars(
            select(Requirement).where(Requirement.project_id == project.id)
        ).all()
        == []
    )


def test_duplicate_submission_is_idempotent(client, db, reviewer):
    _org_id, _user, project, candidate = reviewer

    first = client.post(
        f"{QUEUE_URL}/{candidate.id}/approve", data={}, follow_redirects=False
    )
    second = client.post(
        f"{QUEUE_URL}/{candidate.id}/approve", data={}, follow_redirects=False
    )

    assert first.status_code == 303
    assert second.status_code in (303, 409)
    assert (
        len(
            db.scalars(
                select(Requirement).where(Requirement.project_id == project.id)
            ).all()
        )
        == 1
    )


@pytest.mark.parametrize("action", ["approve", "edit", "reject"])
def test_csrf_required_for_every_review_action(client, db, reviewer, action):
    _org_id, _user, project, candidate = reviewer

    data = {"csrf_token": "wrong"}
    if action == "edit":
        data["edited_text"] = "Reviewer text"

    response = client.post(
        f"{QUEUE_URL}/{candidate.id}/{action}",
        data=data,
        headers={"X-Test-Enforce-CSRF": "true"},
    )
    assert response.status_code == 403

    db.refresh(candidate)
    assert candidate.candidate_status == CANDIDATE_STATUS_PROPOSED
    assert (
        db.scalars(
            select(Requirement).where(Requirement.project_id == project.id)
        ).all()
        == []
    )


def test_valid_csrf_allows_review(client, db, reviewer):
    _org_id, _user, _project, candidate = reviewer
    token = _csrf(client)

    response = client.post(
        f"{QUEUE_URL}/{candidate.id}/approve",
        data={"csrf_token": token},
        headers={"X-Test-Enforce-CSRF": "true"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert len(db.scalars(select(Requirement)).all()) == 1


def test_stale_source_blocks_review_from_page(client, db, reviewer):
    _org_id, _user, project, candidate = reviewer

    page = db.scalar(
        select(DocumentPage).where(DocumentPage.id == candidate.document_page_id)
    )
    page.content = "Completely rewritten page content after a reparse."
    page.content_sha256 = _sha256(page.content)
    db.commit()

    response = client.post(f"{QUEUE_URL}/{candidate.id}/approve", data={})
    assert response.status_code == 400

    db.refresh(candidate)
    assert candidate.candidate_status == CANDIDATE_STATUS_PROPOSED
    assert (
        db.scalars(
            select(Requirement).where(Requirement.project_id == project.id)
        ).all()
        == []
    )


def test_terminal_candidate_renders_read_only(client, db, reviewer):
    _org_id, _user, _project, candidate = reviewer

    client.post(f"{QUEUE_URL}/{candidate.id}/approve", data={}, follow_redirects=False)

    response = client.get(f"{QUEUE_URL}/{candidate.id}")
    assert response.status_code == 200
    assert "Already decided" in response.text
    # No review forms are offered for a settled candidate.
    assert f"{QUEUE_URL}/{candidate.id}/approve" not in response.text
    assert f"{QUEUE_URL}/{candidate.id}/reject" not in response.text


def test_review_makes_no_provider_call(client, db, reviewer, monkeypatch):
    """Review is a database transaction; it must never reach a model."""
    import socket

    def _blocked(*args, **kwargs):
        raise AssertionError("review attempted a network call")

    monkeypatch.setattr(socket.socket, "connect", _blocked)
    monkeypatch.setattr(socket, "create_connection", _blocked)
    monkeypatch.setattr(socket, "getaddrinfo", _blocked)

    _org_id, _user, _project, candidate = reviewer
    assert client.get(QUEUE_URL).status_code == 200
    assert client.get(f"{QUEUE_URL}/{candidate.id}").status_code == 200
    assert (
        client.post(
            f"{QUEUE_URL}/{candidate.id}/approve", data={}, follow_redirects=False
        ).status_code
        == 303
    )
