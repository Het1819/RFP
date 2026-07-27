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

from app.models.document import Document, DocumentPage
from app.models.extraction import (
    CANDIDATE_REVIEW_TASK_TYPE,
    CANDIDATE_STATUS_PROPOSED,
    EXTRACTION_STATUS_COMPLETED,
    EXTRACTION_STATUS_FAILED,
    EXTRACTION_STATUS_RUNNING,
    MAX_EVIDENCE_TEXT_LEN,
    MAX_REQUIREMENT_TEXT_LEN,
    CandidateReviewTask,
    ExtractionRun,
    RequirementCandidate,
)
from app.models.project import ProposalProject
from app.services.extraction_contract import (
    ExtractionRequest,
    ExtractionResponse,
    SourceUnit,
    find_unsafe_content,
)
from app.services.ingestion_state import IngestionStatus
from app.services.requirement_extractor import (
    ExtractionError,
    RequirementExtractor,
)

logger = logging.getLogger(__name__)

EXTRACTION_SCHEMA_VERSION = "requirement-candidates-v1"
PROMPT_VERSION = "pass1-fixture-v1"  # updated when a real prompt is introduced

# Fixed failure codes — never include source text.
_FAIL_NOT_COMPLETED = "DOCUMENT_NOT_COMPLETED"
_FAIL_TENANT_MISMATCH = "TENANT_MISMATCH"
_FAIL_DUPLICATE_RUN = "DUPLICATE_COMPLETED_RUN"
_FAIL_SNAPSHOT_MISMATCH = "SNAPSHOT_HASH_MISMATCH"
_FAIL_PAGE_HASH_MISMATCH = "PAGE_CONTENT_HASH_MISMATCH"
_FAIL_VALIDATION = "EXTRACTION_RESPONSE_INVALID"
_FAIL_EXTRACTOR = "EXTRACTOR_FAILED"
_FAIL_PERSISTENCE = "PERSISTENCE_FAILED"
_FAIL_STALE_ATTEMPT = "STALE_EXTRACTION_ATTEMPT"
_FAIL_CONTENT_REJECTED = "CANDIDATE_CONTENT_REJECTED"


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
        "schema_version=%s prompt_version=%s",
        run_id,
        document_id,
        len(pages),
        EXTRACTION_SCHEMA_VERSION,
        PROMPT_VERSION,
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
        _mark_run_failed(db, run_id, err.code)
        raise ExtractionServiceError(err.code, err.message) from err
    except Exception as err:
        _mark_run_failed(db, run_id, _FAIL_EXTRACTOR)
        logger.error(
            "extraction.extractor_error: run_id=%s type=%s",
            run_id,
            type(err).__name__,
        )
        raise ExtractionServiceError(
            _FAIL_EXTRACTOR,
            f"Extractor raised unexpected exception: {type(err).__name__}",
        ) from err

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

    # Validate and build candidate objects
    seen_spans: set[tuple[int, int, int, str]] = (
        set()
    )  # (seq, start, end, norm_text[:100])
    candidate_rows: list[RequirementCandidate] = []
    task_rows: list[CandidateReviewTask] = []

    for unit in response.candidates:
        seq = unit.source_unit_sequence
        page = page_by_seq.get(seq)
        if page is None:
            _mark_run_failed(db, run_id, _FAIL_VALIDATION)
            raise ExtractionServiceError(
                _FAIL_VALIDATION,
                f"Candidate references unknown source_unit_sequence {seq}",
            )

        # Re-bind the page hash to the page text one final time before the
        # candidate is written, so page_content_sha256 provably describes the
        # content the evidence was sliced out of.
        content = page.content
        if not page.content_sha256 or page.content_sha256 != _sha256_text(content):
            _mark_run_failed(db, run_id, _FAIL_PAGE_HASH_MISMATCH)
            raise ExtractionServiceError(
                _FAIL_PAGE_HASH_MISMATCH,
                f"DocumentPage {page.id} content hash does not match its content",
            )

        span_start = unit.span_start
        span_end = unit.span_end

        if span_start < 0 or span_end > len(content) or span_end <= span_start:
            _mark_run_failed(db, run_id, _FAIL_VALIDATION)
            raise ExtractionServiceError(
                _FAIL_VALIDATION,
                f"Invalid span [{span_start}:{span_end}] "
                f"for page length {len(content)}",
            )

        evidence = content[span_start:span_end]
        if not evidence.strip():
            _mark_run_failed(db, run_id, _FAIL_VALIDATION)
            raise ExtractionServiceError(
                _FAIL_VALIDATION, "Evidence slice is empty or whitespace-only"
            )
        if len(evidence) > MAX_EVIDENCE_TEXT_LEN:
            _mark_run_failed(db, run_id, _FAIL_VALIDATION)
            raise ExtractionServiceError(
                _FAIL_VALIDATION,
                f"Evidence length {len(evidence)} exceeds "
                f"maximum {MAX_EVIDENCE_TEXT_LEN}",
            )

        req_text = unit.requirement_text.strip()
        if not req_text or len(req_text) > MAX_REQUIREMENT_TEXT_LEN:
            _mark_run_failed(db, run_id, _FAIL_VALIDATION)
            raise ExtractionServiceError(
                _FAIL_VALIDATION,
                f"requirement_text length {len(req_text)} out of bounds",
            )

        # Duplicate span+text check
        dedup_key = (seq, span_start, span_end, req_text[:100])
        if dedup_key in seen_spans:
            _mark_run_failed(db, run_id, _FAIL_VALIDATION)
            raise ExtractionServiceError(
                _FAIL_VALIDATION,
                f"Duplicate candidate: seq={seq} span=[{span_start}:{span_end}]",
            )
        seen_spans.add(dedup_key)

        # Content policy: neither the candidate text nor the evidence slice may
        # carry markup, links, storage paths, executable fragments, or text
        # shaped like instructions. Evidence must stay a byte-exact slice, so
        # unsafe evidence can only be rejected, never sanitized.
        for field_text in (req_text, evidence):
            reject_code = find_unsafe_content(field_text)
            if reject_code is not None:
                _mark_run_failed(db, run_id, reject_code)
                raise ExtractionServiceError(
                    _FAIL_CONTENT_REJECTED,
                    f"Candidate rejected by content policy: {reject_code}",
                )

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
            status="OPEN",
        )
        task_rows.append(task)

    # Atomic write: all candidates + tasks + run update in one commit
    for c in candidate_rows:
        db.add(c)
    for t in task_rows:
        db.add(t)

    run.status = EXTRACTION_STATUS_COMPLETED
    run.candidate_count = len(candidate_rows)
    run.completed_at = datetime.now(UTC)
    db.commit()
