"""Unit tests for the validated ingestion state machine.

Document.ingestion_status does not exist as a mapped column yet (it lands
in a follow-up task alongside the rest of the security-metadata columns
and an Alembic migration). Since SQLAlchemy models are plain Python
objects, `transition()` can still read/write `document.ingestion_status`
as an ordinary attribute on an in-memory `Document()` instance - it just
won't persist to a real database column. These tests therefore construct
`Document()` instances directly (no `db.add()` / `db.commit()` on the
document itself) and only use the real `db` fixture/session for the
AuditEvent side effects that `transition()` writes, which map onto
columns that already exist on `AuditEvent` today. `org_id`/`user_id` come
from the real `get_default_org_and_user()` helper (see
tests/integration/test_projects.py for the same pattern), which creates
and commits an Organization/User pair the AuditEvent foreign keys can
reference.
"""

import uuid

import pytest

from app.core.database import get_default_org_and_user
from app.models.audit import AuditEvent
from app.models.document import Document
from app.services.ingestion_state import (
    ALLOWED_TRANSITIONS,
    IngestionStateError,
    IngestionStatus,
    transition,
)


def _make_document(user_id: uuid.UUID) -> Document:
    """Build an in-memory-only Document (never added/committed to `db`).

    ingestion_status is set as a plain attribute since the mapped column
    does not exist yet; that is exactly what this test suite is meant to
    exercise against.
    """
    doc = Document(
        id=uuid.uuid4(),
        project_id=uuid.uuid4(),
        name="test.pdf",
        file_path="/data/storage/documents/x.pdf",
        file_type="application/pdf",
        created_by_id=user_id,
    )
    doc.ingestion_status = IngestionStatus.QUARANTINED
    return doc


class TestTransitionValidity:
    def test_valid_transition_quarantined_to_validating(self, db) -> None:
        org_id, user_id = get_default_org_and_user(db)
        doc = _make_document(user_id)
        transition(db, doc, IngestionStatus.VALIDATING, org_id=org_id, user_id=user_id)
        assert doc.ingestion_status == IngestionStatus.VALIDATING

    def test_invalid_transition_rejected(self, db) -> None:
        org_id, user_id = get_default_org_and_user(db)
        doc = _make_document(user_id)
        with pytest.raises(IngestionStateError):
            transition(
                db, doc, IngestionStatus.COMPLETED, org_id=org_id, user_id=user_id
            )
        assert doc.ingestion_status == IngestionStatus.QUARANTINED  # unchanged

    def test_terminal_states_have_no_outgoing_transitions(self) -> None:
        for terminal in (
            IngestionStatus.REJECTED_TYPE,
            IngestionStatus.REJECTED_MALWARE,
            IngestionStatus.REJECTED_CONTENT_POLICY,
            IngestionStatus.COMPLETED,
        ):
            assert ALLOWED_TRANSITIONS[terminal] == frozenset()

    def test_same_state_transition_is_idempotent_noop(self, db) -> None:
        org_id, user_id = get_default_org_and_user(db)
        doc = _make_document(user_id)
        before_count = (
            db.query(AuditEvent)
            .filter_by(entity_id=doc.id, action="document_ingestion_transition")
            .count()
        )
        transition(
            db,
            doc,
            IngestionStatus.QUARANTINED,
            org_id=org_id,
            user_id=user_id,
        )
        after_count = (
            db.query(AuditEvent)
            .filter_by(entity_id=doc.id, action="document_ingestion_transition")
            .count()
        )
        assert doc.ingestion_status == IngestionStatus.QUARANTINED
        assert after_count == before_count  # no duplicate audit event
        assert after_count == 0

    def test_transition_writes_audit_event(self, db) -> None:
        org_id, user_id = get_default_org_and_user(db)
        doc = _make_document(user_id)
        transition(db, doc, IngestionStatus.VALIDATING, org_id=org_id, user_id=user_id)
        event = (
            db.query(AuditEvent)
            .filter_by(entity_id=doc.id, action="document_ingestion_transition")
            .one()
        )
        assert event.details["from"] == IngestionStatus.QUARANTINED
        assert event.details["to"] == IngestionStatus.VALIDATING

    def test_rejection_transition_records_reason_and_summary(self, db) -> None:
        org_id, user_id = get_default_org_and_user(db)
        doc = _make_document(user_id)
        transition(db, doc, IngestionStatus.VALIDATING, org_id=org_id, user_id=user_id)
        transition(
            db,
            doc,
            IngestionStatus.REJECTED_TYPE,
            org_id=org_id,
            user_id=user_id,
            reason_code="MIME_EXTENSION_MISMATCH",
            safe_summary="Uploaded file does not match a supported PDF or DOCX format.",
        )
        assert doc.ingestion_status == IngestionStatus.REJECTED_TYPE
        assert doc.rejection_reason_code == "MIME_EXTENSION_MISMATCH"
        assert doc.operator_failure_summary == (
            "Uploaded file does not match a supported PDF or DOCX format."
        )

    def test_legacy_unverified_can_only_reenter_validating(self) -> None:
        assert ALLOWED_TRANSITIONS[IngestionStatus.LEGACY_UNVERIFIED] == frozenset(
            {IngestionStatus.VALIDATING}
        )
