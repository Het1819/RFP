"""Production session-cookie configuration: __Host- prefix required."""

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
    "QUARANTINE_STORAGE_PATH": "/data/quarantine",
    "TRUSTED_PROXY_IPS": "172.28.0.10",
    "ALLOWED_HOSTS": "rfp.example.com",
    "PUBLIC_BASE_URL": "https://rfp.example.com",
    "SESSION_COOKIE_NAME": "__Host-rfp_session",
}


def test_host_prefixed_cookie_name_accepted_in_production():
    s = Settings(**_VALID_PROD_BASE)
    assert s.SESSION_COOKIE_NAME == "__Host-rfp_session"


def test_non_prefixed_cookie_name_rejected_in_production():
    with pytest.raises(ValueError, match="__Host-"):
        Settings(**{**_VALID_PROD_BASE, "SESSION_COOKIE_NAME": "rfp_session"})


def test_dev_test_cookie_name_does_not_require_host_prefix():
    s = Settings(
        APP_ENV="test",
        AUTH_MODE="dev",
        DATABASE_URL="sqlite:///:memory:",
        SESSION_COOKIE_NAME="rfp_session",
    )
    assert s.SESSION_COOKIE_NAME == "rfp_session"
