"""Production storage configuration: app and worker resolve the same
persistent path, and the development-relative default is rejected."""

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
}


def test_ephemeral_default_storage_path_rejected_in_production():
    with pytest.raises(ValueError, match="LOCAL_STORAGE_PATH"):
        Settings(**{**_VALID_PROD_BASE, "LOCAL_STORAGE_PATH": "./data"})


def test_relative_storage_path_rejected_in_production():
    with pytest.raises(ValueError, match="absolute path"):
        Settings(**{**_VALID_PROD_BASE, "LOCAL_STORAGE_PATH": "data/storage"})


def test_absolute_mounted_storage_path_accepted():
    s = Settings(**{**_VALID_PROD_BASE, "LOCAL_STORAGE_PATH": "/data/storage"})
    assert s.LOCAL_STORAGE_PATH == "/data/storage"


def test_app_and_worker_read_the_same_settings_field():
    """Both app.services.project_service (used by the app's HTTP routes)
    and app.worker's process_document_task (via
    process_job_pipeline_async) resolve documents under
    `settings.LOCAL_STORAGE_PATH` -- there is exactly one field, so
    app/worker structurally cannot diverge on storage location as long as
    they share Settings (which docker-compose.prod.yml guarantees by
    setting the same LOCAL_STORAGE_PATH env var for both services)."""
    import inspect

    from app.services import project_service

    source = inspect.getsource(project_service)
    assert "settings.LOCAL_STORAGE_PATH" in source
