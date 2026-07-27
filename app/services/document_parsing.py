"""Orchestration service for document parsing lifecycle and atomic page persistence."""

import logging
import uuid
from datetime import UTC, datetime

from sqlalchemy import delete
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import SessionLocal
from app.models.document import Document, DocumentPage
from app.models.project import ProposalProject
from app.parser_service.contracts import ParserResponse
from app.services.ingestion_state import IngestionStatus, transition
from app.services.parser_client import (
    ParserClientError,
    send_document_for_parsing,
)

logger = logging.getLogger(__name__)

# Transient error codes eligible for bounded backoff retry
TRANSIENT_PARSE_ERRORS = frozenset(
    {
        "PARSER_UNAVAILABLE",
        "PARSER_TIMEOUT",
    }
)


class ParseAttemptSnapshot:
    def __init__(
        self,
        document_id: uuid.UUID,
        org_id: uuid.UUID,
        user_id: uuid.UUID,
        clean_identifier: str,
        detected_mime: str,
        sha256_digest: str,
        file_size_bytes: int,
        parse_attempt_id: str,
        attempt_count: int,
    ) -> None:
        self.document_id = document_id
        self.org_id = org_id
        self.user_id = user_id
        self.clean_identifier = clean_identifier
        self.detected_mime = detected_mime
        self.sha256_digest = sha256_digest
        self.file_size_bytes = file_size_bytes
        self.parse_attempt_id = parse_attempt_id
        self.attempt_count = attempt_count


def prepare_parse_attempt(
    db: Session, document_id: uuid.UUID
) -> ParseAttemptSnapshot | None:
    """Phase A: Lock document row, verify CLEAN/PARSE_FAILED,
    and transition to PARSING.
    """
    doc = (
        db.query(Document).filter(Document.id == document_id).with_for_update().first()
    )
    if not doc:
        return None

    project = db.get(ProposalProject, doc.project_id)
    if not project:
        return None

    org_id = project.organization_id
    user_id = doc.created_by_id

    # Idempotency & state checks
    if doc.ingestion_status == IngestionStatus.COMPLETED:
        logger.info(
            "prepare_parse_attempt: document %s is already COMPLETED - skipping parse",
            document_id,
        )
        return None

    if doc.ingestion_status not in (
        IngestionStatus.CLEAN,
        IngestionStatus.PARSE_FAILED,
    ):
        logger.warning(
            "prepare_parse_attempt: document %s in status %s cannot be parsed",
            document_id,
            doc.ingestion_status,
        )
        return None

    max_attempts = getattr(settings, "PARSE_MAX_ATTEMPTS", 3)
    if doc.parse_attempt_count >= max_attempts:
        logger.warning(
            "prepare_parse_attempt: document %s reached max parse attempts (%d)",
            document_id,
            doc.parse_attempt_count,
        )
        return None

    clean_identifier = doc.clean_storage_identifier or doc.file_path
    if not clean_identifier or not doc.sha256_digest or not doc.file_size_bytes:
        logger.error(
            "prepare_parse_attempt: document %s missing clean metadata", document_id
        )
        return None

    attempt_id = uuid.uuid4().hex
    doc.parse_started_at = datetime.now(UTC)
    doc.parse_attempt_count += 1
    doc.parse_attempt_id = attempt_id

    audit_info = {
        "parse_attempt_id": attempt_id,
        "attempt": doc.parse_attempt_count,
    }
    transition(
        db,
        doc,
        IngestionStatus.PARSING,
        org_id=org_id,
        user_id=user_id,
        audit_detail=audit_info,
    )

    db.commit()

    return ParseAttemptSnapshot(
        document_id=doc.id,
        org_id=org_id,
        user_id=user_id,
        clean_identifier=clean_identifier,
        detected_mime=doc.detected_content_type or doc.file_type,
        sha256_digest=doc.sha256_digest,
        file_size_bytes=doc.file_size_bytes,
        parse_attempt_id=attempt_id,
        attempt_count=doc.parse_attempt_count,
    )


