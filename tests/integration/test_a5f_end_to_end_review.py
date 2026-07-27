"""End-to-end human review pipeline with the fixture provider (A5f Pass 2B2).

Walks the whole path a real reviewer walks:

    COMPLETED document
      -> extraction job
      -> COMPLETED ExtractionRun
      -> PROPOSED RequirementCandidates + CandidateReviewTasks
      -> ordinary user is refused the queue
      -> operator CLI grants the capability
      -> reviewer opens queue and detail
      -> approve / edit / reject
      -> at most one Requirement per approved or edited candidate

The fixture provider is used throughout. No Anthropic request, no network, no
indexing, embedding, retrieval, or proposal generation occurs -- asserted at
the socket layer.
"""

from __future__ import annotations

import hashlib
import socket
import uuid

import pytest
from sqlalchemy import select

from app.cli.requirement_reviewer import (
    AUDIT_CAPABILITY_GRANTED,
    RESULT_GRANTED,
    set_capability,
)
from app.core.config import settings
from app.core.database import get_default_org_and_user
from app.models.audit import AuditEvent
from app.models.document import Document, DocumentPage
from app.models.extraction import (
    AUDIT_CANDIDATE_APPROVED,
    AUDIT_CANDIDATE_EDITED,
    AUDIT_CANDIDATE_REJECTED,
    CANDIDATE_STATUS_APPROVED,
    CANDIDATE_STATUS_EDITED,
    CANDIDATE_STATUS_PROPOSED,
    CANDIDATE_STATUS_REJECTED,
    EXTRACTION_STATUS_COMPLETED,
    REVIEW_TASK_STATUS_COMPLETED,
    REVIEW_TASK_STATUS_OPEN,
    CandidateReviewTask,
    ExtractionRun,
    RequirementCandidate,
)
from app.models.requirement import Requirement
from app.models.user import User
from app.services.ingestion_state import IngestionStatus
from app.services.requirement_extractor import build_requirement_extractor

QUEUE_URL = "/compliance/requirement-candidates"

# Three synthetic pages so there is one candidate for each decision.
PAGES = [
    "The vendor MUST provide 99.9% monthly uptime for all core services.",
    "The supplier MUST retain audit records for a period of seven years.",
    "The agency has operated this programme since 1994 across twelve offices.",
]


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@pytest.fixture
def fixture_provider(monkeypatch):
    """Fixture provider, explicitly allowed only in a test environment."""
    monkeypatch.setattr(settings, "REQUIREMENT_EXTRACTOR_PROVIDER", "fixture")
    monkeypatch.setattr(settings, "APP_ENV", "test")
    yield
    # Restored to disabled after validation, as the surrounding pass requires.
    monkeypatch.setattr(settings, "REQUIREMENT_EXTRACTOR_PROVIDER", "disabled")


_LOOPBACK = {"127.0.0.1", "::1", "localhost", ""}


@pytest.fixture
def no_network(monkeypatch):
    """Fail on any egress to a non-loopback destination.

    Loopback is deliberately allowed: on Windows `asyncio.run()` builds its
    event-loop self-pipe from a loopback socketpair, so a blanket block would
    fail on the event loop rather than on anything this pipeline did. What
    matters is that nothing reaches an external host.
    """
    real_connect = socket.socket.connect
    real_create = socket.create_connection
    real_getaddrinfo = socket.getaddrinfo

    def _host_of(address: object) -> str:
        if isinstance(address, tuple) and address:
            return str(address[0])
        return ""

    def _guard_connect(self, address, *args, **kwargs):
        host = _host_of(address)
        if host not in _LOOPBACK:
            raise AssertionError(f"egress to non-loopback host attempted: {host}")
        return real_connect(self, address, *args, **kwargs)

    def _guard_create(address, *args, **kwargs):
        host = _host_of(address)
        if host not in _LOOPBACK:
            raise AssertionError(f"egress to non-loopback host attempted: {host}")
        return real_create(address, *args, **kwargs)

    def _guard_getaddrinfo(host, port, *args, **kwargs):
        if str(host) not in _LOOPBACK:
            raise AssertionError(f"DNS resolution attempted for: {host}")
        return real_getaddrinfo(host, port, *args, **kwargs)

    monkeypatch.setattr(socket.socket, "connect", _guard_connect)
    monkeypatch.setattr(socket, "create_connection", _guard_create)
    monkeypatch.setattr(socket, "getaddrinfo", _guard_getaddrinfo)


