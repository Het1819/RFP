import logging
import secrets

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)

_PLACEHOLDER = "replace-this-with-a-long-random-value"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    APP_ENV: str = "development"
    APP_SECRET_KEY: str = Field(default_factory=lambda: secrets.token_hex(32))
    DATABASE_URL: str = (
        "postgresql+psycopg://rfp_user:rfp_password@localhost:5432/rfp_architect"
    )
    REDIS_URL: str = "redis://localhost:6379/0"
    STORAGE_BACKEND: str = "local"
    LOCAL_STORAGE_PATH: str = "./data"
    MAX_UPLOAD_SIZE: int = 10 * 1024 * 1024  # 10 MB default
    LLM_PROVIDER: str = "fake"
    ANTHROPIC_API_KEY: str = ""
    LLM_MODEL: str = ""

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


settings = Settings()
