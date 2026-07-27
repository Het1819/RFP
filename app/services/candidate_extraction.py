"""Requirement-candidate extraction orchestration service (A5f Pass 1).

Implements the three-phase extraction pipeline:

  Phase A — Snapshot (transactional, row-locked):
    Lock Document (requires COMPLETED), verify tenant/project ownership,
    check for duplicate successful run (idempotency), load ordered page
    metadata, compute deterministic input_snapshot_sha256, create RUNNING
    ExtractionRun, commit.

  Phase B — Extraction (no open transaction):
    Build ExtractionRequest from page metadata (no storage paths, secrets,
    or unrelated documents), call extractor.extract(), validate response.

  Phase C — Persistence (transactional, row-locked):
    Lock ExtractionRun and Document, re-confirm COMPLETED, re-verify every
    page hash matches the snapshot, atomically create all RequirementCandidate
    and CandidateReviewTask rows, mark ExtractionRun COMPLETED, commit.

Failure contract:
  - Any failure leaves zero partial candidates or tasks.
  - ExtractionRun is marked FAILED with a fixed safe code.
  - Previous successful candidates for this document are preserved.
  - No authoritative Requirement records are ever created.

Security:
  - Tenant and project authorization on every entry point.
  - No source text in logs (IDs, versions, counts, latency, fixed codes only).
  - No prompt or model response in logs.
  - PROPOSED candidates are never used downstream without human approval.
"""

from __future__ import annotations

import hashlib
import logging
import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.observability import request_id_var
from app.models.audit import AuditEvent
from app.models.document import Document, DocumentPage
from app.models.extraction import (
    AUDIT_CANDIDATE_SUPERSEDED,
    AUDIT_EXTRACTION_COMPLETED,
    AUDIT_EXTRACTION_FAILED,
    AUDIT_EXTRACTION_INPUT_LIMIT,
    AUDIT_EXTRACTION_STARTED,
    CANDIDATE_REVIEW_TASK_TYPE,
    CANDIDATE_STATUS_PROPOSED,
    CANDIDATE_STATUS_SUPERSEDED,
    EXTRACTION_STATUS_COMPLETED,
    EXTRACTION_STATUS_FAILED,
    EXTRACTION_STATUS_RUNNING,
    MAX_EVIDENCE_TEXT_LEN,
    MAX_REQUIREMENT_TEXT_LEN,
    REVIEW_TASK_STATUS_OPEN,
    REVIEW_TASK_STATUS_SUPERSEDED,
    CandidateReviewTask,
    ExtractionRun,
    RequirementCandidate,
)
from app.models.project import ProposalProject
from app.services.extraction_contract import (
    SCHEMA_VERSION,
    ExtractionRequest,
    ExtractionResponse,
    SourceUnit,
    find_unsafe_content,
)
from app.services.extraction_prompt import PROMPT_VERSION as _PROMPT_VERSION
from app.services.ingestion_state import IngestionStatus
from app.services.requirement_extractor import (
    ExtractionError,
    RequirementExtractor,
)

logger = logging.getLogger(__name__)

EXTRACTION_SCHEMA_VERSION = SCHEMA_VERSION
# Sourced from the prompt module so the run identity always reflects the actual
# policy text sent to the provider: editing the policy without bumping this
# would let a run deduplicate against one produced by a different prompt.
PROMPT_VERSION = _PROMPT_VERSION

# Fixed failure codes — never include source text.
_FAIL_NOT_COMPLETED = "DOCUMENT_NOT_COMPLETED"
_FAIL_TENANT_MISMATCH = "TENANT_MISMATCH"
_FAIL_DUPLICATE_RUN = "DUPLICATE_COMPLETED_RUN"
_FAIL_SNAPSHOT_MISMATCH = "SNAPSHOT_HASH_MISMATCH"
_FAIL_PAGE_HASH_MISMATCH = "PAGE_CONTENT_HASH_MISMATCH"
_FAIL_EXTRACTOR = "EXTRACTOR_FAILED"
_FAIL_PERSISTENCE = "PERSISTENCE_FAILED"
_FAIL_STALE_ATTEMPT = "STALE_EXTRACTION_ATTEMPT"
_FAIL_INPUT_LIMIT = "EXTRACTION_INPUT_LIMIT"

