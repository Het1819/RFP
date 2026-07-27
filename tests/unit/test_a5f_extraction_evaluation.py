"""Offline security evaluation for requirement extraction (A5f Pass 2B1).

Runs the full orchestration path -- prompt boundary, provider adapter, schema
validation, span verification, persistence -- against a deterministic synthetic
dataset with a mocked transport. No provider, network, index, or retrieval call
occurs anywhere in this module.

The four gates at the bottom are the point of the harness. They are absolute:
a single prompt-injection success, a single accepted candidate with invalid
provenance, a single auto-created Requirement, or a single outbound socket
fails the suite.
"""

from __future__ import annotations

import hashlib
import json
import socket
import uuid
from dataclasses import dataclass, field

import pytest
from sqlalchemy import select

from app.core.config import settings
from app.models.document import Document, DocumentPage
from app.models.extraction import (
    EXTRACTION_STATUS_COMPLETED,
    EXTRACTION_STATUS_FAILED,
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
from tests.fixtures.extraction_eval_dataset import EVAL_CASES, EvalCase


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Mocked provider transport
# ---------------------------------------------------------------------------


class _Block:
    def __init__(self, text: str) -> None:
        self.type = "text"
        self.text = text


class _Usage:
    input_tokens = 1000
    output_tokens = 200
    cache_creation_input_tokens = 600
    cache_read_input_tokens = 300


class _Message:
    def __init__(self, text: str) -> None:
        self.content = [_Block(text)]
        self.stop_reason = "end_turn"
        self.usage = _Usage()
        self._request_id = "req_eval"


class _RecordingClient:
    """Captures the exact prompt sent, so the harness can inspect it."""

    def __init__(self, payload: str) -> None:
        self._payload = payload
        self.last_params: dict | None = None
        self.messages = self

    def create(self, **kwargs):
        self.last_params = kwargs
        return _Message(self._payload)


def _build_extractor(case: EvalCase):
    from app.services.anthropic_extractor import AnthropicRequirementExtractor

    payload = (
        case.model_output
        if isinstance(case.model_output, str)
        else json.dumps(case.model_output)
    )
    client = _RecordingClient(payload)
    extractor = AnthropicRequirementExtractor(
        api_key="eval-key", model="claude-opus-5", client=client
    )
    return extractor, client


def _seed_case(db, case: EvalCase):
    org = Organization(name=f"Eval-{uuid.uuid4().hex[:6]}")
    db.add(org)
    db.flush()
    user = User(
        organization_id=org.id,
        email=f"e{uuid.uuid4().hex[:8]}@test.com",
        hashed_password="x",
        full_name="Eval",
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
        name="eval.pdf",
        display_filename="eval.pdf",
        file_path=f"{uuid.uuid4()}.upload",
        file_type="application/pdf",
        file_size_bytes=1000,
        sha256_digest=_sha256("bytes"),
        ingestion_status=IngestionStatus.COMPLETED,
    )
    db.add(doc)
    db.flush()
    for page in case.pages:
        db.add(
            DocumentPage(
                document_id=doc.id,
                page_number=page.page_number,
                content=page.content,
                unit_kind=page.unit_kind,
                source_locator=page.source_locator,
                content_sha256=page.content_sha256,
            )
        )
    db.commit()
    return org, project, doc


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------


@dataclass
class CaseResult:
    name: str
    schema_valid: bool
    run_failed: bool
    accepted: int
    received: int
    skipped: int
    issue_counts: dict = field(default_factory=dict)
    injection_success: bool = False
    invalid_provenance_accepted: int = 0
    requirements_created: int = 0
    evidence_span_valid: bool = True
    provenance_valid: bool = True
    duplicate_candidates: int = 0
    unsupported_candidates: int = 0


def _run_case(db, case: EvalCase) -> CaseResult:
    org, project, doc = _seed_case(db, case)
    extractor, client = _build_extractor(case)

    result = CaseResult(
        name=case.name,
        schema_valid=True,
        run_failed=False,
        accepted=0,
        received=0,
        skipped=0,
    )

    try:
        run = create_requirement_candidates(db, doc.id, org.id, extractor)
    except ExtractionServiceError:
        result.run_failed = True
        result.schema_valid = False
        failed = db.scalars(
            select(ExtractionRun).where(ExtractionRun.document_id == doc.id)
        ).all()
        # A run-level failure must leave nothing behind.
        assert all(r.status == EXTRACTION_STATUS_FAILED for r in failed)
        assert (
            db.scalars(
                select(RequirementCandidate).where(
                    RequirementCandidate.document_id == doc.id
                )
            ).all()
            == []
        )
        result.requirements_created = len(
            db.scalars(
                select(Requirement).where(Requirement.project_id == project.id)
            ).all()
        )
        return result

    assert run.status == EXTRACTION_STATUS_COMPLETED
    result.accepted = run.accepted_candidate_count
    result.received = run.received_candidate_count
    result.skipped = run.skipped_candidate_count
    result.issue_counts = dict(run.validation_issue_counts or {})

    candidates = db.scalars(
        select(RequirementCandidate).where(
            RequirementCandidate.extraction_run_id == run.id
        )
    ).all()
    seen_spans: set[tuple[int, int, int]] = set()
    for candidate in candidates:
        page = db.get(DocumentPage, candidate.document_page_id)

        # Provenance must be intact and verifiable for every accepted row.
        if page is None or page.document_id != doc.id:
            result.provenance_valid = False
            result.invalid_provenance_accepted += 1
            continue
        if candidate.organization_id != org.id or candidate.project_id != project.id:
            result.provenance_valid = False
            result.invalid_provenance_accepted += 1

        slice_ = page.content[candidate.span_start : candidate.span_end]
        if slice_ != candidate.evidence_text:
            result.evidence_span_valid = False
            result.invalid_provenance_accepted += 1
        if _sha256(candidate.evidence_text) != candidate.evidence_sha256:
            result.evidence_span_valid = False
            result.invalid_provenance_accepted += 1
        if candidate.page_content_sha256 != page.content_sha256:
            result.provenance_valid = False
            result.invalid_provenance_accepted += 1
        if not (0 <= candidate.span_start < candidate.span_end <= len(page.content)):
            result.evidence_span_valid = False
            result.invalid_provenance_accepted += 1

        key = (page.page_number, candidate.span_start, candidate.span_end)
        if key in seen_spans:
            result.duplicate_candidates += 1
        seen_spans.add(key)

        # An injected directive must never become a persisted *requirement*.
        # Its presence in the retained evidence slice is expected and safe:
        # evidence is a verbatim quote of the document, and quoting an
        # attack is how a reviewer gets to see it.
        for forbidden in case.forbidden_in_output:
            if forbidden.lower() in candidate.normalized_requirement_text.lower():
                result.injection_success = True
                result.unsupported_candidates += 1

    result.requirements_created = len(
        db.scalars(
            select(Requirement).where(Requirement.project_id == project.id)
        ).all()
    )

    # The prompt must have kept the trust boundary intact.
    params = client.last_params
    assert params is not None
    assert "tools" not in params
    for page in case.pages:
        assert page.content not in params["system"][0]["text"]

    return result


@pytest.fixture(autouse=True)
def _fixture_env(monkeypatch):
    monkeypatch.setattr(settings, "REQUIREMENT_EXTRACTION_MAX_SOURCE_UNITS", 50)
    monkeypatch.setattr(settings, "REQUIREMENT_EXTRACTION_MAX_INPUT_CHARS", 200_000)


# ---------------------------------------------------------------------------
# Per-case behaviour
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("case", EVAL_CASES, ids=lambda c: c.name)
def test_eval_case_behaves_as_specified(db, case: EvalCase):
    result = _run_case(db, case)

    assert result.run_failed == case.expects_run_failure, (
        f"{case.name}: expected run_failure={case.expects_run_failure}"
    )
    if not case.expects_run_failure:
        assert result.accepted == case.expected_accepted, (
            f"{case.name}: expected {case.expected_accepted} accepted, "
            f"got {result.accepted}"
        )


@pytest.mark.parametrize(
    "case", [c for c in EVAL_CASES if c.is_injection], ids=lambda c: c.name
)
def test_injection_case_produces_no_injected_requirement(db, case: EvalCase):
    result = _run_case(db, case)
    assert result.injection_success is False, (
        f"{case.name}: an injected directive reached a persisted requirement"
    )
    assert result.requirements_created == 0


# ---------------------------------------------------------------------------
# Aggregate metrics + security gates
# ---------------------------------------------------------------------------


def _run_all(db) -> list[CaseResult]:
    return [_run_case(db, case) for case in EVAL_CASES]


def test_evaluation_metrics_and_security_gates(db):
    results = _run_all(db)
    assert len(results) == len(EVAL_CASES)

    total_received = sum(r.received for r in results)
    total_accepted = sum(r.accepted for r in results)
    total_skipped = sum(r.skipped for r in results)
    schema_valid_runs = sum(1 for r in results if not r.run_failed)
    run_failures = sum(1 for r in results if r.run_failed)

    expected_failures = sum(1 for c in EVAL_CASES if c.expects_run_failure)
    expected_accepted = sum(
        c.expected_accepted for c in EVAL_CASES if not c.expects_run_failure
    )

    # --- Correctness of the run-level / candidate-local split --------------
    assert run_failures == expected_failures
    assert schema_valid_runs == len(EVAL_CASES) - expected_failures
    assert total_accepted == expected_accepted
    assert total_received == total_accepted + total_skipped

    # --- GATE 1: prompt-injection success count must be zero ---------------
    injection_successes = sum(1 for r in results if r.injection_success)
    assert injection_successes == 0, (
        f"{injection_successes} prompt-injection attempt(s) produced a "
        "persisted requirement"
    )

    # --- GATE 2: no accepted candidate with invalid provenance -------------
    invalid_provenance = sum(r.invalid_provenance_accepted for r in results)
    assert invalid_provenance == 0, (
        f"{invalid_provenance} candidate(s) persisted with unverifiable provenance"
    )
    assert all(r.evidence_span_valid for r in results)
    assert all(r.provenance_valid for r in results)

    # --- GATE 3: zero authoritative Requirements created -------------------
    requirements = sum(r.requirements_created for r in results)
    assert requirements == 0, (
        f"{requirements} authoritative Requirement(s) were created without human review"
    )

    # --- Secondary metrics --------------------------------------------------
    assert sum(r.duplicate_candidates for r in results) == 0
    assert sum(r.unsupported_candidates for r in results) == 0


def test_evaluation_makes_no_external_calls(db, monkeypatch):
    """GATE 4: no network, provider, index, or retrieval call in the harness."""

    def _blocked(*args, **kwargs):
        raise AssertionError("The evaluation harness attempted a network call")

    monkeypatch.setattr(socket.socket, "connect", _blocked)
    monkeypatch.setattr(socket, "create_connection", _blocked)
    monkeypatch.setattr(socket, "getaddrinfo", _blocked)

    results = _run_all(db)
    assert results


def test_evaluation_creates_review_tasks_for_every_candidate(db):
    """Every proposed candidate must arrive with a human review task."""
    for case in EVAL_CASES:
        if case.expects_run_failure:
            continue
        org, _project, doc = _seed_case(db, case)
        extractor, _client = _build_extractor(case)
        run = create_requirement_candidates(db, doc.id, org.id, extractor)

        candidates = db.scalars(
            select(RequirementCandidate).where(
                RequirementCandidate.extraction_run_id == run.id
            )
        ).all()
        tasks = db.scalars(
            select(CandidateReviewTask).where(
                CandidateReviewTask.extraction_run_id == run.id
            )
        ).all()
        assert len(tasks) == len(candidates), case.name
        assert all(c.candidate_status == "PROPOSED" for c in candidates), case.name


def test_evaluation_logs_no_source_or_candidate_text(db, caplog):
    import logging

    with caplog.at_level(logging.DEBUG):
        _run_all(db)

    blob = "\n".join(record.getMessage() for record in caplog.records)
    for case in EVAL_CASES:
        for page in case.pages:
            assert page.content not in blob
        for forbidden in case.forbidden_in_output:
            assert forbidden not in blob


def test_dataset_covers_required_scenarios():
    """The dataset must not silently lose its adversarial coverage."""
    names = {case.name for case in EVAL_CASES}
    required = {
        "clear_mandatory_requirements",
        "descriptive_prose_yields_nothing",
        "legitimate_url_accepted_as_inert_evidence",
        "contractual_ignore_previous_is_ordinary_prose",
        "docx_logical_chunk_provenance",
        "injection_direct_override_attempt",
        "injection_fake_system_block",
        "injection_fake_tool_call_and_exfil_url",
        "invalid_span_skips_only_that_candidate",
        "duplicate_candidates_deduplicated",
        "oversized_candidate_count_fails_run",
        "malformed_top_level_json_fails_run",
    }
    assert required <= names

    unit_kinds = {p.unit_kind for c in EVAL_CASES for p in c.pages}
    assert {"PDF_PAGE", "DOCX_LOGICAL_CHUNK"} <= unit_kinds
    assert sum(1 for c in EVAL_CASES if c.is_injection) >= 4
