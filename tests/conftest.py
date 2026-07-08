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
# Unauthenticated HTTP client — no session, no login
# ---------------------------------------------------------------------------


@pytest.fixture
def unauthenticated_client(db):
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
def client(db):
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