# Candidate-local skip reasons. These never fail the run; they are counted into
# ExtractionRun.validation_issue_counts so the gap between what the extractor
# proposed and what was persisted stays auditable without retaining any raw
# rejected content.
_SKIP_UNKNOWN_SOURCE_UNIT = "SKIP_UNKNOWN_SOURCE_UNIT"
_SKIP_INVALID_SPAN = "SKIP_INVALID_SPAN"
_SKIP_EMPTY_EVIDENCE = "SKIP_EMPTY_EVIDENCE"
_SKIP_EVIDENCE_TOO_LONG = "SKIP_EVIDENCE_TOO_LONG"
_SKIP_REQUIREMENT_TEXT_BOUNDS = "SKIP_REQUIREMENT_TEXT_BOUNDS"
_SKIP_DUPLICATE_CANDIDATE = "SKIP_DUPLICATE_CANDIDATE"


class ExtractionServiceError(Exception):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(message)


def _compute_snapshot_sha256(pages: list[DocumentPage]) -> str:
    """Deterministic SHA-256 over ordered page metadata.

    Input string per page (pipe-separated, newline-terminated):
      {id}|{page_number}|{unit_kind or ''}|{source_locator or ''}|{content_sha256 or ''}
    Pages are ordered by page_number ascending.
    """
    h = hashlib.sha256()
    for page in sorted(pages, key=lambda p: p.page_number):
        line = (
            f"{page.id}"
            f"|{page.page_number}"
            f"|{page.unit_kind or ''}"
            f"|{page.source_locator or ''}"
            f"|{page.content_sha256 or ''}\n"
        )
        h.update(line.encode("utf-8"))
    return h.hexdigest()


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _load_ordered_pages(db: Session, document_id: uuid.UUID) -> list[DocumentPage]:
    return list(
        db.scalars(
            select(DocumentPage)
            .where(DocumentPage.document_id == document_id)
            .order_by(DocumentPage.page_number.asc())
        )
    )


