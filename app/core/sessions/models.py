"""Typed, strictly-validated server-side session record.

Stored as JSON in Redis under ``rfp:session:<session-id>``. Never pickled.
The browser only ever holds the opaque session id -- none of these fields are
ever placed in the cookie.
"""

import json
import re
import secrets

from pydantic import BaseModel, ConfigDict, ValidationError, model_validator

CURRENT_SESSION_VERSION = 1

# Generous bound on serialized record size; real records are a few hundred
# bytes. Anything larger is treated as corrupt/hostile and rejected.
MAX_SESSION_RECORD_BYTES = 4096

# secrets.token_urlsafe(32) always yields a fixed-length string for a given
# Python build; compute it once instead of hardcoding a length that could
# drift with the stdlib implementation.
SESSION_ID_BYTES = 32
SESSION_ID_LENGTH = len(secrets.token_urlsafe(SESSION_ID_BYTES))
_SESSION_ID_CHARSET = re.compile(r"^[A-Za-z0-9_-]+$")


class SessionRecordError(ValueError):
    """Raised when a stored session record is malformed, oversized, or of an
    unsupported version. Callers must treat this as "no valid session" and
    fail closed -- never attempt to partially trust the record."""


class SessionRecord(BaseModel):
    """Server-side session state. All fields are internal-only: never
    serialized into the browser cookie."""

    model_config = ConfigDict(extra="forbid")

    version: int = CURRENT_SESSION_VERSION
    created_at: float
    last_activity_at: float
    csrf_token: str | None = None
    user_id: str | None = None
    org_id: str | None = None
    authenticated_at: float | None = None

    @model_validator(mode="after")
    def _validate_authenticated_fields(self) -> "SessionRecord":
        identity_fields = (self.user_id, self.org_id, self.authenticated_at)
        any_set = any(f is not None for f in identity_fields)
        all_set = all(f is not None for f in identity_fields)
        if any_set and not all_set:
            raise ValueError(
                "authenticated session records require user_id, org_id, and "
                "authenticated_at to be set together"
            )
        return self

    def is_authenticated(self) -> bool:
        return self.user_id is not None

    def to_json(self) -> str:
        return self.model_dump_json()

    @classmethod
    def from_json(cls, raw: str | bytes) -> "SessionRecord":
        if isinstance(raw, bytes):
            if len(raw) > MAX_SESSION_RECORD_BYTES:
                raise SessionRecordError("session record exceeds maximum size")
            try:
                text = raw.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise SessionRecordError("session record is not valid utf-8") from exc
        else:
            text = raw
            if len(text.encode("utf-8")) > MAX_SESSION_RECORD_BYTES:
                raise SessionRecordError("session record exceeds maximum size")

        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise SessionRecordError("session record is not valid JSON") from exc

        if not isinstance(data, dict):
            raise SessionRecordError("session record must be a JSON object")

        if data.get("version") != CURRENT_SESSION_VERSION:
            raise SessionRecordError(
                f"unsupported session record version: {data.get('version')!r}"
            )

        try:
            return cls.model_validate(data)
        except ValidationError as exc:
            raise SessionRecordError("session record failed schema validation") from exc


def generate_session_id() -> str:
    """Cryptographically secure, URL-safe, >=256-bit opaque session id."""
    return secrets.token_urlsafe(SESSION_ID_BYTES)


def is_valid_session_id(candidate: str) -> bool:
    """Validate format/charset/length *before* any Redis lookup is attempted."""
    if len(candidate) != SESSION_ID_LENGTH:
        return False
    return bool(_SESSION_ID_CHARSET.match(candidate))