def _seed_completed_document(db, org_id, user_id):
    from app.models.project import ProposalProject

    project = ProposalProject(
        organization_id=org_id,
        name="E2E Project",
        client_name="Synthetic Client",
        created_by_id=user_id,
    )
    db.add(project)
    db.flush()

    doc = Document(
        project_id=project.id,
        created_by_id=user_id,
        name="synthetic.pdf",
        display_filename="synthetic.pdf",
        file_path=f"{uuid.uuid4()}.upload",
        file_type="application/pdf",
        file_size_bytes=2048,
        sha256_digest=_sha256("synthetic-bytes"),
        ingestion_status=IngestionStatus.COMPLETED,
    )
    db.add(doc)
    db.flush()

    for index, content in enumerate(PAGES, start=1):
        db.add(
            DocumentPage(
                document_id=doc.id,
                page_number=index,
                content=content,
                unit_kind="PDF_PAGE",
                source_locator=f"page_{index}",
                content_sha256=_sha256(content),
            )
        )
    db.commit()
    return project, doc


def test_end_to_end_human_review_pipeline(client, db, fixture_provider, no_network):
    org_id, user_id = get_default_org_and_user(db)
    user = db.get(User, user_id)

    # --- 1. Ordinary organization user, no reviewer capability ------------
    user.can_review_requirements = False
    db.commit()
    assert user.can_review_requirements is False

    # --- 2. One synthetic clean document, already COMPLETED ---------------
    project, doc = _seed_completed_document(db, org_id, user_id)
    assert doc.ingestion_status == IngestionStatus.COMPLETED

    # --- 3. Extraction through the worker entry point ---------------------
    import asyncio

    from app import worker

    asyncio.run(worker.extract_requirements_task(None, str(doc.id), str(org_id)))

    runs = db.scalars(
        select(ExtractionRun).where(ExtractionRun.document_id == doc.id)
    ).all()
    assert len(runs) == 1
    run = runs[0]
    assert run.status == EXTRACTION_STATUS_COMPLETED
    assert run.provider == "fixture"

    candidates = db.scalars(
        select(RequirementCandidate)
        .where(RequirementCandidate.extraction_run_id == run.id)
        .order_by(RequirementCandidate.source_locator.asc())
    ).all()
    tasks = db.scalars(
        select(CandidateReviewTask).where(
            CandidateReviewTask.extraction_run_id == run.id
        )
    ).all()

    assert len(candidates) == 3
    assert len(tasks) == len(candidates)
    assert all(c.candidate_status == CANDIDATE_STATUS_PROPOSED for c in candidates)
    assert all(t.status == REVIEW_TASK_STATUS_OPEN for t in tasks)

    # Nothing is authoritative yet.
    assert (
        db.scalars(
            select(Requirement).where(Requirement.project_id == project.id)
        ).all()
        == []
    )

    # --- 4. The ordinary user cannot reach the queue ----------------------
    assert client.get(QUEUE_URL).status_code == 403
    assert client.get(f"{QUEUE_URL}/{candidates[0].id}").status_code == 403

    # --- 5. Operator CLI grants the capability ----------------------------
    granted_user, result = set_capability(
        db,
        email=user.email,
        organization_id=org_id,
        grant=True,
        reason="E2E validation of the review pipeline",
        confirm=True,
    )
    assert result == RESULT_GRANTED
    assert granted_user.id == user.id
    assert (
        len(
            db.scalars(
                select(AuditEvent).where(AuditEvent.action == AUDIT_CAPABILITY_GRANTED)
            ).all()
        )
        == 1
    )

    # --- 6/7. The reviewer can now open the queue and a detail page -------
    queue = client.get(QUEUE_URL)
    assert queue.status_code == 200
    for candidate in candidates:
        assert str(candidate.id) in queue.text

    detail = client.get(f"{QUEUE_URL}/{candidates[0].id}")
    assert detail.status_code == 200
    assert "Source evidence" in detail.text

    approve_target, edit_target, reject_target = candidates
    original_edit_text = edit_target.normalized_requirement_text
    approve_evidence = approve_target.evidence_text

    # --- 8. Approve -------------------------------------------------------
    response = client.post(
        f"{QUEUE_URL}/{approve_target.id}/approve", data={}, follow_redirects=False
    )
    assert response.status_code == 303

    # --- 9. Edit and approve ---------------------------------------------
    reviewer_text = "Supplier shall retain audit records for seven years."
    response = client.post(
        f"{QUEUE_URL}/{edit_target.id}/edit",
        data={"edited_text": reviewer_text},
        follow_redirects=False,
    )
    assert response.status_code == 303

    # --- 10. Reject -------------------------------------------------------
    response = client.post(
        f"{QUEUE_URL}/{reject_target.id}/reject",
        data={"reviewer_comment": "Background prose, imposes no obligation."},
        follow_redirects=False,
    )
    assert response.status_code == 303

    # --- Verification -----------------------------------------------------
    for candidate in candidates:
        db.refresh(candidate)

    assert approve_target.candidate_status == CANDIDATE_STATUS_APPROVED
    assert edit_target.candidate_status == CANDIDATE_STATUS_EDITED
    assert reject_target.candidate_status == CANDIDATE_STATUS_REJECTED

    requirements = db.scalars(
        select(Requirement).where(Requirement.project_id == project.id)
    ).all()
    # Exactly one per approved/edited candidate; none for the rejected one.
    assert len(requirements) == 2
    by_candidate = {r.source_candidate_id: r for r in requirements}
    assert set(by_candidate) == {approve_target.id, edit_target.id}
    assert by_candidate[edit_target.id].original_text == reviewer_text
    assert (
        db.scalar(
            select(Requirement).where(
                Requirement.source_candidate_id == reject_target.id
            )
        )
        is None
    )

    # Evidence is immutable: review never rewrites what the document said.
    assert approve_target.evidence_text == approve_evidence
    assert edit_target.normalized_requirement_text == original_edit_text
    assert edit_target.reviewer_edited_text == reviewer_text

    # Review tasks completed atomically alongside their decisions.
    for candidate in candidates:
        task = db.scalar(
            select(CandidateReviewTask).where(
                CandidateReviewTask.candidate_id == candidate.id
            )
        )
        assert task.status == REVIEW_TASK_STATUS_COMPLETED
        assert task.resolved_at is not None

    # Audit evidence for each decision.
    for action in (
        AUDIT_CANDIDATE_APPROVED,
        AUDIT_CANDIDATE_EDITED,
        AUDIT_CANDIDATE_REJECTED,
    ):
        events = db.scalars(select(AuditEvent).where(AuditEvent.action == action)).all()
        assert len(events) == 1, action

    # The decided candidates have left the open queue.
    final_queue = client.get(QUEUE_URL)
    assert final_queue.status_code == 200
    for candidate in candidates:
        assert str(candidate.id) not in final_queue.text