def create_requirement_candidates(
    db: Session,
    document_id: uuid.UUID,
    organization_id: uuid.UUID,
    extractor: RequirementExtractor,
) -> ExtractionRun:
    """Orchestrate requirement-candidate extraction for ``document_id``.

    Returns the completed ExtractionRun.  Raises ExtractionServiceError on
    any terminal failure (run is marked FAILED before raising).
    """
    run_id = uuid.uuid4()
    attempt_id = str(uuid.uuid4())
    now = datetime.now(UTC)

    # ------------------------------------------------------------------
    # Phase A: Snapshot
    # ------------------------------------------------------------------
    doc = (
        db.query(Document).filter(Document.id == document_id).with_for_update().first()
    )
    if not doc:
        db.rollback()
        raise ExtractionServiceError(
            _FAIL_NOT_COMPLETED, f"Document {document_id} not found"
        )

    # Tenant ownership. Checked before status so a caller from another tenant
    # cannot probe a document's ingestion state.
    project = (
        db.query(ProposalProject).filter(ProposalProject.id == doc.project_id).first()
    )
    if not project or project.organization_id != organization_id:
        db.rollback()
        raise ExtractionServiceError(
            _FAIL_TENANT_MISMATCH,
            f"Tenant {organization_id} does not own document {document_id}",
        )

    # Require COMPLETED
    if doc.ingestion_status != IngestionStatus.COMPLETED:
        db.rollback()
        raise ExtractionServiceError(
            _FAIL_NOT_COMPLETED,
            f"Document {document_id} status is {doc.ingestion_status!r}, "
            "expected COMPLETED",
        )

    # Load ordered pages
    pages = _load_ordered_pages(db, document_id)

    # Every page must carry a parser-recorded content hash that still matches
    # its stored content. Without this, the snapshot would bind to a hash that
    # no longer describes the text the extractor is about to read.
    for page in pages:
        if not page.content_sha256 or page.content_sha256 != _sha256_text(page.content):
            db.rollback()
            raise ExtractionServiceError(
                _FAIL_PAGE_HASH_MISMATCH,
                f"DocumentPage {page.id} content hash does not match its content",
            )

    # Input budget. Enforced before the run row exists so an oversized document
    # never occupies a RUNNING run, and fails closed rather than truncating:
    # a silently truncated extraction reports success while dropping real
    # requirements, which is the one failure a compliance matrix cannot absorb.
    # Deterministic multi-batch extraction is deferred to a later pass.
    total_chars = sum(len(p.content) for p in pages)
    max_units = settings.REQUIREMENT_EXTRACTION_MAX_SOURCE_UNITS
    max_chars = settings.REQUIREMENT_EXTRACTION_MAX_INPUT_CHARS
    if len(pages) > max_units or total_chars > max_chars:
        db.rollback()
        _record_extraction_audit(
            db,
            organization_id=organization_id,
            project_id=project.id,
            document_id=document_id,
            run_id=None,
            action=AUDIT_EXTRACTION_INPUT_LIMIT,
            details={
                "page_count": len(pages),
                "total_characters": total_chars,
                "max_source_units": max_units,
                "max_input_characters": max_chars,
                "result_code": _FAIL_INPUT_LIMIT,
            },
        )
        logger.warning(
            "extraction.input_limit: document_id=%s pages=%d chars=%d "
            "max_pages=%d max_chars=%d",
            document_id,
            len(pages),
            total_chars,
            max_units,
            max_chars,
        )
        raise ExtractionServiceError(
            _FAIL_INPUT_LIMIT,
            f"Document exceeds extraction input limits "
            f"({len(pages)} units / {total_chars} characters); "
            "operator action required",
        )

    snapshot_sha256 = _compute_snapshot_sha256(pages)

    # Idempotency: reject if a COMPLETED run already exists for this snapshot
    existing = (
        db.query(ExtractionRun)
        .filter(
            ExtractionRun.document_id == document_id,
            ExtractionRun.input_snapshot_sha256 == snapshot_sha256,
            ExtractionRun.extraction_schema_version == EXTRACTION_SCHEMA_VERSION,
            ExtractionRun.prompt_version == PROMPT_VERSION,
            ExtractionRun.status == EXTRACTION_STATUS_COMPLETED,
        )
        .first()
    )
    if existing:
        db.rollback()
        logger.info(
            "extraction.duplicate_run: document_id=%s existing_run=%s",
            document_id,
            existing.id,
        )
        raise ExtractionServiceError(
            _FAIL_DUPLICATE_RUN,
            f"A completed run already exists for this snapshot: {existing.id}",
        )

    # Create ExtractionRun in RUNNING state
    run = ExtractionRun(
        id=run_id,
        organization_id=organization_id,
        project_id=project.id,
        document_id=document_id,
        status=EXTRACTION_STATUS_RUNNING,
        extraction_attempt_id=attempt_id,
        input_snapshot_sha256=snapshot_sha256,
        page_count=len(pages),
        parser_version=doc.parser_version,
        extraction_schema_version=EXTRACTION_SCHEMA_VERSION,
        prompt_version=PROMPT_VERSION,
        provider=extractor.provider_name,
        model=extractor.model_name,
        started_at=now,
    )
    db.add(run)
    db.commit()

    logger.info(
        "extraction.started: run_id=%s document_id=%s pages=%d "
        "schema_version=%s prompt_version=%s provider=%s",
        run_id,
        document_id,
        len(pages),
        EXTRACTION_SCHEMA_VERSION,
        PROMPT_VERSION,
        extractor.provider_name,
    )
    _record_extraction_audit(
        db,
        organization_id=organization_id,
        project_id=project.id,
        document_id=document_id,
        run_id=run_id,
        action=AUDIT_EXTRACTION_STARTED,
        details={
            "page_count": len(pages),
            "total_characters": total_chars,
            "provider": extractor.provider_name,
            "model": extractor.model_name,
        },
    )

    # ------------------------------------------------------------------
    # Phase B: Extraction (no DB transaction open)
    # ------------------------------------------------------------------
    source_units = [
        SourceUnit(
            sequence=p.page_number,
            page_id=str(p.id),
            unit_kind=p.unit_kind or "",
            source_locator=p.source_locator or "",
            content=p.content,
            content_sha256=p.content_sha256 or "",
        )
        for p in pages
    ]

    request = ExtractionRequest(
        document_id=str(document_id),
        extraction_run_id=str(run_id),
        extraction_schema_version=EXTRACTION_SCHEMA_VERSION,
        prompt_version=PROMPT_VERSION,
        source_units=source_units,
    )

    try:
        response: ExtractionResponse = extractor.extract(request)
    except ExtractionError as err:
        _record_provider_usage(db, run_id, extractor)
        _mark_run_failed(db, run_id, err.code)
        _record_extraction_audit(
            db,
            organization_id=organization_id,
            project_id=project.id,
            document_id=document_id,
            run_id=run_id,
            action=AUDIT_EXTRACTION_FAILED,
            details={"result_code": err.code, "provider": extractor.provider_name},
        )
        raise ExtractionServiceError(err.code, err.message) from err
    except Exception as err:
        _record_provider_usage(db, run_id, extractor)
        _mark_run_failed(db, run_id, _FAIL_EXTRACTOR)
        logger.error(
            "extraction.extractor_error: run_id=%s type=%s",
            run_id,
            type(err).__name__,
        )
        _record_extraction_audit(
            db,
            organization_id=organization_id,
            project_id=project.id,
            document_id=document_id,
            run_id=run_id,
            action=AUDIT_EXTRACTION_FAILED,
            details={"result_code": _FAIL_EXTRACTOR},
        )
        raise ExtractionServiceError(
            _FAIL_EXTRACTOR,
            f"Extractor raised unexpected exception: {type(err).__name__}",
        ) from err

    # Usage accounting is recorded before persistence so token spend is
    # captured even if the write phase later fails.
    _record_provider_usage(db, run_id, extractor)

    # ------------------------------------------------------------------
    # Phase C: Validation and atomic persistence
    # ------------------------------------------------------------------
    try:
        _persist_candidates(
            db=db,
            run_id=run_id,
            attempt_id=attempt_id,
            organization_id=organization_id,
            project_id=project.id,
            document_id=document_id,
            snapshot_sha256=snapshot_sha256,
            response=response,
        )
    except ExtractionServiceError:
        raise
    except Exception as err:
        _mark_run_failed(db, run_id, _FAIL_PERSISTENCE)
        logger.error(
            "extraction.persistence_error: run_id=%s type=%s",
            run_id,
            type(err).__name__,
        )
        raise ExtractionServiceError(
            _FAIL_PERSISTENCE, f"Persistence failed: {type(err).__name__}"
        ) from err

    completed_run = db.get(ExtractionRun, run_id)
    logger.info(
        "extraction.completed: run_id=%s candidates=%d",
        run_id,
        completed_run.candidate_count if completed_run else 0,
    )
    _record_extraction_audit(
        db,
        organization_id=organization_id,
        project_id=project.id,
        document_id=document_id,
        run_id=run_id,
        action=AUDIT_EXTRACTION_COMPLETED,
        details={
            "provider": extractor.provider_name,
            "model": extractor.model_name,
            "received_candidate_count": completed_run.received_candidate_count
            if completed_run
            else 0,
            "accepted_candidate_count": completed_run.accepted_candidate_count
            if completed_run
            else 0,
            "skipped_candidate_count": completed_run.skipped_candidate_count
            if completed_run
            else 0,
            "provider_call_count": completed_run.provider_call_count
            if completed_run
            else 0,
            "input_tokens": completed_run.input_tokens if completed_run else 0,
            "output_tokens": completed_run.output_tokens if completed_run else 0,
            "duration_ms": completed_run.duration_ms if completed_run else 0,
            "result_code": "EXTRACTION_OK",
        },
    )
    return completed_run  # type: ignore[return-value]


