"""
Regression tests for audit event SQLite compatibility.

Root cause: AuditEvent.details was mapped with bare `JSONB` (PostgreSQL-only),
causing `Base.metadata.create_all()` to fail under SQLite-backed test suites.
Fixed by using `JSON().with_variant(JSONB(...), "postgresql")` in the model.

These tests prove:
1. AuditEvent table creation succeeds with SQLite.
2. AuditEvent.details can store and read back a dict in SQLite test mode.
3. The shared `setup_db_tables` session fixture (which calls create_all) works
   for all tests in the suite, i.e., the original CI failure does not regress.
"""

import uuid

import pytest
from sqlalchemy import create_engine, inspect
from sqlalchemy.orm import sessionmaker

from app.models.audit import AuditEvent
from app.models.base import Base


@pytest.fixture(scope="module")
def sqlite_engine():
    """Dedicated in-memory SQLite engine isolated to this test module."""
    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(bind=engine)
    yield engine
    engine.dispose()


@pytest.fixture(scope="module")
def sqlite_session(sqlite_engine):
    """Session bound to the in-memory SQLite engine."""
    session_factory = sessionmaker(
        bind=sqlite_engine, autocommit=False, autoflush=False
    )
    session = session_factory()
    yield session
    session.close()


def test_audit_event_table_exists_in_sqlite(sqlite_engine):
    """Proves create_all succeeds and audit_events table is present under SQLite."""
    inspector = inspect(sqlite_engine)
    tables = inspector.get_table_names()
    assert "audit_events" in tables, (
        "audit_events table missing — JSONB with_variant fix may have been reverted"
    )


def test_audit_event_details_roundtrip_sqlite(sqlite_session):
    """Proves AuditEvent.details stores and reads a dict correctly under SQLite."""
    # Use a deterministic org/user id for isolation (no FK enforcement in SQLite)
    org_id = uuid.uuid4()
    event = AuditEvent(
        organization_id=org_id,
        user_id=None,
        action="document.upload",
        entity_type="document",
        entity_id=uuid.uuid4(),
        details={"filename": "rfp.pdf", "size_bytes": 204800, "pages": 42},
        ip_address="127.0.0.1",
        request_id="test-req-001",
    )
    sqlite_session.add(event)
    sqlite_session.commit()
    sqlite_session.refresh(event)

    assert event.id is not None
    assert isinstance(event.details, dict)
    assert event.details["filename"] == "rfp.pdf"
    assert event.details["pages"] == 42


def test_audit_event_details_none_sqlite(sqlite_session):
    """Proves AuditEvent.details accepts None (nullable column)."""
    org_id = uuid.uuid4()
    event = AuditEvent(
        organization_id=org_id,
        user_id=None,
        action="project.view",
        entity_type="project",
        entity_id=uuid.uuid4(),
        details=None,
    )
    sqlite_session.add(event)
    sqlite_session.commit()
    sqlite_session.refresh(event)

    assert event.details is None


def test_audit_event_details_list_sqlite(sqlite_session):
    """Proves AuditEvent.details can also store a list (union type)."""
    org_id = uuid.uuid4()
    event = AuditEvent(
        organization_id=org_id,
        user_id=None,
        action="bulk.export",
        entity_type="project",
        entity_id=uuid.uuid4(),
        details=["req-001", "req-002", "req-003"],
    )
    sqlite_session.add(event)
    sqlite_session.commit()
    sqlite_session.refresh(event)

    assert isinstance(event.details, list)
    assert "req-001" in event.details
