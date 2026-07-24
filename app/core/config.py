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

    # --- Server-side session store (Phase A2) ---
    # Falls back to REDIS_URL when unset; see `effective_session_redis_url`.
    SESSION_REDIS_URL: str | None = None
    SESSION_COOKIE_NAME: str = "rfp_session"
    SESSION_IDLE_TIMEOUT_SECONDS: int = 900
    SESSION_ABSOLUTE_TIMEOUT_SECONDS: int = 28800

    # --- Login throttling (Phase A2) ---
    # Dedicated secret for HMAC-deriving throttle account keys. Must be
    # distinct from APP_SECRET_KEY/SESSION_SECRET_KEY in production.
    LOGIN_THROTTLE_SECRET: str | None = None
    LOGIN_THROTTLE_ACCOUNT_IP_MAX: int = 5
    LOGIN_THROTTLE_ACCOUNT_IP_WINDOW_SECONDS: int = 900
    LOGIN_THROTTLE_IP_MAX: int = 25
    LOGIN_THROTTLE_IP_WINDOW_SECONDS: int = 900
    LOGIN_THROTTLE_ACCOUNT_MAX: int = 20
    LOGIN_THROTTLE_ACCOUNT_WINDOW_SECONDS: int = 3600
    LOGIN_THROTTLE_MAX_COOLDOWN_SECONDS: int = 300

    @property
    def effective_session_redis_url(self) -> str:
        return self.SESSION_REDIS_URL or self.REDIS_URL

    @property
    def effective_login_throttle_secret(self) -> str:
        return (
            self.LOGIN_THROTTLE_SECRET
            or "dev-deterministic-fallback-throttle-secret-at-least-32-chars"
        )

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
    def validate_session_timeouts(self) -> "Settings":
        # Structural invariants enforced in every environment: a fail-open
        # (zero/negative/inverted) timeout configuration is never valid.
        if self.SESSION_IDLE_TIMEOUT_SECONDS <= 0:
            raise ValueError("SESSION_IDLE_TIMEOUT_SECONDS must be positive")
        if self.SESSION_ABSOLUTE_TIMEOUT_SECONDS <= 0:
            raise ValueError("SESSION_ABSOLUTE_TIMEOUT_SECONDS must be positive")
        if self.SESSION_IDLE_TIMEOUT_SECONDS >= self.SESSION_ABSOLUTE_TIMEOUT_SECONDS:
            raise ValueError(
                "SESSION_IDLE_TIMEOUT_SECONDS must be shorter than "
                "SESSION_ABSOLUTE_TIMEOUT_SECONDS"
            )
        return self

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
            if self.AUTH_MODE == "session":
                redis_url = self.effective_session_redis_url
                if (
                    not redis_url
                    or redis_url == _PLACEHOLDER
                    or len(redis_url.strip()) == 0
                ):
                    raise ValueError(
                        "SESSION_REDIS_URL (or REDIS_URL) must be configured "
                        "in production-like environments when AUTH_MODE is "
                        "'session'"
                    )
                if (
                    not self.LOGIN_THROTTLE_SECRET
                    or self.LOGIN_THROTTLE_SECRET == _PLACEHOLDER
                    or len(self.LOGIN_THROTTLE_SECRET) < 32
                    or self.LOGIN_THROTTLE_SECRET
                    == "dev-deterministic-fallback-throttle-secret-at-least-32-chars"
                ):
                    raise ValueError(
                        "LOGIN_THROTTLE_SECRET must be set, secure, and at "
                        "least 32 characters in production-like environments"
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
