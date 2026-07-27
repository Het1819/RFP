"""Quarantine storage configuration: defaults and production hardening for
QUARANTINE_STORAGE_PATH, mirroring the existing LOCAL_STORAGE_PATH pattern."""

import pytest
from pydantic import ValidationError

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
    "TRUSTED_PROXY_IPS": "172.28.0.10",
    "ALLOWED_HOSTS": "rfp.example.com",
    "PUBLIC_BASE_URL": "https://rfp.example.com",
    "SESSION_COOKIE_NAME": "__Host-rfp_session",
}


class TestQuarantineSettingsDefaults:
    def test_defaults_present(self) -> None:
        s = Settings(APP_ENV="development")
        assert s.QUARANTINE_STORAGE_PATH == "./data/quarantine"
        assert s.QUARANTINE_CHUNK_SIZE_BYTES == 1024 * 1024
        assert s.MAX_DISPLAY_FILENAME_LENGTH == 255
        assert s.DOCX_DETECTION_MAX_MEMBERS == 5000


class TestQuarantinePathProductionValidation:
    def test_default_quarantine_path_rejected_in_production(self) -> None:
        with pytest.raises(ValidationError, match="QUARANTINE_STORAGE_PATH"):
            Settings(**_VALID_PROD_BASE)

    def test_relative_quarantine_path_rejected_in_production(self) -> None:
        with pytest.raises(ValidationError, match="absolute"):
            Settings(**{**_VALID_PROD_BASE, "QUARANTINE_STORAGE_PATH": "relative/path"})

    def test_quarantine_path_equal_to_storage_path_rejected(self) -> None:
        with pytest.raises(ValidationError, match="differ"):
            Settings(
                **{
                    **_VALID_PROD_BASE,
                    "QUARANTINE_STORAGE_PATH": "/data/storage",
                    "LOCAL_STORAGE_PATH": "/data/storage",
                }
            )

    def test_valid_distinct_absolute_quarantine_path_accepted(self) -> None:
        s = Settings(
            **{
                **_VALID_PROD_BASE,
                "QUARANTINE_STORAGE_PATH": "/data/quarantine",
                "LOCAL_STORAGE_PATH": "/data/storage",
            }
        )
        assert s.QUARANTINE_STORAGE_PATH == "/data/quarantine"
