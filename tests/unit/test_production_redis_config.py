"""Production Redis configuration: authentication required, safe URL
construction, and a single effective-URL function shared by session,
throttling, and queue code."""

import pytest

from app.core.config import Settings

_VALID_PROD_BASE = {
    "APP_ENV": "production",
    "AUTH_MODE": "session",
    "SESSION_SECRET_KEY": "a" * 32,
    "LOGIN_THROTTLE_SECRET": "b" * 32,
    "DB_HOST": "postgres",
    "POSTGRES_PASSWORD": "real-strong-password-123",
    "REDIS_HOST": "redis",
    "REDIS_PASSWORD": "real-strong-redis-pw-123",
    "LLM_PROVIDER": "anthropic",
    "ANTHROPIC_API_KEY": "sk-ant-real-key",
    "LLM_MODEL": "claude-sonnet-4-6",
    "STORAGE_BACKEND": "local",
    "LOCAL_STORAGE_PATH": "/data/storage",
}


def test_redis_requires_authentication_in_production():
    with pytest.raises(
        ValueError, match="Redis must be configured with authentication"
    ):
        Settings(**{**_VALID_PROD_BASE, "REDIS_PASSWORD": None})


def test_redis_externally_managed_override_allows_unauthenticated_url():
    s = Settings(
        **{
            **_VALID_PROD_BASE,
            "REDIS_HOST": None,
            "REDIS_PASSWORD": None,
            "REDIS_URL": "redis://managed.example.internal:6379/0",
            "REDIS_EXTERNALLY_MANAGED": True,
        }
    )
    assert s.effective_redis_url == "redis://managed.example.internal:6379/0"


def test_effective_redis_url_includes_authentication():
    s = Settings(**_VALID_PROD_BASE)
    url = s.effective_redis_url
    assert url.startswith("redis://:")
    assert "@redis:6379/0" in url


@pytest.mark.parametrize(
    "raw_password",
    ["p@ss:word/weird", "has spaces", "slash/colon:at@percent%"],
)
def test_redis_url_safely_encodes_reserved_characters(raw_password):
    s = Settings(**{**_VALID_PROD_BASE, "REDIS_PASSWORD": raw_password})
    url = s.effective_redis_url
    # redis-py must be able to parse the resulting URL and recover the
    # exact original password.
    import redis

    parsed = redis.connection.parse_url(url)
    assert parsed["password"] == raw_password


def test_session_throttle_and_queue_share_the_same_effective_redis_url():
    """app.main wires RedisSessionStore/RedisThrottleStore from
    effective_session_redis_url (which falls back to effective_redis_url),
    and app.worker/app.core.queue build their arq RedisSettings from
    effective_redis_url directly -- same underlying property, so there is
    exactly one place authentication is assembled."""
    s = Settings(**_VALID_PROD_BASE)
    assert s.effective_session_redis_url == s.effective_redis_url


def test_dev_test_redis_url_still_works_without_redis_host():
    s = Settings(
        APP_ENV="test",
        AUTH_MODE="dev",
        DATABASE_URL="sqlite:///:memory:",
        REDIS_URL="redis://localhost:6379/0",
    )
    assert s.effective_redis_url == "redis://localhost:6379/0"
