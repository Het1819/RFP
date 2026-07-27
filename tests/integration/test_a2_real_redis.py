"""Real-Redis integration test for Phase A2 (spec section 14, step 15).

Skipped automatically unless a Redis instance is reachable at REDIS_URL (or
SESSION_REDIS_URL) -- CI/dev machines without Redis running should not fail
the rest of the suite over this one test. Run locally with:

    docker run -d --name rfp-redis-a2-test -p 6379:6379 redis:7-alpine
    APP_ENV=test AUTH_MODE=dev DATABASE_URL=sqlite:///:memory: \
        REDIS_URL=redis://localhost:6379/0 \
        uv run pytest tests/integration/test_a2_real_redis.py -v

Exercises the full lifecycle against the actual RedisSessionStore
implementation (not the in-memory test double used elsewhere): create
session, authenticate, access a protected route, revoke, confirm access
fails, then simulate Redis being unreachable and confirm readiness and
protected-route requests fail closed.
"""

import asyncio

import pytest
from fastapi.testclient import TestClient

from app.core.config import settings
from app.core.database import get_db
from app.core.sessions.store import RedisSessionStore
from app.core.sessions.throttling import RedisThrottleStore
from app.main import app
from app.models.organization import Organization
from app.models.user import User
from tests.integration.test_csrf import extract_csrf_token

_PASSWORD = "correct-horse-battery-staple"
COOKIE_NAME = "rfp_session"


def _redis_available() -> bool:
    async def _check() -> bool:
        try:
            store = RedisSessionStore(settings.effective_session_redis_url)
            return await store.ping()
        except Exception:
            return False

    try:
        return asyncio.run(_check())
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not _redis_available(),
    reason="Real Redis not reachable at REDIS_URL; skipping live integration test.",
)


def _create_active_user(db, email: str):
    from app.core.passwords import hash_password

    org = Organization(name=f"Org for {email}")
    db.add(org)
    db.commit()
    db.refresh(org)

    user = User(
        organization_id=org.id,
        email=email,
        hashed_password=hash_password(_PASSWORD),
        full_name="Real Redis Test User",
        is_active=True,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return org, user


@pytest.fixture
def real_redis_client(db, monkeypatch):
    """TestClient wired to a genuine RedisSessionStore (not the in-memory
    double the rest of the suite uses), talking to REDIS_URL."""
    monkeypatch.setattr(settings, "AUTH_MODE", "session")

    # Fresh store instances per test: redis.asyncio connections bind to the
    # event loop of their first use, and each TestClient block below runs
    # its own loop. Reusing a store (or the module-level singletons set up
    # at app import) across tests/loops causes "attached to a different
    # loop" failures -- this mirrors real request-scoped connection
    # lifecycles closely enough for an integration test.
    real_store = RedisSessionStore(settings.effective_session_redis_url)
    real_throttle_store = RedisThrottleStore(settings.effective_session_redis_url)
    original_store = app.state.session_store
    original_throttle_store = app.state.throttle_store
    app.state.session_store = real_store
    app.state.throttle_store = real_throttle_store

    def override_get_db():
        yield db

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app, raise_server_exceptions=True) as test_client:
        yield test_client, real_store
    app.dependency_overrides.clear()
    app.state.session_store = original_store
    app.state.throttle_store = original_throttle_store


def test_full_lifecycle_against_real_redis(real_redis_client, db):
    client, _store = real_redis_client
    org, user = _create_active_user(db, "real-redis@rfparchitect.com")

    # 1. Create session (anonymous, CSRF-bearing) against real Redis.
    login_page = client.get("/login")
    assert login_page.status_code == 200
    anon_cookie = client.cookies.get(COOKIE_NAME)
    assert anon_cookie is not None

    # 2. Authenticate.
    csrf_token = extract_csrf_token(login_page.text)
    login_resp = client.post(
        "/login",
        data={
            "csrf_token": csrf_token,
            "email": "real-redis@rfparchitect.com",
            "password": _PASSWORD,
        },
        headers={"X-Test-Enforce-CSRF": "true"},
        follow_redirects=False,
    )
    assert login_resp.headers["location"] == "/projects"
    authed_cookie = client.cookies.get(COOKIE_NAME)
    assert authed_cookie != anon_cookie  # rotated on real Redis too

    # 3. Access a protected route.
    assert client.get("/projects").status_code == 200

    # 4. Revoke the session (simulating admin revocation / logout at the
    # store level, directly against real Redis). Uses its own freshly
    # connected RedisSessionStore -- asyncio.run() opens a new event loop,
    # and redis.asyncio connections are bound to the loop they were first
    # used on, so reusing `store` (already bound to the TestClient's
    # internal loop) here would cross loops and break the connection.
    verify_store = RedisSessionStore(settings.effective_session_redis_url)
    revoked = asyncio.run(verify_store.revoke_all_for_user(str(user.id)))
    assert revoked == 1

    # 5. Confirm access now fails.
    resp = client.get(
        "/projects", headers={"accept": "text/html"}, follow_redirects=False
    )
    assert resp.status_code == 303
    assert resp.headers["location"] == "/login"

    # Clean up: nothing else should remain indexed for this user.
    verify_store_2 = RedisSessionStore(settings.effective_session_redis_url)
    remaining = asyncio.run(verify_store_2.count_for_user(str(user.id)))
    assert remaining == 0

    _ = org  # keep referenced for readability of the fixture chain


def test_readyz_and_protected_routes_fail_when_redis_stopped(real_redis_client, db):
    """Confirms fail-closed behavior against a real store instance whose
    underlying connection is forced unavailable -- mirrors "stop Redis" from
    the spec without requiring this test process to control the Docker
    container's lifecycle directly."""
    client, store = real_redis_client
    _create_active_user(db, "real-redis-outage@rfparchitect.com")

    login_page = client.get("/login")
    csrf_token = extract_csrf_token(login_page.text)
    client.post(
        "/login",
        data={
            "csrf_token": csrf_token,
            "email": "real-redis-outage@rfparchitect.com",
            "password": _PASSWORD,
        },
        headers={"X-Test-Enforce-CSRF": "true"},
        follow_redirects=False,
    )
    assert client.get("/projects").status_code == 200

    # Point the store at an unreachable Redis to simulate an outage without
    # tearing down the shared container other tests may still be using.
    broken_store = RedisSessionStore("redis://127.0.0.1:1/0")
    app.state.session_store = broken_store
    try:
        resp = client.get("/projects")
        assert resp.status_code == 503

        ready_resp = client.get("/readyz")
        assert ready_resp.status_code == 503

        health_resp = client.get("/healthz")
        assert health_resp.status_code == 200
    finally:
        app.state.session_store = store
