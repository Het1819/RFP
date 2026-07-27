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

Authentication notes
--------------------
`unauthenticated_client` — bare TestClient with no session. Use for tests that
explicitly verify unauthenticated / redirect behavior.

`client` — authenticated TestClient. Logs in via the real dev login route using
a deterministic seed email so tests do not depend on local .env or AUTH_MODE
state. The fixture requires AUTH_MODE=dev (set in CI env and local .env).
"""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy import event as sa_event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import settings
from app.core.database import get_db
from app.core.sessions.clock import SystemClock
from app.core.sessions.store import InMemorySessionStore
from app.core.sessions.throttling import InMemoryThrottleStore
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

    # pysqlite's DBAPI driver does its own implicit BEGIN/COMMIT handling
    # ("transactional" mode) that fights with SQLAlchemy's SAVEPOINT-based
    # test-isolation pattern used by the `db` fixture below (a real outer
    # transaction plus a SAVEPOINT per `join_transaction_mode="create_savepoint"`).
    # Without this documented workaround, pysqlite silently auto-commits/
    # auto-begins around SAVEPOINT boundaries, which breaks nested-rollback
    # semantics: internal `session.commit()` calls end up committing the
    # *outer* transaction for real, and rows leak across tests. See:
    # https://docs.sqlalchemy.org/en/20/dialects/sqlite.html#serializable-isolation-savepoints-transactional-ddl
    @sa_event.listens_for(test_engine, "connect")
    def _sqlite_disable_pysqlite_transaction_control(dbapi_connection, _record):
        dbapi_connection.isolation_level = None

    @sa_event.listens_for(test_engine, "begin")
    def _sqlite_emit_real_begin(conn):
        conn.exec_driver_sql("BEGIN")
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

    Uses SQLAlchemy 2.0's built-in external-transaction join mode
    (`join_transaction_mode="create_savepoint"`): the connection opens one
    real outer transaction, and the Session binds to it in savepoint mode.
    Each `session.commit()` releases a SAVEPOINT (and SQLAlchemy
    transparently opens a fresh one for the next unit of work) rather than
    committing the outer transaction, so app code (route handlers, service
    functions like `transition()`) can call commit() any number of times
    within one test and every "commit" still nests inside the outer
    transaction. `session.rollback()` similarly only rolls back to the
    SAVEPOINT. The outer transaction is untouched by any of this and is
    unconditionally rolled back at teardown, discarding everything -
    including changes that were "committed" at the Session level - in one
    step.

    This replaces the older SQLAlchemy 1.x recipe (manual `begin_nested()` +
    an `after_transaction_end` event listener that restarts the savepoint);
    per SQLAlchemy 2.0 docs, "event handlers to 'reset' the nested
    transaction are no longer required" once `join_transaction_mode` is used.
    """
    connection = test_engine.connect()
    outer_transaction = connection.begin()
    session = TestingSessionLocal(
        bind=connection, join_transaction_mode="create_savepoint"
    )

    yield session

    session.close()
    outer_transaction.rollback()
    connection.close()


# ---------------------------------------------------------------------------
# Default org/project/user fixture — shared setup for tests exercising
# project- and document-scoped flows (ingestion, evidence, drafting, ...).
# ---------------------------------------------------------------------------


@pytest.fixture
def org_project_user(db):
    """Creates (or reuses) the default org/user and a fresh test project.

    Mirrors the pattern used in tests/integration/test_projects.py:
    `get_default_org_and_user(db)` for the org/user, plus a manually
    constructed `ProposalProject`. Returns the actual ORM objects (not just
    ids) since callers commonly need `.id` off all three.
    """
    from app.core.database import get_default_org_and_user
    from app.models.organization import Organization
    from app.models.project import ProposalProject
    from app.models.user import User

    org_id, user_id = get_default_org_and_user(db)
    org = db.get(Organization, org_id)
    user = db.get(User, user_id)
    project = ProposalProject(
        organization_id=org.id,
        name="Test Project",
        client_name="Test Client",
        created_by_id=user.id,
    )
    db.add(project)
    db.commit()
    db.refresh(project)
    return org, project, user


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


# ---------------------------------------------------------------------------
# Session store isolation — fresh in-memory store/clock per test
# ---------------------------------------------------------------------------
# The app wires a RedisSessionStore into app.state.session_store at import
# time for production. Tests substitute a fresh InMemorySessionStore (same
# typed interface, see app.core.sessions.store) so the full test suite never
# needs a real Redis instance, and so no state leaks between tests. Tests
# that need to control time (idle/absolute expiry) replace
# app.state.session_clock with a FakeClock themselves.


