"""Unit tests for the validated ingestion state machine.

Document.ingestion_status is a real mapped column (added in A5a task 3,
alongside the rest of the security-metadata columns and an Alembic
migration). Most tests in this module exercise `transition()`'s
validation and audit-write logic against in-memory-only `Document()`
instances (no `db.add()` / `db.commit()` on the document itself), using
the real `db` fixture/session only for the AuditEvent side effects that
`transition()` writes. `TestPersistedRoundTrip` below additionally
persists a real `Document` row and confirms `transition()` writes
through to the actual database column, not just an in-memory attribute.
`org_id`/`user_id` come from the real `get_default_org_and_user()`
helper (see tests/integration/test_projects.py for the same pattern),
which creates and commits an Organization/User pair the AuditEvent
foreign keys can reference.
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

    ingestion_status is set directly on the real mapped column but the
    instance itself is never persisted; this exercises `transition()`'s
    validation and audit-write logic without needing a Document row.
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


class TestPersistedRoundTrip:
    def test_transition_persists_through_real_ingestion_status_column(self, db) -> None:
        """Persist a real Document row, call transition() on it, then
        re-fetch it from the database and confirm ingestion_status
        actually round-tripped through the real mapped column - not
        just an in-memory attribute."""
        from app.models.project import ProposalProject

        org_id, user_id = get_default_org_and_user(db)

        project = ProposalProject(
            organization_id=org_id,
            created_by_id=user_id,
            name="Ingestion state round-trip test project",
            client_name="Acme Corp",
            status="draft",
        )
        db.add(project)
        db.commit()

        doc = Document(
            project_id=project.id,
            name="test.pdf",
            file_path="/data/storage/quarantine/x.pdf",
            file_type="application/pdf",
            created_by_id=user_id,
            ingestion_status=IngestionStatus.QUARANTINED,
        )
        db.add(doc)
        db.commit()

        transition(db, doc, IngestionStatus.VALIDATING, org_id=org_id, user_id=user_id)

        db.refresh(doc)
        assert doc.ingestion_status == IngestionStatus.VALIDATING

        refetched = db.get(Document, doc.id)
        assert refetched is not None
        assert refetched.ingestion_status == IngestionStatus.VALIDATING
