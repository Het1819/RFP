"""Historical regression evidence: these tests prove the Phase A3 production
configuration weaknesses BEFORE the fix. Run against the unmodified A2 tip
(commit 86ea279d8a9e1e61d27ea78154a3ba8c937c4d21) -- all assertions here
passed, confirming each defect below. They are skipped once A3 fixes land
(the fixed behavior is asserted by test_a3_production_config_security.py
instead), but kept for the record.

These tests only read repository files and call in-process config code --
no Docker, no network, no production system is touched.
"""

import pathlib

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]

pytestmark = pytest.mark.skip(
    reason=(
        "Phase A3 pre-fix evidence only (recorded: 10/10 passed against A2 "
        "tip 86ea279d8a9e1e61d27ea78154a3ba8c937c4d21). Superseded by the "
        "hardened production Compose/config in Phase A3 -- see "
        "test_a3_production_config_security.py for the enforced equivalents."
    )
)


def _read(relative_path: str) -> str:
    return (REPO_ROOT / relative_path).read_text(encoding="utf-8")


def test_weakness_1_fixed_postgres_password_in_prod_compose():
    compose = _read("docker-compose.prod.yml")
    assert "rfp_password" in compose


def test_weakness_2_app_and_worker_share_same_fixed_db_password():
    compose = _read("docker-compose.prod.yml")
    assert compose.count("rfp_user:rfp_password@postgres") == 2


def test_weakness_3_prod_compose_selects_fake_llm_provider():
    compose = _read("docker-compose.prod.yml")
    assert "LLM_PROVIDER=fake" in compose


def test_weakness_4_config_does_not_reject_fake_provider_at_startup():
    """get_llm_provider() rejects fake in prod at CALL time, but nothing
    rejects it at Settings() CONSTRUCTION time -- so a prod container built
    from this compose file starts up "successfully" and only blows up on
    the first LLM-touching request."""
    from app.core.config import Settings

    settings = Settings(
        APP_ENV="production",
        AUTH_MODE="session",
        SESSION_SECRET_KEY="a" * 32,
        LOGIN_THROTTLE_SECRET="b" * 32,
        LLM_PROVIDER="fake",
    )
    assert settings.LLM_PROVIDER == "fake"  # construction did not raise


def test_weakness_5_prod_compose_missing_login_throttle_secret():
    compose = _read("docker-compose.prod.yml")
    assert "LOGIN_THROTTLE_SECRET" not in compose


def test_weakness_6_app_port_publicly_bound_not_loopback():
    compose = _read("docker-compose.prod.yml")
    assert '"8000:8000"' in compose
    assert "127.0.0.1:8000:8000" not in compose


def test_weakness_7_no_persistent_document_storage_volume():
    compose = _read("docker-compose.prod.yml")
    assert "postgres_data" in compose
    assert "redis_data" in compose
    # No volume for uploaded/source documents at all.
    assert "storage" not in compose.lower()
    assert "uploads" not in compose.lower()


def test_weakness_8_sensitive_values_passed_via_plain_environment():
    compose = _read("docker-compose.prod.yml")
    assert "SESSION_SECRET_KEY=${SESSION_SECRET_KEY}" in compose
    assert "secrets:" not in compose


def test_weakness_9_staging_docs_advertise_unsupported_openai():
    staging_env = _read(".env.staging.example")
    assert "OPENAI_API_KEY" in staging_env
    assert '"openai"' in staging_env or "openai" in staging_env.lower()


def test_weakness_10_deployment_docs_create_malformed_password_hash_user():
    deployment_doc = _read("DEPLOYMENT.md")
    assert "hashed_password='not-applicable'" in deployment_doc

    from app.core.passwords import verify_password

    assert verify_password("anything", "not-applicable") is False