@pytest.fixture
def session_store():
    """Fresh in-memory session store + throttle store, isolated per test."""
    original_store = getattr(app.state, "session_store", None)
    original_clock = getattr(app.state, "session_clock", None)
    original_throttle_store = getattr(app.state, "throttle_store", None)
    store = InMemorySessionStore()
    app.state.session_store = store
    app.state.session_clock = SystemClock()
    app.state.throttle_store = InMemoryThrottleStore()
    yield store
    if original_store is not None:
        app.state.session_store = original_store
    if original_clock is not None:
        app.state.session_clock = original_clock
    if original_throttle_store is not None:
        app.state.throttle_store = original_throttle_store


# ---------------------------------------------------------------------------
# Unauthenticated HTTP client — no session, no login
# ---------------------------------------------------------------------------


@pytest.fixture
def unauthenticated_client(db, session_store):
    """Bare TestClient with no session injection.

    Use for tests that explicitly verify unauthenticated behavior:
    - Protected routes redirect to /login
    - Unauthenticated POST returns 401
    - Inactive/deleted user fails closed
    """

    def override_get_db():
        try:
            yield db
        finally:
            pass

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app, raise_server_exceptions=True) as test_client:
        yield test_client
    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Authenticated HTTP client — logs in via real dev login route
# ---------------------------------------------------------------------------

# Deterministic seed email used by the authenticated client fixture.
# Must match what the dev login route creates (any email works in dev mode).
_TEST_AUTH_EMAIL = "ci-fixture@rfparchitect.com"


@pytest.fixture
def client(db, session_store):
    """Authenticated TestClient.

    Authenticates by hitting the real /login route in AUTH_MODE=dev using a
    deterministic seed email. This is CI-independent: no .env file is read,
    no AUTH_MODE override is needed, and no production auth bypass is used.

    The dev login route (app/web/routes/auth.py) creates the org/user if they
    do not exist, so this fixture works in a fresh in-memory database.

    Requirements:
    - AUTH_MODE must be "dev" (set via CI env var or local .env).
    - APP_ENV must be "test", "development", or "local".

    Usage in tests:
    - `client` — authenticated, access protected pages directly.
    - `unauthenticated_client` — bare, no session.
    """

    def override_get_db():
        try:
            yield db
        finally:
            pass

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app, raise_server_exceptions=True) as test_client:
        # Step 1: GET /login to obtain a CSRF token in the session cookie.
        login_page = test_client.get("/login")
        assert login_page.status_code == 200, (
            f"Login page returned {login_page.status_code}; "
            "check that AUTH_MODE=dev is set."
        )

        # Extract CSRF token from login form.
        import re

        csrf_match = re.search(
            r'name="csrf_token"\s+value="([a-f0-9]+)"', login_page.text
        )
        if not csrf_match:
            csrf_match = re.search(
                r'value="([a-f0-9]+)"\s+name="csrf_token"', login_page.text
            )
        assert csrf_match is not None, (
            "CSRF token not found in login page HTML. "
            "The login template must render a csrf_token field."
        )
        csrf_token = csrf_match.group(1)

        # Step 2: POST /login with dev email + CSRF token.
        login_resp = test_client.post(
            "/login",
            data={"email": _TEST_AUTH_EMAIL, "csrf_token": csrf_token},
            headers={"X-Test-Enforce-CSRF": "true"},
            follow_redirects=False,
        )
        assert login_resp.status_code == 303, (
            f"Dev login returned {login_resp.status_code} "
            f"(location: {login_resp.headers.get('location', 'N/A')}). "
            "Ensure AUTH_MODE=dev and the login route is reachable."
        )
        assert login_resp.headers.get("location") == "/projects", (
            f"Dev login redirected to {login_resp.headers.get('location')} "
            "instead of /projects."
        )

        # Step 3: Verify authenticated access works.
        projects_resp = test_client.get("/projects")
        assert projects_resp.status_code == 200, (
            f"Authenticated /projects returned {projects_resp.status_code}. "
            "Session cookie may not have been set correctly."
        )

        yield test_client

    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Alias for clarity in future tests
# ---------------------------------------------------------------------------

auth_client = client
