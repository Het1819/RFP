"""Real authenticated-Redis integration tests for Phase A3 (spec section 12,
Redis items 14-21).

Skipped automatically unless a Redis instance requiring authentication is
reachable at REDIS_AUTH_TEST_URL. Run locally with a Redis started from the
generated secret config:

    uv run python scripts/generate_local_prod_secrets.py
    docker run -d --name rfp-redis-a3-test -p 16380:6379 \
        -v "$(pwd)/secrets/redis.conf:/usr/local/etc/redis/redis.conf:ro" \
        redis:7-alpine redis-server /usr/local/etc/redis/redis.conf

    REDIS_AUTH_TEST_URL="redis://:$(cat secrets/redis_password.txt)@localhost:16380/0" \
    APP_ENV=test AUTH_MODE=dev DATABASE_URL=sqlite:///:memory: \
    REDIS_URL=redis://localhost:16380/0 \
        uv run pytest tests/integration/test_a3_redis_auth_real.py -v
"""

import asyncio
import os

import pytest

pytestmark = pytest.mark.skipif(
    "REDIS_AUTH_TEST_URL" not in os.environ,
    reason="REDIS_AUTH_TEST_URL not set; skipping live authenticated-Redis test.",
)


def _run(coro):
    return asyncio.run(coro)


def _auth_url() -> str:
    return os.environ["REDIS_AUTH_TEST_URL"]


def _unauth_url() -> str:
    # Same host/port, no credentials.
    url = _auth_url()
    return url.split("@", 1)[-1].join(["redis://", ""]) if "@" in url else url


def test_redis_requires_authentication_real_server():
    """Connecting with no password to a `requirepass`-protected server
    fails; this is the real-server counterpart to the unit-level
    'requires authentication' config test."""
    import redis

    host_part = _auth_url().split("@", 1)[-1]
    client = redis.Redis.from_url(f"redis://{host_part}")
    with pytest.raises(redis.exceptions.AuthenticationError):
        client.ping()


def test_invalid_password_fails_real_server():
    import redis

    host_part = _auth_url().split("@", 1)[-1]
    client = redis.Redis.from_url(f"redis://:wrong-password-value@{host_part}")
    with pytest.raises(redis.exceptions.AuthenticationError):
        client.ping()


def test_app_can_authenticate_and_ping_real_server():
    from app.core.sessions.store import RedisSessionStore

    store = RedisSessionStore(_auth_url())
    assert _run(store.ping()) is True


def test_session_lifecycle_against_authenticated_redis():
    from app.core.sessions.models import SessionRecord
    from app.core.sessions.store import RedisSessionStore

    store = RedisSessionStore(_auth_url())
    record = SessionRecord(
        created_at=0.0,
        last_activity_at=0.0,
        user_id="u1",
        org_id="o1",
        authenticated_at=0.0,
    )

    async def _flow():
        await store.save("a3-test-session", record, ttl_seconds=30)
        loaded = await store.get("a3-test-session")
        assert loaded is not None
        assert loaded.user_id == "u1"
        await store.delete("a3-test-session")
        assert await store.get("a3-test-session") is None

    _run(_flow())


def test_throttling_works_against_authenticated_redis():
    from app.core.sessions.throttling import LoginThrottle, RedisThrottleStore

    store = RedisThrottleStore(_auth_url())
    throttle = LoginThrottle(
        store,
        "test-throttle-secret-at-least-32-characters-long",
        account_ip_max=3,
        account_ip_window_seconds=60,
        ip_max=100,
        ip_window_seconds=60,
        account_max=100,
        account_window_seconds=3600,
        max_cooldown_seconds=300,
    )

    async def _flow():
        email = "a3-throttle-test@rfparchitect.com"
        for _ in range(3):
            await throttle.record_failure(normalized_email=email, source_ip="1.2.3.4")
        decision = await throttle.check(normalized_email=email, source_ip="1.2.3.4")
        assert not decision.allowed
        await throttle.record_success(normalized_email=email, source_ip="1.2.3.4")
        decision2 = await throttle.check(normalized_email=email, source_ip="1.2.3.4")
        assert decision2.allowed

    _run(_flow())


def test_readiness_fails_with_invalid_redis_credentials():
    from app.core.sessions.store import RedisSessionStore, SessionStoreUnavailableError

    host_part = _auth_url().split("@", 1)[-1]
    store = RedisSessionStore(f"redis://:wrong-password-value@{host_part}")
    with pytest.raises(SessionStoreUnavailableError):
        _run(store.ping())
