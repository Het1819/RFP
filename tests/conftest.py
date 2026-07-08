"""
Shared pytest fixtures for the RFP Architect test suite.

SQLite compatibility notes
--------------------------
GitHub Actions CI runs tests with DATABASE_URL=sqlite:///:memory:.
To make SQLite work correctly with FastAPI's TestClient (which runs the ASGI
app in a separate thread), the engine must use StaticPool so all connections
share the same in-memory database, and check_same_thread must be False.

Without StaticPool:
- Each new connection creates a fresh in-memory database.
- Tables created in the fixture thread are invisible to the TestClient thread.
- Result: 401 Unauthorized on every request (no users/orgs seeded).

The `client` fixture intentionally creates a *new* session per request via
`override_get_db`, rather than sharing the `db` fixture session across threads.
Both sessions bind to the same StaticPool connection, so they share data.
"""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import settings
from app.core.database import get_db
from app.main import app
from app.models.base import Base

# ---------------------------------------------------------------------------
# Engine setup — SQLite-portable, thread-safe for TestClient
# ---------------------------------------------------------------------------
# If DATABASE_URL is SQLite (CI), use StaticPool + check_same_thread=False so
# that the TestClient thread and the test thread share the same in-memory DB.
# If DATABASE_URL is PostgreSQL (local/prod), fall through to normal engine.

_IS_SQLITE = settings.DATABASE_URL.startswith("sqlite")

if _IS_SQLITE:
    test_engine = create_engine(
        # Use sqlite:// (not sqlite:///:memory:) so SQLAlchemy picks up the
        # same database on every connect() call when StaticPool is in use.
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
else:
    test_engine = create_engine(settings.DATABASE_URL)

TestingSessionLocal = sessionmaker(
    autocommit=False,
    autoflush=False,
    bind=test_engine,
)


# ---------------------------------------------------------------------------
# Table setup — session-scoped, runs once for the entire test run
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session", autouse=True)
def setup_db_tables():
    """Create all ORM tables before any test, drop after the session ends."""
    Base.metadata.create_all(bind=test_engine)
    yield
    Base.metadata.drop_all(bind=test_engine)


# ---------------------------------------------------------------------------
# Per-test DB session — transactional rollback for isolation
# ---------------------------------------------------------------------------


@pytest.fixture
def db():
    """Provides a transactional database session rolled back after each test.

    Using a savepoint (nested transaction) rather than a raw rollback so that
    tests can call `db.commit()` internally without destroying the outer
    rollback boundary.
    """
    connection = test_engine.connect()
    transaction = connection.begin()
    session = TestingSessionLocal(bind=connection)

    yield session

    session.close()
    transaction.rollback()
    connection.close()


# ---------------------------------------------------------------------------
# Per-test HTTP client — injects a thread-safe DB override
# ---------------------------------------------------------------------------


@pytest.fixture
def client(db):
    """Provides a test client with get_db overridden to use the test session.

    The override yields the same `db` session as the test itself.  This is
    safe because:
    - StaticPool ensures a single shared in-memory database.
    - check_same_thread=False allows the same SQLite connection to be used
      from both the test thread and the TestClient worker thread.
    - Sharing one session means any db.commit() / db.flush() done in the
      test is immediately visible to the app's route handlers.
    """

    def override_get_db():
        try:
            yield db
        finally:
            pass

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# SessionLocal monkeypatch — routes all app SessionLocal() calls to test db
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def mock_session_local(monkeypatch, db):
    """Route all app.core.database.SessionLocal() calls to the test db session.

    Background helpers, audit loggers, queue processors, and route handlers
    that call SessionLocal() directly will all receive the same `db` session
    as the test itself. This means:
    - Data written by app code is immediately visible in the test's db queries.
    - All writes participate in the same transaction, which rolls back after
      the test to preserve isolation.

    The wrapper class mirrors the SessionLocal() call interface (callable that
    returns a session-like object with a no-op close()).
    """

    class _SessionProxy:
        """Thin proxy so that close() is a no-op (test manages lifecycle)."""

        def __getattr__(self, name: str):
            return getattr(db, name)

        def close(self) -> None:
            pass  # lifecycle managed by the `db` fixture

    def _session_factory() -> _SessionProxy:
        return _SessionProxy()

    monkeypatch.setattr("app.core.database.SessionLocal", _session_factory)