def _mark_run_failed(db: Session, run_id: uuid.UUID, code: str) -> None:
    """Mark an ExtractionRun as FAILED in a fresh nested save-point."""
    try:
        run = db.query(ExtractionRun).filter(ExtractionRun.id == run_id).first()
        if run:
            run.status = EXTRACTION_STATUS_FAILED
            run.failure_code = code
            run.completed_at = datetime.now(UTC)
            db.commit()
    except Exception:
        logger.exception("extraction.mark_failed: could not mark run %s FAILED", run_id)
        try:
            db.rollback()
        except Exception:
            pass


def _record_extraction_audit(
    db: Session,
    organization_id: uuid.UUID,
    project_id: uuid.UUID,
    document_id: uuid.UUID,
    run_id: uuid.UUID | None,
    action: str,
    details: dict[str, object],
) -> None:
    """Write a fixed extraction audit event and commit it.

    Payloads carry IDs, versions, counts, and fixed result codes only. No
    prompt, no source text, no model output, no credentials, and no raw
    exception ever reaches an audit row.

    Auditing must never be the reason an extraction fails, so a failure here is
    logged and swallowed rather than propagated.
    """
    try:
        payload: dict[str, object] = {
            "project_id": str(project_id),
            "document_id": str(document_id),
            "extraction_schema_version": EXTRACTION_SCHEMA_VERSION,
            "prompt_version": PROMPT_VERSION,
        }
        payload.update(details)
        db.add(
            AuditEvent(
                organization_id=organization_id,
                user_id=None,
                action=action,
                entity_type="ExtractionRun",
                entity_id=run_id or document_id,
                details=payload,
                request_id=request_id_var.get(),
            )
        )
        db.commit()
    except Exception:
        logger.exception(
            "extraction.audit_failed: action=%s document_id=%s", action, document_id
        )
        try:
            db.rollback()
        except Exception:
            pass


