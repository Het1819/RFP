"""Validated ingestion lifecycle for uploaded documents.

Document.ingestion_status must never be set directly by routes, worker
tasks, or templates - always call transition(). This is the single
enforcement point for the security invariants in AGENTS.md / A5 spec
section 3: quarantined files cannot be downloaded, approved, retrieved,
sent to the LLM, or parsed by the legacy in-process parser; only files
that pass structural validation, detected-type validation, malware
scanning, and content-policy inspection may reach CLEAN; only CLEAN
files may be parsed; only successfully parsed documents may reach
COMPLETED and enter requirement extraction.

Note: as of this module's introduction, app.models.document.Document does
not yet declare an `ingestion_status` mapped column (that lands in a
follow-up task that adds the security-metadata migration). transition()
still reads/writes `document.ingestion_status` as a plain attribute -
this works fine on any Python object, mapped or not, it just means the
value will not persist to a real database column until that column
exists on the model.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.orm import Session

from app.core.observability import request_id_var
from app.models.audit import AuditEvent
from app.models.document import Document


class IngestionStatus:
    QUARANTINED = "QUARANTINED"
    VALIDATING = "VALIDATING"
    SCANNING = "SCANNING"
    SCAN_RETRY_PENDING = "SCAN_RETRY_PENDING"
    REJECTED_TYPE = "REJECTED_TYPE"
    REJECTED_MALWARE = "REJECTED_MALWARE"
    REJECTED_CONTENT_POLICY = "REJECTED_CONTENT_POLICY"
    CLEAN = "CLEAN"
    PARSING = "PARSING"
    PARSE_FAILED = "PARSE_FAILED"
    COMPLETED = "COMPLETED"
    LEGACY_UNVERIFIED = "LEGACY_UNVERIFIED"

    ALL = frozenset(
        {
            QUARANTINED,
            VALIDATING,
            SCANNING,
            SCAN_RETRY_PENDING,
            REJECTED_TYPE,
            REJECTED_MALWARE,
            REJECTED_CONTENT_POLICY,
            CLEAN,
            PARSING,
            PARSE_FAILED,
            COMPLETED,
            LEGACY_UNVERIFIED,
        }
    )


# Content-policy inspection (A5 spec sections 7-8) runs as part of the
# SCANNING phase, between a clean malware result and promotion to CLEAN -
# it is not a separate persisted state, so both REJECTED_MALWARE and
# REJECTED_CONTENT_POLICY are reachable directly from SCANNING.
ALLOWED_TRANSITIONS: dict[str, frozenset[str]] = {
    IngestionStatus.QUARANTINED: frozenset({IngestionStatus.VALIDATING}),
    IngestionStatus.VALIDATING: frozenset(
        {IngestionStatus.SCANNING, IngestionStatus.REJECTED_TYPE}
    ),
    IngestionStatus.SCANNING: frozenset(
        {
            IngestionStatus.CLEAN,
            IngestionStatus.REJECTED_MALWARE,
            IngestionStatus.REJECTED_CONTENT_POLICY,
            IngestionStatus.SCAN_RETRY_PENDING,
        }
    ),
    IngestionStatus.SCAN_RETRY_PENDING: frozenset({IngestionStatus.SCANNING}),
    IngestionStatus.REJECTED_TYPE: frozenset(),
    IngestionStatus.REJECTED_MALWARE: frozenset(),
    IngestionStatus.REJECTED_CONTENT_POLICY: frozenset(),
    IngestionStatus.CLEAN: frozenset({IngestionStatus.PARSING}),
    IngestionStatus.PARSING: frozenset(
        {IngestionStatus.COMPLETED, IngestionStatus.PARSE_FAILED}
    ),
    IngestionStatus.PARSE_FAILED: frozenset({IngestionStatus.PARSING}),
    IngestionStatus.COMPLETED: frozenset(),
    IngestionStatus.LEGACY_UNVERIFIED: frozenset({IngestionStatus.VALIDATING}),
}


class IngestionStateError(Exception):
    """Raised when an ingestion-status transition is not permitted."""


def transition(
    db: Session,
    document: Document,
    new_status: str,
    *,
    org_id: uuid.UUID,
    user_id: uuid.UUID | None,
    reason_code: str | None = None,
    safe_summary: str | None = None,
) -> None:
    """Validate and apply an ingestion-status transition on `document`.

    Mutates `document.ingestion_status` (and `rejection_reason_code` /
    `operator_failure_summary` when provided), writes an AuditEvent
    recording the transition, and commits. Same-state calls are a no-op
    (idempotent) and do not write a duplicate audit event. Raises
    IngestionStateError, leaving `document` unmutated, if `new_status` is
    unknown or not reachable from the document's current status.
    """
    if new_status not in IngestionStatus.ALL:
        raise IngestionStateError(f"Unknown ingestion status: {new_status!r}")

    # NOTE: Document does not declare ingestion_status / rejection_reason_code /
    # operator_failure_summary as mapped columns yet (pending a follow-up
    # migration + model task). These are plain attribute reads/writes on the
    # Document instance until that lands, hence the attr-defined ignores.
    current = document.ingestion_status  # type: ignore[attr-defined]
    if current == new_status:
        return  # idempotent no-op, no duplicate audit event

    allowed = ALLOWED_TRANSITIONS.get(current, frozenset())
    if new_status not in allowed:
        raise IngestionStateError(
            f"Illegal ingestion transition: {current} -> {new_status}"
        )

    document.ingestion_status = new_status  # type: ignore[attr-defined]
    if reason_code is not None:
        document.rejection_reason_code = reason_code  # type: ignore[attr-defined]
    if safe_summary is not None:
        document.operator_failure_summary = safe_summary  # type: ignore[attr-defined]

    details: dict[str, Any] = {"from": current, "to": new_status}
    if reason_code is not None:
        details["reason_code"] = reason_code

    db.add(
        AuditEvent(
            organization_id=org_id,
            user_id=user_id,
            action="document_ingestion_transition",
            entity_type="document",
            entity_id=document.id,
            details=details,
            request_id=request_id_var.get(),
        )
    )
    db.commit()
