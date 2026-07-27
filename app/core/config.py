import logging
import secrets
from pathlib import Path
from urllib.parse import quote

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.core.client_ip import parse_trusted_proxy_ips
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

    # --- Edge / reverse-proxy trust (Phase A4) ---
    # Comma-separated list of EXACT trusted-proxy IPs (never a CIDR range
    # or wildcard). In the A4 topology this is the Nginx service's
    # deterministic backend-network IP. See app.core.client_ip.
    TRUSTED_PROXY_IPS: str | None = None
    # Comma-separated explicit hostnames this app answers to. No "*", no
    # empty entries, no localhost/loopback/dev hostnames in production.
    ALLOWED_HOSTS: str | None = None
    # Absolute HTTPS URL identifying the public-facing origin. Must agree
    # with ALLOWED_HOSTS and the Nginx server_name in production.
    PUBLIC_BASE_URL: str | None = None
    # Documented escape hatch for LOCAL VALIDATION ONLY: allows "localhost"/
    # loopback values in ALLOWED_HOSTS and PUBLIC_BASE_URL so the full
    # production-hardening code path can be exercised against the local
    # Nginx edge stack without public DNS. Off by default; a real
    # deployment must never set this.
    ALLOW_LOOPBACK_HOST: bool = False

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
    QUARANTINE_STORAGE_PATH: str = "./data/quarantine"
    QUARANTINE_CHUNK_SIZE_BYTES: int = 1024 * 1024  # 1 MiB
    MAX_DISPLAY_FILENAME_LENGTH: int = 255
    DOCX_DETECTION_MAX_MEMBERS: int = 5000
    PARSE_MAX_ATTEMPTS: int = 3
    PARSE_RETRY_BACKOFF_BASE_SECONDS: float = 2.0
    PARSE_RETRY_BACKOFF_MAX_SECONDS: float = 30.0
    PARSER_SERVICE_URL: str = "http://parser:8000"

    # --- DOCX content-policy inspection (Phase A5c) ---
    # Bounds for the zip-bomb defense in app.services.docx_content_policy.
    # Both are read from ZIP central-directory metadata only (info.file_size
    # / info.compress_size), never by decompressing member content.
    # 200 MiB is generously above any realistic legitimate DOCX (typical
    # proposal/knowledge documents with embedded images run low tens of MB
    # uncompressed) while still bounding worst-case memory/CPU exposure from
    # a maliciously crafted package.
    DOCX_MAX_UNCOMPRESSED_TOTAL_BYTES: int = 200 * 1024 * 1024  # 200 MiB
    # A 100:1 declared-uncompressed-to-compressed ratio is far beyond what
    # ordinary XML/text/image content inside a DOCX achieves (typically
    # single-digit to low double-digit ratios), so this catches classic
    # zip-bomb-style entries without rejecting genuine documents.
    DOCX_MAX_COMPRESSION_RATIO: int = 100

    # --- ClamAV malware scanning (Phase A5c) ---
    # clamd's own daemon-side StreamMaxLength is configured separately in
    # the clamd deployment; CLAMAV_STREAM_MAX_BYTES is this app's
    # client-side limit, enforced incrementally while streaming (never by
    # buffering the whole file) so an oversized upload is rejected before
    # more than one extra chunk is sent to the daemon.
    CLAMAV_HOST: str = "clamd"
    CLAMAV_PORT: int = 3310
    CLAMAV_CONNECT_TIMEOUT_SECONDS: float = 5.0
    CLAMAV_IO_TIMEOUT_SECONDS: float = 30.0
    CLAMAV_STREAM_MAX_BYTES: int = 10 * 1024 * 1024  # must be <= MAX_UPLOAD_SIZE
    CLAMAV_MAX_SIGNATURE_AGE_HOURS: int = 48
    # Maximum number of scan attempts (app.services.malware_scan.run_scan
    # invocations) a document may accumulate before it is left in
    # SCAN_FAILED as the operator-visible terminal-for-now state instead
    # of being eligible for a further bounded retry. The retry scheduler
    # (Task 6, worker wiring: app.services.malware_scan.prepare_scan_attempt,
    # called from both run_scan_sync and app.worker.scan_document_task) is
    # what reads this against Document.scan_attempt_count, under the same
    # row lock run_scan itself uses, to decide whether to re-arm SCANNING
    # for another attempt; run_scan only records the attempt count and
    # outcome.
    SCAN_MAX_ATTEMPTS: int = 3
    # Bounded-retry backoff for SCAN_FAILED outcomes (Task 6, worker
    # wiring). enqueue_scan_retry() computes delay = min(base * 2**(attempt
    # - 1), max) then applies +/-50% jitter. Both the real-queue path
    # (arq `_defer_by`) and the QUEUE_ENABLED=False sync fallback compute
    # the same delay value; the sync fallback just does not actually wait
    # on it (see app.services.malware_scan.run_scan_sync).
    SCAN_RETRY_BACKOFF_BASE_SECONDS: int = 5
    SCAN_RETRY_BACKOFF_MAX_SECONDS: int = 300
    # --- PDF content-policy inspection (Phase A5c) ---
    # The inspector itself runs in an isolated OS subprocess (see
    # app.services.pdf_inspector_subprocess) -- these settings bound both
    # the parent's wall-clock wait for that subprocess and the resource
    # limits the subprocess applies to itself before opening the
    # untrusted file. 10s CPU / 512 MiB address space is generous for
    # parsing PDF structural metadata (no rendering, no text extraction)
    # while still bounding worst-case resource exposure from a
    # maliciously crafted file. The wall-clock timeout is kept a little
    # above the CPU limit to allow for process startup and I/O wait.
    PDF_INSPECTION_TIMEOUT_SECONDS: float = 15.0
    PDF_INSPECTOR_CPU_SECONDS: int = 10
    PDF_INSPECTOR_MEMORY_BYTES: int = 512 * 1024 * 1024  # 512 MiB

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
    def effective_trusted_proxy_ips(self) -> frozenset[str]:
        return parse_trusted_proxy_ips(self.TRUSTED_PROXY_IPS)

    @property
    def allowed_hosts_list(self) -> list[str]:
        if not self.ALLOWED_HOSTS:
            return []
        return [h.strip() for h in self.ALLOWED_HOSTS.split(",") if h.strip()]

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
    def validate_clamav_config(self) -> "Settings":
        # Structural invariant enforced in every environment (not just
        # production): a client-side scan-stream limit greater than the
        # upload limit is always a misconfiguration -- no upload can ever
        # exceed MAX_UPLOAD_SIZE, so a larger CLAMAV_STREAM_MAX_BYTES would
        # be dead configuration masking a real bug.
        if self.CLAMAV_STREAM_MAX_BYTES > self.MAX_UPLOAD_SIZE:
            raise ValueError("CLAMAV_STREAM_MAX_BYTES must not exceed MAX_UPLOAD_SIZE")
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

        # 15: quarantine storage must be a real, distinct, absolute mounted
        # path in production-like environments - never the repo-relative
        # dev default, and never the same directory as clean document
        # storage (quarantine and clean documents must never share a root).
        if self.STORAGE_BACKEND == "local":
            if (
                self.QUARANTINE_STORAGE_PATH == "./data/quarantine"
                or not self.QUARANTINE_STORAGE_PATH
            ):
                raise ValueError(
                    "QUARANTINE_STORAGE_PATH must be set to a persistent "
                    "mounted path (not the './data/quarantine' development "
                    "default) in production-like environments"
                )
            if not self.QUARANTINE_STORAGE_PATH.startswith("/"):
                raise ValueError(
                    "QUARANTINE_STORAGE_PATH must be an absolute path to a "
                    "mounted volume in production-like environments"
                )
            if (
                Path(self.QUARANTINE_STORAGE_PATH).resolve()
                == Path(self.LOCAL_STORAGE_PATH).resolve()
            ):
                raise ValueError(
                    "QUARANTINE_STORAGE_PATH must differ from "
                    "LOCAL_STORAGE_PATH - quarantine and clean document "
                    "storage must be separate directories"
                )

        # 16: cookie name must be explicitly set (never empty/blank), and in
        # production must use the `__Host-` prefix. Browsers only accept a
        # `__Host-` cookie when it also has Secure, Path=/, and no Domain --
        # ServerSessionMiddleware always sets those three in production
        # (https_only=True, path="/", no domain param), so requiring the
        # prefix here is a real behavioral guarantee, not just a name.
        if not self.SESSION_COOKIE_NAME or not self.SESSION_COOKIE_NAME.strip():
            raise ValueError(
                "SESSION_COOKIE_NAME must not be empty in production-like environments"
            )
        if not self.SESSION_COOKIE_NAME.startswith("__Host-"):
            raise ValueError(
                "SESSION_COOKIE_NAME must use the '__Host-' prefix in "
                "production-like environments (requires Secure, Path=/, "
                "and no Domain -- all of which this app always sets)"
            )

        # Edge / reverse-proxy trust (Phase A4): fail closed if no exact
        # trusted proxy is configured -- this app must never guess.
        try:
            trusted_ips = self.effective_trusted_proxy_ips
        except ValueError as exc:
            raise ValueError(f"TRUSTED_PROXY_IPS is invalid: {exc}") from exc
        if not trusted_ips:
            raise ValueError(
                "TRUSTED_PROXY_IPS must be set to the exact Nginx backend "
                "IP in production-like environments; forwarded-header "
                "trust must never be left unconfigured"
            )

        # ALLOWED_HOSTS: explicit, no wildcard, no empty entries, no
        # dev/loopback hostnames.
        hosts = self.allowed_hosts_list
        if not hosts:
            raise ValueError(
                "ALLOWED_HOSTS must be set explicitly in production-like environments"
            )
        _forbidden_hosts = {"*", "0.0.0.0", ""}
        _loopback_hosts = {"localhost", "127.0.0.1", "::1"}
        if not self.ALLOW_LOOPBACK_HOST:
            _forbidden_hosts = _forbidden_hosts | _loopback_hosts
        for host in hosts:
            if host in _forbidden_hosts:
                raise ValueError(
                    f"ALLOWED_HOSTS contains a disallowed value in "
                    f"production-like environments: {host!r}"
                )

        # PUBLIC_BASE_URL: absolute HTTPS URL, exact hostname, no
        # userinfo/fragment/unexpected path, standard port unless explicit.
        if not self.PUBLIC_BASE_URL:
            raise ValueError(
                "PUBLIC_BASE_URL must be set in production-like environments"
            )
        from urllib.parse import urlsplit

        parsed = urlsplit(self.PUBLIC_BASE_URL)
        if parsed.scheme != "https":
            raise ValueError("PUBLIC_BASE_URL must use the https scheme")
        if not parsed.hostname:
            raise ValueError("PUBLIC_BASE_URL must include a hostname")
        if parsed.username or parsed.password:
            raise ValueError("PUBLIC_BASE_URL must not contain userinfo")
        if parsed.fragment:
            raise ValueError("PUBLIC_BASE_URL must not contain a fragment")
        if parsed.path not in ("", "/"):
            raise ValueError("PUBLIC_BASE_URL must not contain a path")
        if parsed.port is not None and parsed.port != 443:
            raise ValueError(
                "PUBLIC_BASE_URL must use the standard HTTPS port (443) "
                "unless explicitly documented otherwise"
            )
        if parsed.hostname not in hosts:
            raise ValueError(
                "PUBLIC_BASE_URL hostname must be included in ALLOWED_HOSTS"
            )

        return self


settings = Settings()