def _record_provider_usage(
    db: Session, run_id: uuid.UUID, extractor: RequirementExtractor
) -> None:
    """Persist token/cache/latency counters reported by the extractor.

    Reads a duck-typed ``usage`` attribute so extractors that do not call a
    provider (fixture, disabled) contribute nothing and need no special case.
    Counters only -- prompts and responses are never persisted.
    """
    usage = getattr(extractor, "usage", None)
    if usage is None:
        return
    try:
        run = db.get(ExtractionRun, run_id)
        if run is None:
            return
        run.provider_call_count = int(getattr(usage, "provider_call_count", 0) or 0)
        run.input_tokens = int(getattr(usage, "input_tokens", 0) or 0)
        run.output_tokens = int(getattr(usage, "output_tokens", 0) or 0)
        run.cache_creation_input_tokens = int(
            getattr(usage, "cache_creation_input_tokens", 0) or 0
        )
        run.cache_read_input_tokens = int(
            getattr(usage, "cache_read_input_tokens", 0) or 0
        )
        run.duration_ms = int(getattr(usage, "duration_ms", 0) or 0)
        db.commit()
    except Exception:
        logger.exception("extraction.usage_record_failed: run_id=%s", run_id)
        try:
            db.rollback()
        except Exception:
            pass


def _supersede_stale_candidates(
    db: Session,
    organization_id: uuid.UUID,
    document_id: uuid.UUID,
    current_run_id: uuid.UUID,
) -> int:
    """Mark PROPOSED candidates from earlier runs of this document SUPERSEDED.

    Tenant-scoped and idempotent: a second call finds nothing left in PROPOSED
    and is a no-op. Writes an audit event only when something actually changed,
    and only ever transitions PROPOSED -- reviewed candidates and any
    Requirements promoted from them are left untouched.
    """
    stale = list(
        db.scalars(
            select(RequirementCandidate).where(
                RequirementCandidate.organization_id == organization_id,
                RequirementCandidate.document_id == document_id,
                RequirementCandidate.extraction_run_id != current_run_id,
                RequirementCandidate.candidate_status == CANDIDATE_STATUS_PROPOSED,
            )
        )
    )
    if not stale:
        return 0

    now = datetime.now(UTC)
    project_id = stale[0].project_id
    for candidate in stale:
        candidate.candidate_status = CANDIDATE_STATUS_SUPERSEDED

    # Close the open review tasks for those candidates so a reviewer is not
    # asked to decide on a proposal that no longer reflects the document.
    open_tasks = list(
        db.scalars(
            select(CandidateReviewTask).where(
                CandidateReviewTask.organization_id == organization_id,
                CandidateReviewTask.candidate_id.in_([c.id for c in stale]),
                CandidateReviewTask.status == REVIEW_TASK_STATUS_OPEN,
            )
        )
    )
    for task in open_tasks:
        task.status = REVIEW_TASK_STATUS_SUPERSEDED
        task.resolved_at = now

    db.add(
        AuditEvent(
            organization_id=organization_id,
            user_id=None,
            action=AUDIT_CANDIDATE_SUPERSEDED,
            entity_type="ExtractionRun",
            entity_id=current_run_id,
            details={
                "project_id": str(project_id),
                "document_id": str(document_id),
                "superseded_candidate_count": len(stale),
                "closed_task_count": len(open_tasks),
                "extraction_schema_version": EXTRACTION_SCHEMA_VERSION,
                "result_code": AUDIT_CANDIDATE_SUPERSEDED,
            },
            request_id=request_id_var.get(),
        )
    )
    return len(stale)


