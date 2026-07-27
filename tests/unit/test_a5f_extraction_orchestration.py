"""Queue, worker, and input-budget tests for extraction (A5f Pass 2B1)."""

from __future__ import annotations

import asyncio
import hashlib
import uuid

import pytest
from sqlalchemy import select

from app.core.config import settings
from app.models.audit import AuditEvent
from app.models.document import Document, DocumentPage
from app.models.extraction import (
    AUDIT_EXTRACTION_COMPLETED,
    AUDIT_EXTRACTION_FAILED,
    AUDIT_EXTRACTION_INPUT_LIMIT,
    AUDIT_EXTRACTION_STARTED,
    EXTRACTION_STATUS_COMPLETED,
    CandidateReviewTask,
    ExtractionRun,
    RequirementCandidate,
)
from app.models.organization import Organization
from app.models.project import ProposalProject
from app.models.requirement import Requirement
from app.models.user import User
from app.services.candidate_extraction import (
    ExtractionServiceError,
    create_requirement_candidates,
)
from app.services.ingestion_state import IngestionStatus
from app.services.requirement_extractor import FixtureRequirementExtractor

PAGE = "The vendor MUST provide 99.9% uptime SLA for all core services."


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _seed(db, *, pages: list[str] | None = None):
    org = Organization(name=f"Org-{uuid.uuid4().hex[:6]}")
    db.add(org)
    db.flush()
    user = User(
        organization_id=org.id,
        email=f"u{uuid.uuid4().hex[:8]}@test.com",
        hashed_password="x",
        full_name="U",
    )
    db.add(user)
    db.flush()
    project = ProposalProject(
        organization_id=org.id, name="P", client_name="C", created_by_id=user.id
    )
    db.add(project)
    db.flush()
    doc = Document(
        project_id=project.id,
        created_by_id=user.id,
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
    for index, content in enumerate(pages or [PAGE], start=1):
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
    return org, project, doc


def _audits(db, action):
    return list(db.scalars(select(AuditEvent).where(AuditEvent.action == action)))


# ---------------------------------------------------------------------------
# Input budget
# ---------------------------------------------------------------------------


def test_too_many_source_units_fails_closed(db, monkeypatch):
    monkeypatch.setattr(settings, "REQUIREMENT_EXTRACTION_MAX_SOURCE_UNITS", 2)
    org, _project, doc = _seed(db, pages=[PAGE, PAGE + " A", PAGE + " B"])

    with pytest.raises(ExtractionServiceError) as exc_info:
        create_requirement_candidates(db, doc.id, org.id, FixtureRequirementExtractor())
    assert exc_info.value.code == "EXTRACTION_INPUT_LIMIT"

    # Fails closed: no run, no candidates, and the document is retained.
    assert db.scalars(select(ExtractionRun)).all() == []
    assert db.scalars(select(RequirementCandidate)).all() == []
    db.refresh(doc)
    assert doc.ingestion_status == IngestionStatus.COMPLETED
    assert len(db.scalars(select(DocumentPage)).all()) == 3


def test_too_many_input_characters_fails_closed(db, monkeypatch):
    monkeypatch.setattr(settings, "REQUIREMENT_EXTRACTION_MAX_INPUT_CHARS", 10)
    org, _project, doc = _seed(db)

    with pytest.raises(ExtractionServiceError) as exc_info:
        create_requirement_candidates(db, doc.id, org.id, FixtureRequirementExtractor())
    assert exc_info.value.code == "EXTRACTION_INPUT_LIMIT"
    assert db.scalars(select(ExtractionRun)).all() == []


def test_input_limit_is_audited_with_operator_detail(db, monkeypatch):
    monkeypatch.setattr(settings, "REQUIREMENT_EXTRACTION_MAX_SOURCE_UNITS", 1)
    org, _project, doc = _seed(db, pages=[PAGE, PAGE + " second"])

    with pytest.raises(ExtractionServiceError):
        create_requirement_candidates(db, doc.id, org.id, FixtureRequirementExtractor())

    events = _audits(db, AUDIT_EXTRACTION_INPUT_LIMIT)
    assert len(events) == 1
    details = events[0].details
    assert details["result_code"] == "EXTRACTION_INPUT_LIMIT"
    assert details["page_count"] == 2
    assert details["max_source_units"] == 1
    # Operator-visible remediation numbers only -- never document text.
    assert PAGE not in str(details)


def test_document_within_budget_is_not_truncated(db, monkeypatch):
    monkeypatch.setattr(settings, "REQUIREMENT_EXTRACTION_MAX_SOURCE_UNITS", 3)
    org, _project, doc = _seed(db, pages=[PAGE, PAGE + " two", PAGE + " three"])

    run = create_requirement_candidates(
        db, doc.id, org.id, FixtureRequirementExtractor()
    )
    assert run.page_count == 3
    assert run.status == EXTRACTION_STATUS_COMPLETED


# ---------------------------------------------------------------------------
# Usage + audit
# ---------------------------------------------------------------------------


class _UsageExtractor(FixtureRequirementExtractor):
    """Fixture extractor reporting provider usage like the real adapter."""

    def __init__(self) -> None:
        from app.services.anthropic_extractor import ProviderUsage

        self.usage = ProviderUsage(
            provider_call_count=1,
            input_tokens=2500,
            output_tokens=400,
            cache_creation_input_tokens=1800,
            cache_read_input_tokens=700,
            duration_ms=1234,
        )


def test_usage_counters_persisted_on_run(db):
    org, _project, doc = _seed(db)
    run = create_requirement_candidates(db, doc.id, org.id, _UsageExtractor())

    assert run.provider_call_count == 1
    assert run.input_tokens == 2500
    assert run.output_tokens == 400
    assert run.cache_creation_input_tokens == 1800
    assert run.cache_read_input_tokens == 700
    assert run.duration_ms == 1234
    assert run.prompt_version == "requirement-extraction-v1"


def test_started_and_completed_events_emitted(db):
    org, _project, doc = _seed(db)
    create_requirement_candidates(db, doc.id, org.id, FixtureRequirementExtractor())

    assert len(_audits(db, AUDIT_EXTRACTION_STARTED)) == 1
    completed = _audits(db, AUDIT_EXTRACTION_COMPLETED)
    assert len(completed) == 1
    assert completed[0].details["result_code"] == "EXTRACTION_OK"
    assert PAGE not in str(completed[0].details)


def test_failed_event_emitted_with_fixed_code(db):
    from app.services.requirement_extractor import (
        DisabledRequirementExtractor,
    )

    org, _project, doc = _seed(db)
    with pytest.raises(ExtractionServiceError):
        create_requirement_candidates(
            db, doc.id, org.id, DisabledRequirementExtractor()
        )

    events = _audits(db, AUDIT_EXTRACTION_FAILED)
    assert len(events) == 1
    assert events[0].details["result_code"] == "EXTRACTOR_NOT_CONFIGURED"


# ---------------------------------------------------------------------------
# Queue trigger
# ---------------------------------------------------------------------------


def test_enqueue_skipped_when_queue_disabled(db, monkeypatch):
    """Extraction must never run inline -- a provider call is not a route."""
    from app.core.queue import enqueue_extraction_job

    monkeypatch.setattr(settings, "QUEUE_ENABLED", False)
    called: list[str] = []
    monkeypatch.setattr(
        "app.core.queue._enqueue_extraction_to_redis",
        lambda *a, **k: called.append("enqueued"),
    )
    enqueue_extraction_job(uuid.uuid4(), uuid.uuid4())
    assert called == []


def test_enqueue_payload_carries_only_identifiers(monkeypatch):
    monkeypatch.setattr(settings, "QUEUE_ENABLED", True)
    captured: dict = {}

    class _Redis:
        async def enqueue_job(self, name, *args, **kwargs):
            captured["name"] = name
            captured["args"] = args
            captured["kwargs"] = kwargs

        async def close(self):
            return None

    async def _fake_pool(*_a, **_k):
        return _Redis()

    monkeypatch.setattr("arq.create_pool", _fake_pool)

    from app.core.queue import _enqueue_extraction_to_redis

    document_id = uuid.uuid4()
    organization_id = uuid.uuid4()
    asyncio.run(_enqueue_extraction_to_redis(document_id, organization_id))

    assert captured["name"] == "extract_requirements_task"
    assert captured["args"] == (str(document_id), str(organization_id))
    # Two identifiers, nothing else: no text, path, credential, or model.
    assert len(captured["args"]) == 2


def test_enqueue_failure_leaves_document_completed_and_audits(db, monkeypatch):
    from app.core.queue import _handle_extraction_enqueue_failure

    org, _project, doc = _seed(db)
    _handle_extraction_enqueue_failure(doc.id, org.id, RuntimeError("redis down"))

    db.refresh(doc)
    assert doc.ingestion_status == IngestionStatus.COMPLETED
    events = _audits(db, AUDIT_EXTRACTION_FAILED)
    assert len(events) == 1
    assert events[0].details["result_code"] == "EXTRACTION_QUEUE_FAILED"


def test_reenqueue_after_queue_failure_is_idempotent(db):
    """Reconciliation must not double-extract an unchanged document."""
    org, _project, doc = _seed(db)
    create_requirement_candidates(db, doc.id, org.id, FixtureRequirementExtractor())

    with pytest.raises(ExtractionServiceError) as exc_info:
        create_requirement_candidates(db, doc.id, org.id, FixtureRequirementExtractor())
    assert exc_info.value.code == "DUPLICATE_COMPLETED_RUN"
    assert len(db.scalars(select(ExtractionRun)).all()) == 1


def test_parse_completion_enqueues_after_commit(monkeypatch):
    """The enqueue call site must sit after the COMPLETED commit."""
    import inspect

    from app.services import document_parsing

    source = inspect.getsource(document_parsing.persist_parse_results)
    commit_at = source.index("db.commit()")
    enqueue_at = source.index("enqueue_extraction_job(")
    assert commit_at < enqueue_at, "extraction enqueued before pages were committed"


# ---------------------------------------------------------------------------
# Worker
# ---------------------------------------------------------------------------


def test_worker_task_registered():
    from app.worker import WorkerSettings, extract_requirements_task

    assert extract_requirements_task in WorkerSettings.functions


def test_worker_uses_configured_provider_not_payload(db, monkeypatch):
    from app import worker

    monkeypatch.setattr(settings, "REQUIREMENT_EXTRACTOR_PROVIDER", "fixture")
    monkeypatch.setattr(settings, "APP_ENV", "test")
    org, _project, doc = _seed(db)

    asyncio.run(worker.extract_requirements_task(None, str(doc.id), str(org.id)))

    runs = db.scalars(select(ExtractionRun)).all()
    assert len(runs) == 1
    assert runs[0].provider == "fixture"


def test_worker_creates_no_requirement_and_leaves_candidates_proposed(db, monkeypatch):
    from app import worker

    monkeypatch.setattr(settings, "REQUIREMENT_EXTRACTOR_PROVIDER", "fixture")
    monkeypatch.setattr(settings, "APP_ENV", "test")
    org, project, doc = _seed(db)

    asyncio.run(worker.extract_requirements_task(None, str(doc.id), str(org.id)))

    candidates = db.scalars(select(RequirementCandidate)).all()
    tasks = db.scalars(select(CandidateReviewTask)).all()
    assert len(candidates) >= 1
    assert len(tasks) == len(candidates)
    assert all(c.candidate_status == "PROPOSED" for c in candidates)
    # The whole point: no authoritative Requirement without a human.
    assert (
        db.scalars(
            select(Requirement).where(Requirement.project_id == project.id)
        ).all()
        == []
    )


def test_worker_does_not_retry_deterministic_failure(db, monkeypatch):
    """A terminal ExtractionServiceError must not resurface as a crash loop."""
    from app import worker

    monkeypatch.setattr(settings, "REQUIREMENT_EXTRACTOR_PROVIDER", "disabled")
    org, _project, doc = _seed(db)

    # Swallowed, not raised: arq would otherwise retry a permanent failure.
    asyncio.run(worker.extract_requirements_task(None, str(doc.id), str(org.id)))

    runs = db.scalars(select(ExtractionRun)).all()
    assert len(runs) == 1
    assert runs[0].status == "FAILED"
    assert runs[0].failure_code == "EXTRACTOR_NOT_CONFIGURED"


def test_worker_is_tenant_scoped(db, monkeypatch):
    from app import worker

    monkeypatch.setattr(settings, "REQUIREMENT_EXTRACTOR_PROVIDER", "fixture")
    monkeypatch.setattr(settings, "APP_ENV", "test")
    _org, _project, doc = _seed(db)
    other = Organization(name="Other")
    db.add(other)
    db.commit()

    asyncio.run(worker.extract_requirements_task(None, str(doc.id), str(other.id)))

    assert db.scalars(select(RequirementCandidate)).all() == []
