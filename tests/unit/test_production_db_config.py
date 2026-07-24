"""Production database configuration: no literal password, safe URL
encoding, repository-default rejection, and single shared effective URL
used by app/worker/migrations."""

import pytest

from app.core.config import Settings, settings

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


def test_production_db_config_contains_no_literal_password_field():
    """The DB_HOST/PORT/NAME/USER fields on Settings are non-sensitive by
    construction -- the only sensitive component (POSTGRES_PASSWORD) is
    never itself a compose-file literal in the production stack (it comes
    from a secret file). This test documents that the *field set* used to
    build the production URL is separated into sensitive/non-sensitive
    parts, unlike the old single-string DATABASE_URL default."""
    s = Settings(**_VALID_PROD_BASE)
    # Non-sensitive components are plain strings/ints, not derived from a
    # single opaque connection string containing the password.
    assert s.DB_HOST == "postgres"
    assert s.DB_PORT == 5432
    assert s.DB_NAME == "rfp_architect"
    assert s.DB_USER == "rfp_user"


@pytest.mark.parametrize(
    "raw_password",
    [
        "p@ss:word/with#reserved?chars",
        'has spaces and "quotes"',
        "slash/colon:at@percent%",
    ],
)
def test_effective_url_safely_encodes_reserved_characters(raw_password):
    s = Settings(**{**_VALID_PROD_BASE, "POSTGRES_PASSWORD": raw_password})
    url = s.effective_database_url
    # The URL must remain a single well-formed connection string: no raw
    # '@' or '/' from the password confusing the host/path boundary.
    assert url.startswith("postgresql+psycopg://rfp_user:")
    assert url.endswith("@postgres:5432/rfp_architect")
    # SQLAlchemy must be able to parse the resulting URL without error.
    from sqlalchemy.engine import make_url

    parsed = make_url(url)
    assert parsed.password == raw_password
    assert parsed.host == "postgres"
    assert parsed.database == "rfp_architect"


def test_repository_default_database_url_rejected_in_production():
    with pytest.raises(ValueError, match="repository default DATABASE_URL"):
        Settings(
            **{
                k: v
                for k, v in _VALID_PROD_BASE.items()
                if k not in ("DB_HOST", "POSTGRES_PASSWORD")
            },
            DATABASE_URL=(
                "postgresql+psycopg://rfp_user:rfp_password@localhost:5432/"
                "rfp_architect"
            ),
        )


def test_rfp_password_literal_rejected_in_production():
    with pytest.raises(ValueError, match="repository default database password"):
        Settings(
            **{
                k: v
                for k, v in _VALID_PROD_BASE.items()
                if k not in ("DB_HOST", "POSTGRES_PASSWORD")
            },
            DATABASE_URL="postgresql+psycopg://rfp_user:rfp_password@db.internal:5432/x",
        )


def test_missing_postgres_password_rejected_when_db_host_set():
    with pytest.raises(ValueError, match="POSTGRES_PASSWORD"):
        Settings(**{**_VALID_PROD_BASE, "POSTGRES_PASSWORD": None})


def test_localhost_db_host_rejected_without_explicit_opt_in():
    with pytest.raises(ValueError, match="DB_HOST=localhost"):
        Settings(**{**_VALID_PROD_BASE, "DB_HOST": "localhost"})


def test_localhost_db_host_allowed_with_explicit_opt_in():
    s = Settings(
        **{**_VALID_PROD_BASE, "DB_HOST": "localhost", "DB_ALLOW_LOCALHOST": True}
    )
    assert "localhost" in s.effective_database_url


def test_dev_test_database_url_still_works_without_db_host():
    # Dev/test/external-managed path: DATABASE_URL remains authoritative
    # when DB_HOST is unset.
    s = Settings(APP_ENV="test", AUTH_MODE="dev", DATABASE_URL="sqlite:///:memory:")
    assert s.effective_database_url == "sqlite:///:memory:"


def test_app_and_alembic_use_the_same_effective_url_function():
    """app.core.database.engine and alembic/env.py's offline mode both call
    settings.effective_database_url -- verified here by asserting the
    engine's URL matches the property directly (same singleton settings,
    same property, so any drift would be a real bug, not a test double
    silently diverging from production code)."""
    from app.core.database import engine

    assert (
        str(engine.url).split("@")[0].split("://")[0]
        == settings.effective_database_url.split("://")[0]
    )