def test_no_candidate_is_automatically_approved(
    client, db, fixture_provider, no_network
):
    """Extraction alone must never produce an approval or a Requirement."""
    import asyncio

    from app import worker

    org_id, user_id = get_default_org_and_user(db)
    project, doc = _seed_completed_document(db, org_id, user_id)

    asyncio.run(worker.extract_requirements_task(None, str(doc.id), str(org_id)))

    candidates = db.scalars(select(RequirementCandidate)).all()
    assert candidates
    assert all(c.candidate_status == CANDIDATE_STATUS_PROPOSED for c in candidates)
    assert all(c.reviewed_by is None for c in candidates)
    assert all(c.reviewed_at is None for c in candidates)
    assert (
        db.scalars(
            select(Requirement).where(Requirement.project_id == project.id)
        ).all()
        == []
    )


def test_pipeline_uses_fixture_provider_not_anthropic(db, fixture_provider):
    """The configured provider is what runs -- never a live model."""
    extractor = build_requirement_extractor()
    assert extractor.provider_name == "fixture"
    assert extractor.model_name is None


def test_provider_restored_to_disabled_after_validation(db):
    """Outside the fixture fixture, the default remains disabled."""
    from app.core.config import Settings

    assert Settings(APP_ENV="test", AUTH_MODE="dev").REQUIREMENT_EXTRACTOR_PROVIDER == (
        "disabled"
    )