def _persist_candidates(
    db: Session,
    run_id: uuid.UUID,
    attempt_id: str,
    organization_id: uuid.UUID,
    project_id: uuid.UUID,
    document_id: uuid.UUID,
    snapshot_sha256: str,
    response: ExtractionResponse,
) -> None:
    """Phase C: lock, re-verify, atomically persist candidates + review tasks."""
    # Lock ExtractionRun and re-confirm Document is still COMPLETED
    run = (
        db.query(ExtractionRun)
        .filter(ExtractionRun.id == run_id)
        .with_for_update()
        .first()
    )
    if not run or run.status != EXTRACTION_STATUS_RUNNING:
        raise ExtractionServiceError(
            _FAIL_PERSISTENCE, "ExtractionRun no longer in RUNNING state"
        )

    # A redelivered or superseded attempt must never write. The run row is the
    # single arbiter of which attempt owns this extraction.
    if run.extraction_attempt_id != attempt_id:
        raise ExtractionServiceError(
            _FAIL_STALE_ATTEMPT,
            f"ExtractionRun {run_id} is owned by a different extraction attempt",
        )

    doc = (
        db.query(Document).filter(Document.id == document_id).with_for_update().first()
    )
    if not doc or doc.ingestion_status != IngestionStatus.COMPLETED:
        _mark_run_failed(db, run_id, _FAIL_NOT_COMPLETED)
        raise ExtractionServiceError(
            _FAIL_NOT_COMPLETED, "Document is no longer COMPLETED"
        )

    # Re-load pages and reconfirm snapshot hash
    fresh_pages = _load_ordered_pages(db, document_id)

    current_snapshot = _compute_snapshot_sha256(fresh_pages)
    if current_snapshot != snapshot_sha256:
        _mark_run_failed(db, run_id, _FAIL_SNAPSHOT_MISMATCH)
        raise ExtractionServiceError(
            _FAIL_SNAPSHOT_MISMATCH,
            "DocumentPage snapshot changed between extraction phases",
        )

    # Build page lookup by sequence number
    page_by_seq: dict[int, DocumentPage] = {p.page_number: p for p in fresh_pages}

    # Every page hash must still describe its content before any candidate is
    # written. This is document integrity, not per-candidate validity, so it
    # fails the whole run rather than skipping one item.
    for source_page in fresh_pages:
        if not source_page.content_sha256 or source_page.content_sha256 != _sha256_text(
            source_page.content
        ):
            _mark_run_failed(db, run_id, _FAIL_PAGE_HASH_MISMATCH)
            raise ExtractionServiceError(
                _FAIL_PAGE_HASH_MISMATCH,
                f"DocumentPage {source_page.id} content hash "
                "does not match its content",
            )

    # Validate and build candidate objects.
    #
    # A single malformed candidate is a defect in one item of model output, not
    # evidence that the document or the run is untrustworthy. Skipping it and
    # keeping its valid siblings is what a reviewer actually wants: one bad span
    # out of forty should not discard thirty-nine real requirements. Run-level
    # integrity failures (document status, snapshot drift, stale attempt, page
    # hash) are handled above and still fail everything.
    seen_spans: set[tuple[int, int, int, str]] = (
        set()
    )  # (seq, start, end, norm_text[:100])
    candidate_rows: list[RequirementCandidate] = []
    task_rows: list[CandidateReviewTask] = []
    issue_counts: dict[str, int] = {}

    def _skip(code: str) -> None:
        """Record a candidate-local rejection by fixed code only."""
        issue_counts[code] = issue_counts.get(code, 0) + 1

    for unit in response.candidates:
        seq = unit.source_unit_sequence
        page = page_by_seq.get(seq)
        if page is None:
            _skip(_SKIP_UNKNOWN_SOURCE_UNIT)
            continue

        content = page.content
        span_start = unit.span_start
        span_end = unit.span_end

        if span_start < 0 or span_end > len(content) or span_end <= span_start:
            _skip(_SKIP_INVALID_SPAN)
            continue

        evidence = content[span_start:span_end]
        if not evidence.strip():
            _skip(_SKIP_EMPTY_EVIDENCE)
            continue
        if len(evidence) > MAX_EVIDENCE_TEXT_LEN:
            _skip(_SKIP_EVIDENCE_TOO_LONG)
            continue

        req_text = unit.requirement_text.strip()
        if not req_text or len(req_text) > MAX_REQUIREMENT_TEXT_LEN:
            _skip(_SKIP_REQUIREMENT_TEXT_BOUNDS)
            continue

        # Duplicate span+text check
        dedup_key = (seq, span_start, span_end, req_text[:100])
        if dedup_key in seen_spans:
            _skip(_SKIP_DUPLICATE_CANDIDATE)
            continue
        seen_spans.add(dedup_key)

        # Character-content policy. URLs, markup, paths, and instruction-shaped
        # prose are all legitimate RFP text and are retained as inert evidence;
        # only content that is not usable as text at all (NUL and other control
        # characters, i.e. binary or truncated data) is rejected.
        reject_code = find_unsafe_content(req_text) or find_unsafe_content(evidence)
        if reject_code is not None:
            _skip(reject_code)
            continue

        evidence_sha = _sha256_text(evidence)
        candidate_id = uuid.uuid4()

        candidate = RequirementCandidate(
            id=candidate_id,
            organization_id=organization_id,
            project_id=project_id,
            extraction_run_id=run_id,
            document_id=document_id,
            document_page_id=page.id,
            page_content_sha256=page.content_sha256 or "",
            unit_kind=page.unit_kind or "",
            source_locator=page.source_locator or "",
            span_start=span_start,
            span_end=span_end,
            evidence_text=evidence,
            evidence_sha256=evidence_sha,
            normalized_requirement_text=req_text,
            requirement_type=unit.requirement_type,
            confidence=unit.confidence,
            uncertainty_reason=unit.uncertainty_reason,
            extraction_schema_version=EXTRACTION_SCHEMA_VERSION,
            candidate_status=CANDIDATE_STATUS_PROPOSED,
        )
        candidate_rows.append(candidate)

        task = CandidateReviewTask(
            organization_id=organization_id,
            project_id=project_id,
            candidate_id=candidate_id,
            extraction_run_id=run_id,
            task_type=CANDIDATE_REVIEW_TASK_TYPE,
            source_locator=page.source_locator or "",
            status=REVIEW_TASK_STATUS_OPEN,
        )
        task_rows.append(task)

    # Supersede older unreviewed proposals for this document. A newer
    # successful run over a changed page snapshot makes the previous run's
    # untouched suggestions obsolete, and leaving them PROPOSED would show a
    # reviewer two competing candidates for the same text.
    #
    # Scoped to PROPOSED only: APPROVED, EDITED, and REJECTED are human
    # decisions and are never rewritten by a machine run. Approved Requirements
    # are likewise untouched -- reconciling an already-promoted Requirement
    # against re-extracted text is an explicit workflow, not a side effect.
    #
    # Runs before the new rows are added so its SELECT cannot trigger an
    # autoflush of half-built state.
    superseded_count = _supersede_stale_candidates(
        db,
        organization_id=organization_id,
        document_id=document_id,
        current_run_id=run_id,
    )

    # Atomic write: all candidates + tasks + run update in one commit.
    #
    # Candidates are flushed before their review tasks are added. Relying on
    # the unit of work to infer the order is not good enough: PostgreSQL
    # enforces the candidate_review_tasks -> requirement_candidates foreign key
    # immediately, and an autoflush at the wrong moment inserts a task whose
    # candidate row does not exist yet. Ordering it explicitly makes the write
    # correct regardless of when a flush happens to fire.
    db.add_all(candidate_rows)
    db.flush()
    db.add_all(task_rows)
    db.flush()

    received = len(response.candidates)
    accepted = len(candidate_rows)

    run.status = EXTRACTION_STATUS_COMPLETED
    run.candidate_count = accepted
    run.received_candidate_count = received
    run.accepted_candidate_count = accepted
    run.skipped_candidate_count = received - accepted
    run.validation_issue_counts = issue_counts or None
    run.completed_at = datetime.now(UTC)
    db.commit()

    if superseded_count:
        logger.info(
            "extraction.candidates_superseded: run_id=%s document_id=%s count=%d",
            run_id,
            document_id,
            superseded_count,
        )

    if issue_counts:
        # Fixed reason codes and counts only -- never the rejected candidate
        # text, which is untrusted model output over an untrusted document.
        logger.info(
            "extraction.candidates_skipped: run_id=%s received=%d accepted=%d "
            "skipped=%d issues=%s",
            run_id,
            received,
            accepted,
            received - accepted,
            sorted(issue_counts.items()),
        )
