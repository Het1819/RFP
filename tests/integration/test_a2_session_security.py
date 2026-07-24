"""Phase A2 tests: server-side Redis-backed sessions, idle/absolute
expiration, immediate revocation, and login throttling.

These enforce the fixed behavior that supersedes the vulnerabilities
recorded (and now skipped) in test_a1_session_weaknesses.py. Section
numbers below correspond to the Phase A2 spec's required-test list.
"""

import asyncio

import pytest
from sqlalchemy import select

from app.core.config import settings
from app.core.sessions.clock import FakeClock
from app.core.sessions.throttling import LoginThrottle
from app.main import app
from app.models.audit import AuditEvent
from app.models.organization import Organization
from app.models.user import User
from tests.integration.test_csrf import extract_csrf_token

_PASSWORD = "correct-horse-battery-staple"
COOKIE_NAME = "rfp_session"
TEST_CLIENT_IP = "testclient"  # Starlette TestClient's fixed peer address.


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture(autouse=True)
def _session_mode(monkeypatch):
    monkeypatch.setattr(settings, "AUTH_MODE", "session")


def _create_active_user(db, email: str, password: str = _PASSWORD):
    from app.core.passwords import hash_password

    org = Organization(name=f"Org for {email}")
    db.add(org)
    db.commit()
    db.refresh(org)

    user = User(
        organization_id=org.id,
        email=email,
        hashed_password=hash_password(password),
        full_name="A2 Test User",
        is_active=True,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return org, user


def _login(client, email: str, password: str = _PASSWORD, **extra_headers):
    csrf_token = extract_csrf_token(client.get("/login").text)
    headers = {"X-Test-Enforce-CSRF": "true", **extra_headers}
    return client.post(
        "/login",
        data={"csrf_token": csrf_token, "email": email, "password": password},
        headers=headers,
        follow_redirects=False,
    )


def _throttle() -> LoginThrottle:
    return LoginThrottle(
        app.state.throttle_store,
        settings.effective_login_throttle_secret,
        account_ip_max=settings.LOGIN_THROTTLE_ACCOUNT_IP_MAX,
        account_ip_window_seconds=settings.LOGIN_THROTTLE_ACCOUNT_IP_WINDOW_SECONDS,
        ip_max=settings.LOGIN_THROTTLE_IP_MAX,
        ip_window_seconds=settings.LOGIN_THROTTLE_IP_WINDOW_SECONDS,
        account_max=settings.LOGIN_THROTTLE_ACCOUNT_MAX,
        account_window_seconds=settings.LOGIN_THROTTLE_ACCOUNT_WINDOW_SECONDS,
        max_cooldown_seconds=settings.LOGIN_THROTTLE_MAX_COOLDOWN_SECONDS,
    )


# ---------------------------------------------------------------------------
# Cookie and storage (1-8)
# ---------------------------------------------------------------------------


def test_cookie_contains_only_opaque_identifier(unauthenticated_client, db):
    from app.core.sessions.models import is_valid_session_id

    _create_active_user(db, "opaque@rfparchitect.com")
    _login(unauthenticated_client, "opaque@rfparchitect.com")

    raw_cookie = unauthenticated_client.cookies.get(COOKIE_NAME)
    assert raw_cookie is not None
    assert is_valid_session_id(raw_cookie)


def test_cookie_does_not_contain_sensitive_fields(unauthenticated_client, db):
    org, user = _create_active_user(db, "nosensitive@rfparchitect.com")
    _login(unauthenticated_client, "nosensitive@rfparchitect.com")

    raw_cookie = unauthenticated_client.cookies.get(COOKIE_NAME)
    assert str(user.id) not in raw_cookie
    assert str(org.id) not in raw_cookie
    assert "nosensitive@rfparchitect.com" not in raw_cookie
    # No JSON/base64 structure at all -- just an opaque token.
    assert "." not in raw_cookie or "=" not in raw_cookie.split(".")[0]


def test_invalid_cookie_rejected_before_redis_lookup(
    unauthenticated_client, session_store
):
    calls: list[str] = []
    orig_get = session_store.get

    async def spy_get(session_id):
        calls.append(session_id)
        return await orig_get(session_id)

    session_store.get = spy_get

    unauthenticated_client.cookies.set(COOKIE_NAME, "not-a-valid-session-id!!")
    resp = unauthenticated_client.get(
        "/projects", headers={"accept": "text/html"}, follow_redirects=False
    )
    assert resp.status_code == 303
    assert calls == []  # format rejected before any store lookup was attempted


def test_malformed_redis_record_fails_closed(unauthenticated_client, session_store):
    from app.core.sessions.models import generate_session_id

    session_id = generate_session_id()
    session_store._records[session_id] = "{not valid json"

    unauthenticated_client.cookies.set(COOKIE_NAME, session_id)
    resp = unauthenticated_client.get(
        "/projects", headers={"accept": "text/html"}, follow_redirects=False
    )
    assert resp.status_code == 303
    # The corrupt record must be removed, not left in the store.
    assert _run(session_store.get(session_id)) is None


def test_anonymous_csrf_session_works(unauthenticated_client):
    resp = unauthenticated_client.get("/login")
    assert resp.status_code == 200
    assert extract_csrf_token(resp.text)
    assert unauthenticated_client.cookies.get(COOKIE_NAME) is not None


def test_authentication_rotates_session_identifier(unauthenticated_client, db):
    _create_active_user(db, "rotate@rfparchitect.com")
    unauthenticated_client.get("/login")
    anon_cookie = unauthenticated_client.cookies.get(COOKIE_NAME)

    _login(unauthenticated_client, "rotate@rfparchitect.com")
    authed_cookie = unauthenticated_client.cookies.get(COOKIE_NAME)

    assert anon_cookie is not None
    assert authed_cookie is not None
    assert anon_cookie != authed_cookie


def test_pre_authentication_identifier_becomes_invalid(unauthenticated_client, db):
    from fastapi.testclient import TestClient

    from app.core.database import get_db

    _create_active_user(db, "preauth@rfparchitect.com")
    anon_cookie = unauthenticated_client.get("/login").cookies.get(COOKIE_NAME)
    _login(unauthenticated_client, "preauth@rfparchitect.com")

    def override_get_db():
        yield db

    app.dependency_overrides[get_db] = override_get_db
    try:
        with TestClient(app, raise_server_exceptions=True) as replay_client:
            replay_client.cookies.set(COOKIE_NAME, anon_cookie)
            resp = replay_client.get(
                "/projects", headers={"accept": "text/html"}, follow_redirects=False
            )
            assert resp.status_code == 303
            assert resp.headers["location"] == "/login"
    finally:
        app.dependency_overrides.clear()


def test_authenticated_request_resolves_identity_only_from_redis(
    unauthenticated_client, db, session_store
):
    _create_active_user(db, "onlyredis@rfparchitect.com")
    _login(unauthenticated_client, "onlyredis@rfparchitect.com")
    assert unauthenticated_client.get("/projects").status_code == 200

    session_id = unauthenticated_client.cookies.get(COOKIE_NAME)
    _run(session_store.delete(session_id))

    resp = unauthenticated_client.get(
        "/projects", headers={"accept": "text/html"}, follow_redirects=False
    )
    assert resp.status_code == 303
    assert resp.headers["location"] == "/login"


# ---------------------------------------------------------------------------
# Expiration (9-15)
# ---------------------------------------------------------------------------


def test_idle_timeout_expires_session(unauthenticated_client, db):
    _create_active_user(db, "idle-expire@rfparchitect.com")
    clock = FakeClock()
    app.state.session_clock = clock
    _login(unauthenticated_client, "idle-expire@rfparchitect.com")
    assert unauthenticated_client.get("/projects").status_code == 200

    clock.advance(settings.SESSION_IDLE_TIMEOUT_SECONDS + 1)
    resp = unauthenticated_client.get(
        "/projects", headers={"accept": "text/html"}, follow_redirects=False
    )
    assert resp.status_code == 303
    assert resp.headers["location"] == "/login"


def test_activity_resets_idle_expiry(unauthenticated_client, db):
    _create_active_user(db, "idle-reset@rfparchitect.com")
    clock = FakeClock()
    app.state.session_clock = clock
    _login(unauthenticated_client, "idle-reset@rfparchitect.com")

    step = settings.SESSION_IDLE_TIMEOUT_SECONDS - 10
    for _ in range(3):
        clock.advance(step)
        resp = unauthenticated_client.get("/projects")
        assert resp.status_code == 200  # each gap stayed under the idle limit


def test_activity_does_not_extend_absolute_expiry(unauthenticated_client, db):
    _create_active_user(db, "absolute-anchor@rfparchitect.com")
    clock = FakeClock()
    app.state.session_clock = clock
    _login(unauthenticated_client, "absolute-anchor@rfparchitect.com")

    step = settings.SESSION_IDLE_TIMEOUT_SECONDS - 10
    total = 0
    resp = None
    while total <= settings.SESSION_ABSOLUTE_TIMEOUT_SECONDS:
        clock.advance(step)
        total += step
        resp = unauthenticated_client.get(
            "/projects", headers={"accept": "text/html"}, follow_redirects=False
        )
        if resp.status_code == 303:
            break

    # Continuous activity (idle never expired) still hit the absolute
    # ceiling -- the boundary never moved despite every request refreshing
    # last_activity_at.
    assert resp is not None
    assert resp.status_code == 303
    assert resp.headers["location"] == "/login"


def test_absolute_timeout_expires_active_session(unauthenticated_client, db):
    _create_active_user(db, "absolute-expire@rfparchitect.com")
    clock = FakeClock()
    app.state.session_clock = clock
    _login(unauthenticated_client, "absolute-expire@rfparchitect.com")

    step = settings.SESSION_IDLE_TIMEOUT_SECONDS // 2
    total = 0
    resp = None
    while total <= settings.SESSION_ABSOLUTE_TIMEOUT_SECONDS:
        clock.advance(step)
        total += step
        resp = unauthenticated_client.get(
            "/projects", headers={"accept": "text/html"}, follow_redirects=False
        )
        if resp.status_code == 303:
            break

    assert resp is not None
    assert resp.status_code == 303


def test_expired_records_deleted_from_store(unauthenticated_client, db, session_store):
    _create_active_user(db, "expire-delete@rfparchitect.com")
    clock = FakeClock()
    app.state.session_clock = clock
    _login(unauthenticated_client, "expire-delete@rfparchitect.com")
    session_id = unauthenticated_client.cookies.get(COOKIE_NAME)

    clock.advance(settings.SESSION_IDLE_TIMEOUT_SECONDS + 1)
    unauthenticated_client.get("/projects")

    assert _run(session_store.get(session_id)) is None


def test_cookie_cleared_after_expiration(unauthenticated_client, db):
    _create_active_user(db, "cookie-clear@rfparchitect.com")
    clock = FakeClock()
    app.state.session_clock = clock
    _login(unauthenticated_client, "cookie-clear@rfparchitect.com")
    original_cookie = unauthenticated_client.cookies.get(COOKIE_NAME)

    clock.advance(settings.SESSION_IDLE_TIMEOUT_SECONDS + 1)
    unauthenticated_client.get("/projects")

    assert unauthenticated_client.cookies.get(COOKIE_NAME) != original_cookie


def test_redis_ttl_never_exceeds_remaining_absolute_duration(
    unauthenticated_client, db, session_store
):
    _create_active_user(db, "ttl-bound@rfparchitect.com")
    clock = FakeClock()
    app.state.session_clock = clock
    _login(unauthenticated_client, "ttl-bound@rfparchitect.com")

    captured_ttls: list[int] = []
    orig_save = session_store.save

    async def spy_save(session_id, record, *, ttl_seconds):
        captured_ttls.append(ttl_seconds)
        return await orig_save(session_id, record, ttl_seconds=ttl_seconds)

    session_store.save = spy_save

    # Approach the absolute boundary in hops smaller than the idle timeout,
    # with a request on each hop, so the session stays alive on idle terms
    # the whole way -- only then does the TTL-vs-absolute-remaining math get
    # exercised instead of a fresh post-idle-expiry session being issued.
    remaining_target = 100
    step = settings.SESSION_IDLE_TIMEOUT_SECONDS - 50
    target_total = settings.SESSION_ABSOLUTE_TIMEOUT_SECONDS - remaining_target
    total_advanced = 0
    while total_advanced + step < target_total:
        clock.advance(step)
        total_advanced += step
        resp = unauthenticated_client.get("/projects")
        assert resp.status_code == 200

    clock.advance(target_total - total_advanced)
    unauthenticated_client.get("/projects")

    assert captured_ttls
    assert captured_ttls[-1] <= remaining_target + 1
    assert captured_ttls[-1] < settings.SESSION_IDLE_TIMEOUT_SECONDS


# ---------------------------------------------------------------------------
# Revocation (16-20)
# ---------------------------------------------------------------------------


def test_post_logout_deletes_server_side_session(
    unauthenticated_client, db, session_store
):
    _create_active_user(db, "logout-delete@rfparchitect.com")
    _login(unauthenticated_client, "logout-delete@rfparchitect.com")
    session_id = unauthenticated_client.cookies.get(COOKIE_NAME)

    projects_resp = unauthenticated_client.get("/projects")
    logout_csrf = extract_csrf_token(projects_resp.text)
    unauthenticated_client.post(
        "/logout",
        data={"csrf_token": logout_csrf},
        headers={"X-Test-Enforce-CSRF": "true"},
        follow_redirects=False,
    )

    assert _run(session_store.get(session_id)) is None


def test_copied_cookie_fails_immediately_after_logout(db, session_store, monkeypatch):
    from fastapi.testclient import TestClient

    from app.core.database import get_db

    monkeypatch.setattr(settings, "AUTH_MODE", "session")
    _create_active_user(db, "fixed-stolen@rfparchitect.com")

    def override_get_db():
        yield db

    app.dependency_overrides[get_db] = override_get_db
    try:
        with (
            TestClient(app, raise_server_exceptions=True) as victim,
            TestClient(app, raise_server_exceptions=True) as attacker,
        ):
            _login(victim, "fixed-stolen@rfparchitect.com")
            stolen_cookie = victim.cookies.get(COOKIE_NAME)
            attacker.cookies.set(COOKIE_NAME, stolen_cookie)
            assert attacker.get("/projects").status_code == 200

            projects_resp = victim.get("/projects")
            logout_csrf = extract_csrf_token(projects_resp.text)
            victim.post(
                "/logout",
                data={"csrf_token": logout_csrf},
                headers={"X-Test-Enforce-CSRF": "true"},
                follow_redirects=False,
            )

            attacker_resp = attacker.get(
                "/projects", headers={"accept": "text/html"}, follow_redirects=False
            )
            assert attacker_resp.status_code == 303
            assert attacker_resp.headers["location"] == "/login"
    finally:
        app.dependency_overrides.clear()


def test_admin_revocation_removes_all_sessions_for_one_user(db, session_store):
    from fastapi.testclient import TestClient

    from app.core.database import get_db

    _org, user = _create_active_user(db, "revoke-all@rfparchitect.com")

    def override_get_db():
        yield db

    app.dependency_overrides[get_db] = override_get_db
    try:
        with (
            TestClient(app, raise_server_exceptions=True) as device_a,
            TestClient(app, raise_server_exceptions=True) as device_b,
        ):
            _login(device_a, "revoke-all@rfparchitect.com")
            _login(device_b, "revoke-all@rfparchitect.com")
            assert device_a.get("/projects").status_code == 200
            assert device_b.get("/projects").status_code == 200

            revoked = _run(session_store.revoke_all_for_user(str(user.id)))
            assert revoked == 2

            for c in (device_a, device_b):
                resp = c.get(
                    "/projects",
                    headers={"accept": "text/html"},
                    follow_redirects=False,
                )
                assert resp.status_code == 303
    finally:
        app.dependency_overrides.clear()


def test_revoking_one_user_does_not_affect_another(
    unauthenticated_client, db, session_store
):
    from fastapi.testclient import TestClient

    from app.core.database import get_db

    _org1, user1 = _create_active_user(db, "revoke-target@rfparchitect.com")
    _org2, _user2 = _create_active_user(db, "revoke-bystander@rfparchitect.com")

    def override_get_db():
        yield db

    app.dependency_overrides[get_db] = override_get_db
    try:
        with (
            TestClient(app, raise_server_exceptions=True) as target,
            TestClient(app, raise_server_exceptions=True) as bystander,
        ):
            _login(target, "revoke-target@rfparchitect.com")
            _login(bystander, "revoke-bystander@rfparchitect.com")

            _run(session_store.revoke_all_for_user(str(user1.id)))

            target_resp = target.get(
                "/projects", headers={"accept": "text/html"}, follow_redirects=False
            )
            assert target_resp.status_code == 303
            assert bystander.get("/projects").status_code == 200
    finally:
        app.dependency_overrides.clear()


def test_repeated_logout_is_idempotent(unauthenticated_client, db):
    _create_active_user(db, "idempotent-logout@rfparchitect.com")
    _login(unauthenticated_client, "idempotent-logout@rfparchitect.com")

    projects_resp = unauthenticated_client.get("/projects")
    logout_csrf = extract_csrf_token(projects_resp.text)
    first = unauthenticated_client.post(
        "/logout",
        data={"csrf_token": logout_csrf},
        headers={"X-Test-Enforce-CSRF": "true"},
        follow_redirects=False,
    )
    assert first.status_code == 303

    login_page = unauthenticated_client.get("/login")
    csrf_2 = extract_csrf_token(login_page.text)
    second = unauthenticated_client.post(
        "/logout",
        data={"csrf_token": csrf_2},
        headers={"X-Test-Enforce-CSRF": "true"},
        follow_redirects=False,
    )
    assert second.status_code == 303


# ---------------------------------------------------------------------------
# Redis failure (21-24)
# ---------------------------------------------------------------------------


def test_protected_routes_fail_closed_when_redis_unavailable(
    unauthenticated_client, db, session_store
):
    _create_active_user(db, "outage-protected@rfparchitect.com")
    _login(unauthenticated_client, "outage-protected@rfparchitect.com")
    assert unauthenticated_client.get("/projects").status_code == 200

    session_store.available = False
    resp = unauthenticated_client.get("/projects")
    assert resp.status_code == 503


def test_login_cannot_create_authenticated_session_when_redis_unavailable(
    unauthenticated_client, db, session_store
):
    _create_active_user(db, "outage-login@rfparchitect.com")
    csrf_token = extract_csrf_token(unauthenticated_client.get("/login").text)

    session_store.available = False
    resp = unauthenticated_client.post(
        "/login",
        data={
            "csrf_token": csrf_token,
            "email": "outage-login@rfparchitect.com",
            "password": _PASSWORD,
        },
        headers={"X-Test-Enforce-CSRF": "true"},
        follow_redirects=False,
    )
    assert resp.status_code == 503
    assert resp.headers.get("location") != "/projects"


def test_healthz_remains_available_without_redis(unauthenticated_client, session_store):
    session_store.available = False
    resp = unauthenticated_client.get("/healthz")
    assert resp.status_code == 200


def test_readyz_reports_not_ready_without_redis(unauthenticated_client, session_store):
    session_store.available = False
    resp = unauthenticated_client.get("/readyz")
    assert resp.status_code == 503


# ---------------------------------------------------------------------------
# Throttling (25-34)
# ---------------------------------------------------------------------------


def test_failed_attempts_increment_account_ip_state(session_store):
    throttle = _throttle()
    _run(
        throttle.record_failure(
            normalized_email="throttle-a@rfparchitect.com", source_ip="1.2.3.4"
        )
    )
    decision = _run(
        throttle.check(
            normalized_email="throttle-a@rfparchitect.com", source_ip="1.2.3.4"
        )
    )
    assert decision.allowed  # one failure, under the account+ip threshold

    for _ in range(settings.LOGIN_THROTTLE_ACCOUNT_IP_MAX):
        _run(
            throttle.record_failure(
                normalized_email="throttle-a@rfparchitect.com", source_ip="1.2.3.4"
            )
        )
    blocked = _run(
        throttle.check(
            normalized_email="throttle-a@rfparchitect.com", source_ip="1.2.3.4"
        )
    )
    assert not blocked.allowed


def test_failed_attempts_increment_source_ip_state(session_store):
    throttle = _throttle()
    for i in range(settings.LOGIN_THROTTLE_IP_MAX):
        _run(
            throttle.record_failure(
                normalized_email=f"unique-{i}@rfparchitect.com", source_ip="9.9.9.9"
            )
        )
    blocked = _run(
        throttle.check(
            normalized_email="yet-another@rfparchitect.com", source_ip="9.9.9.9"
        )
    )
    assert not blocked.allowed  # blocked purely on IP volume, different accounts


def test_failed_attempts_increment_account_across_ip_state(session_store):
    throttle = _throttle()
    email = "distributed-target@rfparchitect.com"
    for i in range(settings.LOGIN_THROTTLE_ACCOUNT_MAX):
        _run(
            throttle.record_failure(
                normalized_email=email, source_ip=f"10.0.0.{i % 250}"
            )
        )
    blocked = _run(throttle.check(normalized_email=email, source_ip="203.0.113.99"))
    assert not blocked.allowed  # new IP, but the account itself is over its limit


def test_concurrent_requests_cannot_exceed_limit_via_race(session_store):
    throttle = _throttle()

    async def _hammer():
        await asyncio.gather(
            *[
                throttle.record_failure(
                    normalized_email="race@rfparchitect.com", source_ip="5.5.5.5"
                )
                for _ in range(50)
            ]
        )

    _run(_hammer())
    # Every one of the 50 concurrent failures must have been counted --
    # a non-atomic read-modify-write would lose increments under
    # concurrency and under-count here.
    decision = _run(
        throttle.check(normalized_email="race@rfparchitect.com", source_ip="5.5.5.5")
    )
    assert not decision.allowed  # 50 >> account_ip_max, so it must be blocked


def test_unknown_and_existing_user_indistinguishable_responses(
    unauthenticated_client, db
):
    _create_active_user(db, "knownuser@rfparchitect.com")

    csrf_1 = extract_csrf_token(unauthenticated_client.get("/login").text)
    resp_known = unauthenticated_client.post(
        "/login",
        data={
            "csrf_token": csrf_1,
            "email": "knownuser@rfparchitect.com",
            "password": "wrong-password-value",
        },
        headers={"X-Test-Enforce-CSRF": "true"},
        follow_redirects=False,
    )

    csrf_2 = extract_csrf_token(unauthenticated_client.get("/login").text)
    resp_unknown = unauthenticated_client.post(
        "/login",
        data={
            "csrf_token": csrf_2,
            "email": "no-such-user@rfparchitect.com",
            "password": "wrong-password-value",
        },
        headers={"X-Test-Enforce-CSRF": "true"},
        follow_redirects=False,
    )

    assert resp_known.status_code == resp_unknown.status_code
    assert resp_known.headers["location"] == resp_unknown.headers["location"]


def test_threshold_breach_returns_bounded_retry_after(unauthenticated_client, db):
    _create_active_user(db, "willblock@rfparchitect.com")

    last_resp = None
    for _ in range(settings.LOGIN_THROTTLE_ACCOUNT_IP_MAX + 1):
        csrf_token = extract_csrf_token(unauthenticated_client.get("/login").text)
        last_resp = unauthenticated_client.post(
            "/login",
            data={
                "csrf_token": csrf_token,
                "email": "willblock@rfparchitect.com",
                "password": "wrong-password",
            },
            headers={"X-Test-Enforce-CSRF": "true"},
            follow_redirects=False,
        )

    assert last_resp is not None
    assert "Retry-After" in last_resp.headers
    retry_after = int(last_resp.headers["Retry-After"])
    assert 0 < retry_after <= settings.LOGIN_THROTTLE_MAX_COOLDOWN_SECONDS


def test_correct_password_cannot_bypass_active_throttle(unauthenticated_client, db):
    _create_active_user(db, "cantbypass@rfparchitect.com")

    for _ in range(settings.LOGIN_THROTTLE_ACCOUNT_IP_MAX + 1):
        csrf_token = extract_csrf_token(unauthenticated_client.get("/login").text)
        unauthenticated_client.post(
            "/login",
            data={
                "csrf_token": csrf_token,
                "email": "cantbypass@rfparchitect.com",
                "password": "wrong-password",
            },
            headers={"X-Test-Enforce-CSRF": "true"},
            follow_redirects=False,
        )

    csrf_token = extract_csrf_token(unauthenticated_client.get("/login").text)
    resp = unauthenticated_client.post(
        "/login",
        data={
            "csrf_token": csrf_token,
            "email": "cantbypass@rfparchitect.com",
            "password": _PASSWORD,  # correct password
        },
        headers={"X-Test-Enforce-CSRF": "true"},
        follow_redirects=False,
    )
    assert resp.headers.get("location") != "/projects"
    assert "Retry-After" in resp.headers


def test_successful_login_clears_only_appropriate_counters(unauthenticated_client, db):
    _create_active_user(db, "partial-clear@rfparchitect.com")
    throttle = _throttle()

    # A couple of failures (under threshold) on the target account+ip.
    for _ in range(2):
        csrf_token = extract_csrf_token(unauthenticated_client.get("/login").text)
        unauthenticated_client.post(
            "/login",
            data={
                "csrf_token": csrf_token,
                "email": "partial-clear@rfparchitect.com",
                "password": "wrong-password",
            },
            headers={"X-Test-Enforce-CSRF": "true"},
            follow_redirects=False,
        )

    # Unrelated failures against a different account, same IP -- this is
    # the abusive-IP history that must survive.
    for _ in range(3):
        _run(
            throttle.record_failure(
                normalized_email="other-account@rfparchitect.com",
                source_ip=TEST_CLIENT_IP,
            )
        )

    # Successful login for the target account.
    csrf_token = extract_csrf_token(unauthenticated_client.get("/login").text)
    resp = unauthenticated_client.post(
        "/login",
        data={
            "csrf_token": csrf_token,
            "email": "partial-clear@rfparchitect.com",
            "password": _PASSWORD,
        },
        headers={"X-Test-Enforce-CSRF": "true"},
        follow_redirects=False,
    )
    assert resp.headers["location"] == "/projects"

    post_login_decision = _run(
        throttle.check(
            normalized_email="partial-clear@rfparchitect.com", source_ip=TEST_CLIENT_IP
        )
    )
    assert post_login_decision.allowed  # account+ip counter was cleared

    ip_count = _run(throttle._store.get_count(f"rfp:throttle:ip:{TEST_CLIENT_IP}"))
    assert ip_count >= 3  # unrelated IP-wide history was NOT wiped


def test_raw_emails_and_passwords_not_in_throttle_keys_or_audit(
    unauthenticated_client, db
):
    _create_active_user(db, "privacy-check@rfparchitect.com")

    csrf_token = extract_csrf_token(unauthenticated_client.get("/login").text)
    unauthenticated_client.post(
        "/login",
        data={
            "csrf_token": csrf_token,
            "email": "privacy-check@rfparchitect.com",
            "password": "SuperSecretPassword123!",
        },
        headers={"X-Test-Enforce-CSRF": "true"},
        follow_redirects=False,
    )

    for key in app.state.throttle_store._counts:
        assert "privacy-check" not in key
        assert "SuperSecretPassword123!" not in key

    events = db.scalars(select(AuditEvent)).all()
    for event in events:
        blob = str(event.details)
        assert "SuperSecretPassword123!" not in blob


def test_forged_forwarding_headers_do_not_change_source_ip(unauthenticated_client, db):
    _create_active_user(db, "xff-ignore@rfparchitect.com")

    last_resp = None
    for i in range(settings.LOGIN_THROTTLE_ACCOUNT_IP_MAX + 1):
        csrf_token = extract_csrf_token(unauthenticated_client.get("/login").text)
        last_resp = unauthenticated_client.post(
            "/login",
            data={
                "csrf_token": csrf_token,
                "email": "xff-ignore@rfparchitect.com",
                "password": "wrong-password",
            },
            headers={
                "X-Test-Enforce-CSRF": "true",
                "X-Forwarded-For": f"198.51.100.{i}",
                "X-Real-IP": f"198.51.100.{i}",
            },
            follow_redirects=False,
        )

    # If forged headers changed the throttling identity per request, each
    # request would look like a fresh IP and never trip the limit.
    assert last_resp is not None
    assert "Retry-After" in last_resp.headers