def persist_parse_results(
    db: Session,
    snapshot: ParseAttemptSnapshot,
    parser_response: ParserResponse,
) -> None:
    """Phase C: Atomically delete old pages, insert new units,
    and transition to COMPLETED.
    """
    doc = (
        db.query(Document)
        .filter(Document.id == snapshot.document_id)
        .with_for_update()
        .first()
    )
    if not doc:
        return

    # Concurrency guard: verify attempt token matches
    if (
        doc.ingestion_status != IngestionStatus.PARSING
        or doc.parse_attempt_id != snapshot.parse_attempt_id
    ):
        logger.warning(
            "persist_parse_results: stale parse attempt token for document %s",
            snapshot.document_id,
        )
        return

    # Verify metadata snapshots have not drifted
    current_clean_id = doc.clean_storage_identifier or doc.file_path
    if (
        current_clean_id != snapshot.clean_identifier
        or doc.sha256_digest != snapshot.sha256_digest
    ):
        logger.error(
            "persist_parse_results: document %s metadata drifted during parsing",
            snapshot.document_id,
        )
        fail_parse_attempt(
            db,
            snapshot,
            error_code="PARSER_INPUT_CHANGED",
            summary="Clean document metadata changed during parsing",
        )
        return

    # Atomic page replacement: delete old pages, insert new units
    db.execute(delete(DocumentPage).where(DocumentPage.document_id == doc.id))

    new_pages: list[DocumentPage] = []
    for unit in parser_response.units:
        page = DocumentPage(
            document_id=doc.id,
            page_number=unit.sequence,
            content=unit.content,
            unit_kind=unit.unit_kind,
            source_locator=unit.source_locator,
            content_sha256=unit.content_sha256,
        )
        new_pages.append(page)

    db.add_all(new_pages)

    doc.parser_version = parser_response.parser_version
    doc.parser_completed_at = datetime.now(UTC)
    doc.parse_error_code = None

    transition(
        db,
        doc,
        IngestionStatus.COMPLETED,
        org_id=snapshot.org_id,
        user_id=snapshot.user_id,
        audit_detail={
            "total_units": parser_response.total_units,
            "total_characters": parser_response.total_characters,
        },
    )

    db.commit()


def fail_parse_attempt(
    db: Session,
    snapshot: ParseAttemptSnapshot,
    error_code: str,
    summary: str,
) -> None:
    """Record parse failure in DB and transition to PARSE_FAILED."""
    doc = (
        db.query(Document)
        .filter(Document.id == snapshot.document_id)
        .with_for_update()
        .first()
    )
    if not doc:
        return

    if (
        doc.ingestion_status != IngestionStatus.PARSING
        or doc.parse_attempt_id != snapshot.parse_attempt_id
    ):
        return

    doc.parse_error_code = error_code
    doc.operator_failure_summary = summary

    transition(
        db,
        doc,
        IngestionStatus.PARSE_FAILED,
        org_id=snapshot.org_id,
        user_id=snapshot.user_id,
        reason_code=error_code,
        audit_detail={
            "error_code": error_code,
            "parse_attempt_id": snapshot.parse_attempt_id,
        },
    )

    db.commit()


async def run_parse_pipeline_async(document_id: uuid.UUID) -> None:
    """Execute complete 3-phase parse pipeline for document_id."""
    # Phase A: Begin attempt (DB Tx 1)
    db = SessionLocal()
    try:
        snapshot = prepare_parse_attempt(db, document_id)
    finally:
        db.close()

    if not snapshot:
        return

    # Phase B: Remote parser call (No DB Tx)
    request_id = f"req-{snapshot.parse_attempt_id[:8]}"
    try:
        response = await send_document_for_parsing(
            clean_identifier=snapshot.clean_identifier,
            detected_mime=snapshot.detected_mime,
            expected_sha256=snapshot.sha256_digest,
            expected_size=snapshot.file_size_bytes,
            request_id=request_id,
        )
    except ParserClientError as err:
        db_err = SessionLocal()
        try:
            fail_parse_attempt(db_err, snapshot, err.code, err.message)
            if err.code in TRANSIENT_PARSE_ERRORS and snapshot.attempt_count < getattr(
                settings, "PARSE_MAX_ATTEMPTS", 3
            ):
                from app.core.queue import enqueue_parse_retry

                enqueue_parse_retry(document_id, attempt=snapshot.attempt_count + 1)
        finally:
            db_err.close()
        return
    except Exception as err:
        logger.exception("Unexpected error during parsing call for %s", document_id)
        db_err = SessionLocal()
        try:
            fail_parse_attempt(
                db_err,
                snapshot,
                "PARSER_PERSISTENCE_FAILED",
                f"Unexpected exception: {type(err).__name__}",
            )
        finally:
            db_err.close()
        return

    # Phase C: Atomic completion (DB Tx 2)
    db_comp = SessionLocal()
    try:
        persist_parse_results(db_comp, snapshot, response)
    except Exception as err:
        logger.exception("Failed to persist parse results for %s", document_id)
        fail_parse_attempt(
            db_comp,
            snapshot,
            "PARSER_PERSISTENCE_FAILED",
            f"Database persistence error: {err}",
        )
    finally:
        db_comp.close()
