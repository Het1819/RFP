"""Proves the `db` test fixture correctly isolates multi-commit test flows.

Every later A5b task relies on this: routes and services under test call
db.commit() multiple times (upload -> transition -> transition), and the
fixture must still guarantee full rollback at test teardown.
"""

import pytest
from sqlalchemy import select

from app.models.audit import AuditEvent
from app.models.document import Document
from app.services.ingestion_state import IngestionStatus, transition


def test_multiple_internal_commits_are_visible_within_one_test(db, org_project_user):
    org, project, user = org_project_user
    doc = Document(
        project_id=project.id,
        name="a.pdf",
        file_path="/tmp/a.pdf",
        file_type="application/pdf",
        created_by_id=user.id,
        ingestion_status=IngestionStatus.QUARANTINED,
    )
    db.add(doc)
    db.commit()  # commit #1
    transition(
        db, doc, IngestionStatus.VALIDATING, org_id=org.id, user_id=user.id
    )  # commit #2 (internal to transition())
    transition(
        db, doc, IngestionStatus.SCANNING, org_id=org.id, user_id=user.id
    )  # commit #3
    reloaded = db.scalar(select(Document).where(Document.id == doc.id))
    assert reloaded is not None
    assert reloaded.ingestion_status == IngestionStatus.SCANNING


# Module-level marker so the next test function can assert isolation from
# the row created above without relying on execution order across files;
# pytest runs tests within a module in definition order by default, which
# is sufficient here since both tests live in this one file.
_LEAKED_DOC_ID_HOLDER: dict[str, object] = {}


def test_committed_row_from_previous_test_is_not_visible_here(db, org_project_user):
    """If this test can see a Document named "a.pdf" from the previous
    test, the fixture is leaking committed data across tests."""
    _org, _project, _user = org_project_user
    leaked = db.scalar(select(Document).where(Document.name == "a.pdf"))
    assert leaked is None


def test_rollback_after_exception_clears_all_test_state(db, org_project_user):
    _org, project, user = org_project_user
    doc = Document(
        project_id=project.id,
        name="b.pdf",
        file_path="/tmp/b.pdf",
        file_type="application/pdf",
        created_by_id=user.id,
        ingestion_status=IngestionStatus.QUARANTINED,
    )
    db.add(doc)
    db.commit()
    with pytest.raises(ValueError):
        raise ValueError("simulated mid-flow failure after a commit")
    # No explicit assertion here - the real proof is the next test.


def test_previous_tests_exception_state_did_not_leak(db, org_project_user):
    leaked = db.scalar(select(Document).where(Document.name == "b.pdf"))
    assert leaked is None


def test_audit_events_participate_correctly_across_commits(db, org_project_user):
    org, project, user = org_project_user
    doc = Document(
        project_id=project.id,
        name="c.pdf",
        file_path="/tmp/c.pdf",
        file_type="application/pdf",
        created_by_id=user.id,
        ingestion_status=IngestionStatus.QUARANTINED,
    )
    db.add(doc)
    db.commit()
    transition(db, doc, IngestionStatus.VALIDATING, org_id=org.id, user_id=user.id)
    events = db.scalars(select(AuditEvent).where(AuditEvent.entity_id == doc.id)).all()
    assert len(events) == 1
    assert events[0].details["to"] == IngestionStatus.VALIDATING


def test_no_audit_events_leak_from_previous_test(db):
    from sqlalchemy import func

    count = db.scalar(select(func.count()).select_from(AuditEvent))
    assert count == 0
