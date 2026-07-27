"""Shared quarantine-first ingestion orchestration for both RFP and
knowledge-document uploads.

Sequence: stream to quarantine -> create Document(ingestion_status=
QUARANTINED) -> transition to VALIDATING -> run candidate-type detection
-> transition to SCANNING (success) or REJECTED_TYPE (failure). This
phase (A5b) never transitions past SCANNING - no malware scan, no clean
promotion, no parsing, and no legacy processing job (`enqueue_job` /
`ProcessingJob`) is ever created for a document that goes through this
function.

Failure handling is fail-closed throughout: if quarantine write fails,
nothing is persisted. If the initial Document commit fails, the
quarantine file that was already written is deleted before the error
propagates. If candidate-type detection raises an unexpected exception
(rather than returning a normal UNKNOWN result), the document is routed
to REJECTED_TYPE with a generic, safe reason rather than being left
stuck mid-pipeline or silently promoted to SCANNING.
"""

import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi import HTTPException, UploadFile
from sqlalchemy.orm import Session

from app.models.document import Document
from app.models.project import ProposalProject
from app.services.document_type_detection import (
    DetectedType,
    DetectionResult,
    detect_docx_candidate,
    detect_pdf_candidate,
)
from app.services.ingestion_state import IngestionStatus, transition
from app.services.quarantine_storage import (
    QuarantineStorageError,
    delete_quarantine_file,
    normalize_display_filename,
    stream_upload_to_quarantine,
)

_SAFE_UPLOAD_FAILURE_MESSAGE = (
    "The document could not be accepted. Please upload a valid PDF or DOCX file."
)
_DETECTION_ERROR_REASON_CODE = "DETECTION_ERROR"
_UNSUPPORTED_EXTENSION_REASON_CODE = "UNSUPPORTED_EXTENSION"


def ingest_uploaded_document(
    db: Session,
    *,
    project: ProposalProject,
    org_id: uuid.UUID,
    user_id: uuid.UUID,
    upload: UploadFile,
    doc_role: str,
    **role_metadata: Any,
) -> Document:
    """Stream `upload` into quarantine storage, create its `Document` row,
    and run it through candidate-type detection.

    Returns the `Document`, which will be in ingestion_status SCANNING
    (detection succeeded) or REJECTED_TYPE (detection failed or raised
    unexpectedly) by the time this function returns. Never enqueues a
    processing job. `role_metadata` is forwarded directly to the
    `Document(...)` constructor (e.g. `owner_name`, `tags`,
    `approval_status`, `version`, `review_date` for knowledge documents);
    callers must only pass keys that are real `Document` columns.
    """
    display_filename = normalize_display_filename(upload.filename)

    try:
        write_result = stream_upload_to_quarantine(upload)
    except QuarantineStorageError as exc:
        raise HTTPException(
            status_code=400, detail=_SAFE_UPLOAD_FAILURE_MESSAGE
        ) from exc

    doc = Document(
        project_id=project.id,
        name=display_filename,
        display_filename=display_filename,
        file_path=str(write_result.storage_path),
        file_type=upload.content_type or "application/octet-stream",
        doc_role=doc_role,
        ingestion_status=IngestionStatus.QUARANTINED,
        sha256_digest=write_result.sha256_digest,
        file_size_bytes=write_result.byte_size,
        quarantined_at=datetime.now(UTC),
        processing_status="pending_security_scan",
        created_by_id=user_id,
        **role_metadata,
    )
    db.add(doc)
    try:
        db.commit()
    except Exception:
        db.rollback()
        delete_quarantine_file(write_result.storage_id)
        raise
    db.refresh(doc)

    from app.services.project_service import log_audit_event

    log_audit_event(
        db,
        org_id=org_id,
        user_id=user_id,
        action="document_upload_quarantined",
        entity_type="Document",
        entity_id=doc.id,
        details={"doc_role": doc_role},
    )

    # From this point on, `doc` is a persisted row that has left the
    # initial QUARANTINED state. Every subsequent transition() call
    # commits internally, so failures here must be handled explicitly
    # rather than relying on the caller to roll anything back.
    transition(db, doc, IngestionStatus.VALIDATING, org_id=org_id, user_id=user_id)

    declared_extension = _extension_of(display_filename)

    try:
        detection = _run_detection(declared_extension, write_result.storage_path)
    except Exception:
        # Fail closed: an unexpected exception from detection must never
        # leave the document silently stuck in VALIDATING or promote it
        # to SCANNING. Route it to the terminal REJECTED_TYPE state with
        # a generic, safe operator summary.
        transition(
            db,
            doc,
            IngestionStatus.REJECTED_TYPE,
            org_id=org_id,
            user_id=user_id,
            reason_code=_DETECTION_ERROR_REASON_CODE,
            safe_summary=_SAFE_UPLOAD_FAILURE_MESSAGE,
        )
        return doc

    if detection is not None and detection.detected_type != DetectedType.UNKNOWN:
        doc.detected_content_type = detection.canonical_mime
        db.commit()
        transition(db, doc, IngestionStatus.SCANNING, org_id=org_id, user_id=user_id)

        from app.core.queue import enqueue_scan_job

        enqueue_scan_job(doc.id)
    else:
        reason = (
            detection.reason_code if detection else _UNSUPPORTED_EXTENSION_REASON_CODE
        )
        transition(
            db,
            doc,
            IngestionStatus.REJECTED_TYPE,
            org_id=org_id,
            user_id=user_id,
            reason_code=reason,
            safe_summary=_SAFE_UPLOAD_FAILURE_MESSAGE,
        )

    return doc


def _run_detection(
    declared_extension: str, storage_path: Any
) -> DetectionResult | None:
    """Dispatch to the appropriate candidate-type detector for a declared
    extension, or return None if the extension is not one we can classify
    at all (neither .pdf nor .docx)."""
    if declared_extension == ".pdf":
        return detect_pdf_candidate(storage_path, declared_extension=declared_extension)
    if declared_extension == ".docx":
        return detect_docx_candidate(
            storage_path, declared_extension=declared_extension
        )
    return None


def _extension_of(display_filename: str) -> str:
    idx = display_filename.rfind(".")
    return display_filename[idx:].lower() if idx != -1 else ""
