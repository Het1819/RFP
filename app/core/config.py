import logging
import secrets
from urllib.parse import quote

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.core.secrets import read_secret_file

logger = logging.getLogger(__name__)

_PLACEHOLDER = "replace-this-with-a-long-random-value"

_PRODUCTION_ENVS_EXCLUDED = ("development", "local", "test")

# Field -> the *_FILE settings name that, if set, is read via
# read_secret_file() and overrides the field before any other validation
# runs. A file that exists but cannot be read (missing/empty/oversized/not
# a regular file) fails Settings() construction outright -- it never
# silently falls through to a weaker default.
_SECRET_FILE_FIELDS: dict[str, str] = {
    "APP_SECRET_KEY": "APP_SECRET_KEY_FILE",
    "SESSION_SECRET_KEY": "SESSION_SECRET_KEY_FILE",
    "LOGIN_THROTTLE_SECRET": "LOGIN_THROTTLE_SECRET_FILE",
    "POSTGRES_PASSWORD": "POSTGRES_PASSWORD_FILE",
    "REDIS_PASSWORD": "REDIS_PASSWORD_FILE",
    "ANTHROPIC_API_KEY": "ANTHROPIC_API_KEY_FILE",
}

_KNOWN_PLACEHOLDER_VALUES = {
    _PLACEHOLDER,
    "replace-with-a-strong-random-session-secret-at-least-32-chars",
    "replace-with-a-strong-random-throttle-secret-at-least-32-chars",
    "changeme",
    "change-me",
    "password",
    "rfp_password",
    "dev-deterministic-fallback-secret-key-at-least-32-chars-long",
    "dev-deterministic-fallback-throttle-secret-at-least-32-chars",
}


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

    # --- Production database configuration (Phase A3) ---
    # When DB_HOST is set, `effective_database_url` builds the connection
    # string from these non-sensitive components plus POSTGRES_PASSWORD (or
    # POSTGRES_PASSWORD_FILE), safely URL-encoded. DATABASE_URL remains
    # authoritative for dev/test and externally-managed database
    # deployments where DB_HOST is left unset.
    DB_HOST: str | None = None
    DB_PORT: int = 5432
    DB_NAME: str = "rfp_architect"
    DB_USER: str = "rfp_user"
    POSTGRES_PASSWORD: str | None = None
    # Documented escape hatch for a single-container deployment where
    # PostgreSQL genuinely runs on the same host/network namespace as the
    # app (not the multi-container Compose topology). Off by default.
    DB_ALLOW_LOCALHOST: bool = False

    # --- Production Redis configuration (Phase A3) ---
    # Same pattern as above: when REDIS_HOST is set, `effective_redis_url`
    # builds an authenticated connection string from these components plus
    # REDIS_PASSWORD (or REDIS_PASSWORD_FILE).
    REDIS_HOST: str | None = None
    REDIS_PORT: int = 6379
    REDIS_DB: int = 0
    REDIS_PASSWORD: str | None = None
    # Documented escape hatch: set only when Redis is a separately managed,
    # already-authenticated service (e.g. a cloud provider's managed Redis)
    # whose connection string legitimately has no password segment visible
    # to this app (auth handled via IAM/VPC/managed-identity instead).
    REDIS_EXTERNALLY_MANAGED: bool = False

    # --- Secret files (Phase A3) ---
    # If set, each *_FILE value is read via read_secret_file() and
    # overrides the corresponding field above before any other validation
    # runs (see _load_secret_files). A file that cannot be read fails
    # Settings() construction -- it is never silently ignored.
    APP_SECRET_KEY_FILE: str | None = None
    SESSION_SECRET_KEY_FILE: str | None = None
    LOGIN_THROTTLE_SECRET_FILE: str | None = None
    POSTGRES_PASSWORD_FILE: str | None = None
    REDIS_PASSWORD_FILE: str | None = None
    ANTHROPIC_API_KEY_FILE: str | None = None

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

    @model_validator(mode="before")
    @classmethod
    def _load_secret_files(cls, data: object) -> object:
        if not isinstance(data, dict):
            return data
        for field_name, file_field_name in _SECRET_FILE_FIELDS.items():
            file_path = data.get(file_field_name)
            if file_path:
                # Deliberately not wrapped in try/except: a configured
                # secret file that can't be read must fail Settings()
                # construction, not fall back to a weaker default.
                data[field_name] = read_secret_file(file_path)
        return data

    @property
    def effective_database_url(self) -> str:
        """Safe, typed production database URL.

        When DB_HOST is configured, builds the SQLAlchemy URL from the
        non-sensitive components plus POSTGRES_PASSWORD, with the user and
        password percent-encoded so reserved characters (`:`, `/`, `@`,
        etc.) in either cannot corrupt the URL. Falls back to DATABASE_URL
        when DB_HOST is unset (dev/test/externally-managed databases).
        """
        if not self.DB_HOST:
            return self.DATABASE_URL
        user = quote(self.DB_USER, safe="")
        password = quote(self.POSTGRES_PASSWORD or "", safe="")
        return (
            f"postgresql+psycopg://{user}:{password}"
            f"@{self.DB_HOST}:{self.DB_PORT}/{self.DB_NAME}"
        )

    @property
    def effective_redis_url(self) -> str:
        """Safe, typed authenticated Redis URL.

        When REDIS_HOST is configured, builds an authenticated `redis://`
        URL from the non-sensitive components plus REDIS_PASSWORD, safely
        URL-encoded. Falls back to REDIS_URL when REDIS_HOST is unset.
        """
        if not self.REDIS_HOST:
            return self.REDIS_URL
        if self.REDIS_PASSWORD:
            password = quote(self.REDIS_PASSWORD, safe="")
            auth = f":{password}@"
        else:
            auth = ""
        return f"redis://{auth}{self.REDIS_HOST}:{self.REDIS_PORT}/{self.REDIS_DB}"

    @property
    def effective_session_redis_url(self) -> str:
        return self.SESSION_REDIS_URL or self.effective_redis_url

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

    @model_validator(mode="after")
    def validate_production_hardening(self) -> "Settings":
        """Phase A3: fail-closed production startup checks beyond A1/A2's
        session/CSRF-focused validation. These RAISE in production-like
        environments -- they never merely warn or log."""
        if self.APP_ENV in _PRODUCTION_ENVS_EXCLUDED:
            return self

        # 1/2/18: application secret must be strong and not a placeholder.
        if (
            not self.APP_SECRET_KEY
            or self.APP_SECRET_KEY == _PLACEHOLDER
            or self.APP_SECRET_KEY in _KNOWN_PLACEHOLDER_VALUES
            or len(self.APP_SECRET_KEY) < 32
        ):
            raise ValueError(
                "APP_SECRET_KEY must be set, secure, and at least 32 "
                "characters in production-like environments"
            )

        # 18: reject known placeholder values across every secret field,
        # regardless of length (catches short, obviously-fake values too).
        for secret_name, secret_value in (
            ("SESSION_SECRET_KEY", self.SESSION_SECRET_KEY),
            ("LOGIN_THROTTLE_SECRET", self.LOGIN_THROTTLE_SECRET),
            ("POSTGRES_PASSWORD", self.POSTGRES_PASSWORD),
            ("REDIS_PASSWORD", self.REDIS_PASSWORD),
        ):
            if secret_value and secret_value in _KNOWN_PLACEHOLDER_VALUES:
                raise ValueError(
                    f"{secret_name} is a known placeholder value and must "
                    "be replaced in production-like environments"
                )

        # 5/6: database configuration must not use the repository default,
        # and if using the host/port/user/DB-name form, a real password.
        if self.DB_HOST:
            if (
                not self.POSTGRES_PASSWORD
                or self.POSTGRES_PASSWORD in _KNOWN_PLACEHOLDER_VALUES
                or len(self.POSTGRES_PASSWORD) < 8
            ):
                raise ValueError(
                    "POSTGRES_PASSWORD (or POSTGRES_PASSWORD_FILE) must be "
                    "set to a real password in production-like environments "
                    "when DB_HOST is configured"
                )
            db_host_is_local = self.DB_HOST in ("localhost", "127.0.0.1")
            if db_host_is_local and not self.DB_ALLOW_LOCALHOST:
                raise ValueError(
                    "DB_HOST=localhost is rejected in production-like "
                    "environments; use the Compose service hostname, or "
                    "set DB_ALLOW_LOCALHOST=true for the documented "
                    "single-container exception"
                )
        repo_default_db_url = (
            "postgresql+psycopg://rfp_user:rfp_password@localhost:5432/rfp_architect"
        )
        if self.effective_database_url == repo_default_db_url:
            raise ValueError(
                "The repository default DATABASE_URL is rejected in "
                "production-like environments"
            )
        if "rfp_password" in self.effective_database_url:
            raise ValueError(
                "The repository default database password is rejected in "
                "production-like environments"
            )

        # 7: Redis must be authenticated unless explicitly declared external.
        redis_url = self.effective_redis_url
        redis_has_password = "@" in redis_url.split("://", 1)[-1].split("/", 1)[0]
        if not redis_has_password and not self.REDIS_EXTERNALLY_MANAGED:
            raise ValueError(
                "Redis must be configured with authentication in "
                "production-like environments (REDIS_PASSWORD or "
                "REDIS_PASSWORD_FILE), unless REDIS_EXTERNALLY_MANAGED=true "
                "is explicitly set for a documented externally-managed "
                "Redis deployment"
            )

        # 8/9/10/11: LLM provider must be the real, fully-configured
        # Anthropic provider -- never fake, never an unsupported name.
        if self.LLM_PROVIDER not in ("fake", "anthropic"):
            raise ValueError(
                f"Unsupported LLM_PROVIDER {self.LLM_PROVIDER!r}; must be "
                "'fake' or 'anthropic'"
            )
        if self.LLM_PROVIDER == "fake":
            raise ValueError(
                "LLM_PROVIDER cannot be 'fake' in production-like environments"
            )
        if self.LLM_PROVIDER == "anthropic":
            if not self.ANTHROPIC_API_KEY or len(self.ANTHROPIC_API_KEY.strip()) == 0:
                raise ValueError(
                    "ANTHROPIC_API_KEY (or ANTHROPIC_API_KEY_FILE) is "
                    "required in production-like environments when "
                    "LLM_PROVIDER is 'anthropic'"
                )
            if not self.LLM_MODEL or len(self.LLM_MODEL.strip()) == 0:
                raise ValueError(
                    "LLM_MODEL must be set explicitly in production-like "
                    "environments when LLM_PROVIDER is 'anthropic'"
                )

        # 14: local storage must be a real mounted/persistent path, not the
        # repository-relative development default.
        if self.STORAGE_BACKEND == "local":
            if self.LOCAL_STORAGE_PATH == "./data" or not self.LOCAL_STORAGE_PATH:
                raise ValueError(
                    "LOCAL_STORAGE_PATH must be set to a persistent mounted "
                    "path (not the './data' development default) in "
                    "production-like environments"
                )
            if not self.LOCAL_STORAGE_PATH.startswith("/"):
                raise ValueError(
                    "LOCAL_STORAGE_PATH must be an absolute path to a "
                    "mounted volume in production-like environments"
                )

        # 15: cookie name must be explicitly set (never empty/blank).
        if not self.SESSION_COOKIE_NAME or not self.SESSION_COOKIE_NAME.strip():
            raise ValueError(
                "SESSION_COOKIE_NAME must not be empty in production-like environments"
            )

        return self


settings = Settings()
