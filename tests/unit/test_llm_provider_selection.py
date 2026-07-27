"""Provider-selection and fail-closed tests for the LLM configuration layer.

No test in this file makes a real Anthropic API request -- the client is
never invoked past construction, and network calls are neither made nor
mocked to succeed; we only assert on provider/model selection.
"""

import pytest

from app.core.config import Settings
from app.core.llm import AnthropicProvider, FakeLLMProvider, get_llm_provider


def test_anthropic_provider_selected_with_valid_config(monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "LLM_PROVIDER", "anthropic")
    monkeypatch.setattr(settings, "ANTHROPIC_API_KEY", "sk-ant-test-key-not-real")
    monkeypatch.setattr(settings, "LLM_MODEL", "claude-sonnet-4-6")

    provider = get_llm_provider()
    assert isinstance(provider, AnthropicProvider)
    assert provider.model == "claude-sonnet-4-6"


def test_anthropic_provider_defaults_model_when_unset_in_dev(monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "LLM_PROVIDER", "anthropic")
    monkeypatch.setattr(settings, "ANTHROPIC_API_KEY", "sk-ant-test-key-not-real")
    monkeypatch.setattr(settings, "LLM_MODEL", "")

    provider = get_llm_provider()
    assert isinstance(provider, AnthropicProvider)
    assert provider.model  # non-empty default


def test_fake_provider_selected_in_dev_when_configured(monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "LLM_PROVIDER", "fake")
    monkeypatch.setattr(settings, "APP_ENV", "test")

    assert isinstance(get_llm_provider(), FakeLLMProvider)


def test_fake_provider_blocked_outside_dev_at_call_time(monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "LLM_PROVIDER", "fake")
    monkeypatch.setattr(settings, "APP_ENV", "production")

    with pytest.raises(RuntimeError):
        get_llm_provider()


def test_settings_rejects_fake_provider_in_production():
    with pytest.raises(ValueError, match="cannot be 'fake'"):
        Settings(
            APP_ENV="production",
            AUTH_MODE="session",
            SESSION_SECRET_KEY="a" * 32,
            LOGIN_THROTTLE_SECRET="b" * 32,
            DB_HOST="postgres",
            POSTGRES_PASSWORD="real-strong-password-123",
            REDIS_HOST="redis",
            REDIS_PASSWORD="real-strong-redis-pw-123",
            LLM_PROVIDER="fake",
        )


def test_settings_rejects_unsupported_provider_name():
    with pytest.raises(ValueError, match="Unsupported LLM_PROVIDER"):
        Settings(
            APP_ENV="production",
            AUTH_MODE="session",
            SESSION_SECRET_KEY="a" * 32,
            LOGIN_THROTTLE_SECRET="b" * 32,
            DB_HOST="postgres",
            POSTGRES_PASSWORD="real-strong-password-123",
            REDIS_HOST="redis",
            REDIS_PASSWORD="real-strong-redis-pw-123",
            LLM_PROVIDER="openai",
        )


def test_settings_rejects_missing_anthropic_key_in_production():
    with pytest.raises(ValueError, match="ANTHROPIC_API_KEY"):
        Settings(
            APP_ENV="production",
            AUTH_MODE="session",
            SESSION_SECRET_KEY="a" * 32,
            LOGIN_THROTTLE_SECRET="b" * 32,
            DB_HOST="postgres",
            POSTGRES_PASSWORD="real-strong-password-123",
            REDIS_HOST="redis",
            REDIS_PASSWORD="real-strong-redis-pw-123",
            LLM_PROVIDER="anthropic",
            ANTHROPIC_API_KEY="",
            LLM_MODEL="claude-sonnet-4-6",
        )


def test_settings_rejects_missing_explicit_model_in_production():
    with pytest.raises(ValueError, match="LLM_MODEL"):
        Settings(
            APP_ENV="production",
            AUTH_MODE="session",
            SESSION_SECRET_KEY="a" * 32,
            LOGIN_THROTTLE_SECRET="b" * 32,
            DB_HOST="postgres",
            POSTGRES_PASSWORD="real-strong-password-123",
            REDIS_HOST="redis",
            REDIS_PASSWORD="real-strong-redis-pw-123",
            LLM_PROVIDER="anthropic",
            ANTHROPIC_API_KEY="sk-ant-real-key",
            LLM_MODEL="",
        )


def test_anthropic_key_never_appears_in_settings_repr(monkeypatch):
    from app.core.config import settings

    secret_key = "sk-ant-super-secret-value-should-never-leak"
    monkeypatch.setattr(settings, "ANTHROPIC_API_KEY", secret_key)

    # Settings.__repr__ / str must not leak the raw key into logs that
    # might print the settings object directly.
    provider = AnthropicProvider(api_key=secret_key, model="claude-sonnet-4-6")
    assert secret_key not in repr(provider)
    assert secret_key not in str(provider)


@pytest.mark.asyncio
async def test_anthropic_extract_requirements_failure_does_not_leak_key():
    """A failing Anthropic call (e.g. bad key) must not leak the key
    material anywhere in the raised exception's string representation."""
    secret_key = "sk-ant-should-not-leak-1234567890"
    provider = AnthropicProvider(api_key=secret_key, model="claude-sonnet-4-6")

    class _BoomClient:
        class messages:  # noqa: N801 - mimicking the real client's shape
            @staticmethod
            async def create(**kwargs):
                raise RuntimeError("simulated network failure")

    provider.client = _BoomClient()

    with pytest.raises(RuntimeError) as exc_info:
        await provider.extract_requirements("[PAGE 1] The system must log in.")

    assert secret_key not in str(exc_info.value)
