import logging
import secrets

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)

_PLACEHOLDER = "replace-this-with-a-long-random-value"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    APP_ENV: str = "development"
    APP_SECRET_KEY: str = Field(default_factory=lambda: secrets.token_hex(32))
    SESSION_SECRET_KEY: str | None = None
    AUTH_MODE: str = "dev"
    OIDC_ISSUER_URL: str | None = None
    OIDC_CLIENT_ID: str | None = None
    OIDC_CLIENT_SECRET: str | None = None
    OIDC_REDIRECT_URI: str | None = None

    DATABASE_URL: str = (
        "postgresql+psycopg://rfp_user:rfp_password@localhost:5432/rfp_architect"
    )
    REDIS_URL: str = "redis://localhost:6379/0"
    QUEUE_ENABLED: bool = False
    JOB_MAX_RETRIES: int = 3
    JOB_TIMEOUT_SECONDS: int = 300
    JOB_RETRY_BACKOFF_SECONDS: int = 5
    STORAGE_BACKEND: str = "local"
    LOCAL_STORAGE_PATH: str = "./data"
    MAX_UPLOAD_SIZE: int = 10 * 1024 * 1024  # 10 MB default
    LLM_PROVIDER: str = "fake"
    ANTHROPIC_API_KEY: str = ""
    LLM_MODEL: str = ""
    ENABLE_LLM_TELEMETRY: bool = True
    ENABLE_LLM_DEBUG_PAYLOAD_LOGGING: bool = False

    @field_validator("APP_SECRET_KEY")
    @classmethod
    def warn_weak_secret(cls, v: str, info: object) -> str:
        data = getattr(info, "data", {})
        if data.get("APP_ENV", "development") != "development":
            if v == _PLACEHOLDER or len(v) < 32:
                logger.warning(
                    "APP_SECRET_KEY is weak or uses the default placeholder "
                    "in a non-development environment. Set a strong secret."
                )
        return v

    @field_validator("SESSION_SECRET_KEY")
    @classmethod
    def validate_session_secret(cls, v: str | None, info: object) -> str | None:
        if not v:
            return "dev-deterministic-fallback-secret-key-at-least-32-chars-long"
        return v

    @model_validator(mode="after")
    def validate_auth_config(self) -> "Settings":
        if self.APP_ENV not in ("development", "local", "test"):
            if self.AUTH_MODE == "dev":
                raise ValueError(
                    "AUTH_MODE cannot be 'dev' in production-like environments"
                )
            if (
                not self.SESSION_SECRET_KEY
                or self.SESSION_SECRET_KEY == _PLACEHOLDER
                or len(self.SESSION_SECRET_KEY) < 32
                or self.SESSION_SECRET_KEY
                == "dev-deterministic-fallback-secret-key-at-least-32-chars-long"
            ):
                raise ValueError(
                    "SESSION_SECRET_KEY must be set, secure, and at least 32 "
                    "characters in production-like environments"
                )
            if self.ENABLE_LLM_DEBUG_PAYLOAD_LOGGING:
                raise ValueError(
                    "ENABLE_LLM_DEBUG_PAYLOAD_LOGGING must be False "
                    "in production-like environments"
                )
            if self.QUEUE_ENABLED and (
                not self.REDIS_URL
                or self.REDIS_URL == _PLACEHOLDER
                or len(self.REDIS_URL.strip()) == 0
            ):
                raise ValueError(
                    "REDIS_URL must be configured when QUEUE_ENABLED is "
                    "True in production-like environments"
                )

        if self.AUTH_MODE not in ("dev", "session", "oidc"):
            raise ValueError("AUTH_MODE must be one of 'dev', 'session', 'oidc'")

        if self.AUTH_MODE == "oidc":
            for name, val in [
                ("OIDC_ISSUER_URL", self.OIDC_ISSUER_URL),
                ("OIDC_CLIENT_ID", self.OIDC_CLIENT_ID),
                ("OIDC_CLIENT_SECRET", self.OIDC_CLIENT_SECRET),
                ("OIDC_REDIRECT_URI", self.OIDC_REDIRECT_URI),
            ]:
                if not val or val == _PLACEHOLDER or len(val.strip()) == 0:
                    raise ValueError(f"{name} is required when AUTH_MODE is 'oidc'")
        return self


settings = Settings()
