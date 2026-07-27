"""Server-side session middleware.

Replaces the Phase A1 client-side ``SimpleSessionMiddleware``. The browser
cookie carries only an opaque session id; all session state (user_id,
org_id, csrf_token, timestamps) lives server-side in Redis (or the
in-memory test double), keyed by that id.

Activity definition
--------------------
"Activity" is any request routed through this middleware to a path other
than the exempt infrastructure paths below. Those exempt paths never touch
the session store at all, so they can never refresh (or expire) a session:

- ``/static/*``           (asset serving)
- ``/healthz``, ``/health`` (liveness)
- ``/readyz``              (readiness -- checks Redis itself, separately)
- ``/metrics``             (scraping)

Every other request that carries a valid session cookie resets that
session's idle-expiry clock (``last_activity_at``) to "now". It never moves
the session's absolute-expiry anchor (``created_at`` for anonymous sessions,
``authenticated_at`` once logged in).

Fail-closed behavior
---------------------
If the session store cannot be reached while resolving the incoming cookie,
this middleware returns ``503`` immediately for every non-exempt path,
without calling the downstream route at all. It never falls back to
reconstructing authentication state from the browser.
"""

import logging
import secrets
from collections.abc import Iterator, MutableMapping
from typing import Any, cast

from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response
from starlette.types import ASGIApp

from app.core.observability import MetricsRegistry
from app.core.sessions.clock import Clock, SystemClock
from app.core.sessions.models import (
    SessionRecord,
    generate_session_id,
    is_valid_session_id,
)
from app.core.sessions.store import SessionStore, SessionStoreUnavailableError

logger = logging.getLogger(__name__)

_EXEMPT_EXACT = {"/healthz", "/health", "/readyz", "/metrics"}
_EXEMPT_PREFIXES = ("/static",)

_SESSION_KEYS = ("user_id", "org_id", "csrf_token")


class SessionProxy(MutableMapping[str, str]):
    """Dict-like view over a SessionRecord's three mutable fields, matching
    the subset of the mapping interface routes already use
    (``[]``, ``.get()``, ``in``, ``.clear()``)."""

    def __init__(self, record: SessionRecord) -> None:
        self._record = record

    def __getitem__(self, key: str) -> str:
        if key not in _SESSION_KEYS:
            raise KeyError(key)
        value = getattr(self._record, key)
        if value is None:
            raise KeyError(key)
        return cast(str, value)

    def __setitem__(self, key: str, value: str) -> None:
        if key not in _SESSION_KEYS:
            raise KeyError(f"unsupported session key: {key!r}")
        setattr(self._record, key, value)

    def __delitem__(self, key: str) -> None:
        if key not in _SESSION_KEYS:
            raise KeyError(key)
        setattr(self._record, key, None)

    def __iter__(self) -> Iterator[str]:
        for key in _SESSION_KEYS:
            if getattr(self._record, key) is not None:
                yield key

    def __len__(self) -> int:
        return sum(1 for _ in self)

    def clear(self) -> None:
        for key in _SESSION_KEYS:
            setattr(self._record, key, None)


def _is_exempt(path: str) -> bool:
    if path in _EXEMPT_EXACT:
        return True
    return any(path.startswith(prefix) for prefix in _EXEMPT_PREFIXES)


def _absolute_anchor(record: SessionRecord) -> float:
    if record.authenticated_at is not None:
        return record.authenticated_at
    return record.created_at


class ServerSessionMiddleware(BaseHTTPMiddleware):
    def __init__(
        self,
        app: ASGIApp,
        *,
        default_store: SessionStore,
        cookie_name: str,
        idle_timeout_seconds: int,
        absolute_timeout_seconds: int,
        https_only: bool,
        default_clock: Clock | None = None,
    ) -> None:
        super().__init__(app)
        self._default_store = default_store
        self._default_clock: Clock = default_clock or SystemClock()
        self.cookie_name = cookie_name
        self.idle_timeout_seconds = idle_timeout_seconds
        self.absolute_timeout_seconds = absolute_timeout_seconds
        self.https_only = https_only

    def _resolve_store(self, request: Request) -> SessionStore:
        return cast(
            SessionStore,
            getattr(request.app.state, "session_store", None) or self._default_store,
        )

    def _resolve_clock(self, request: Request) -> Clock:
        return cast(
            Clock,
            getattr(request.app.state, "session_clock", None) or self._default_clock,
        )

    def _set_cookie(self, response: Response, session_id: str) -> None:
        response.set_cookie(
            key=self.cookie_name,
            value=session_id,
            httponly=True,
            samesite="lax",
            secure=self.https_only,
            path="/",
            # No max_age/expires: cookie is a browser-session cookie, cleared
            # on browser close. Server-side TTL enforces real expiration.
        )

    async def dispatch(self, request: Request, call_next: Any) -> Response:
        path = request.url.path
        if _is_exempt(path):
            return cast(Response, await call_next(request))

        store = self._resolve_store(request)
        clock = self._resolve_clock(request)

        raw_cookie = request.cookies.get(self.cookie_name)
        session_id: str | None = None
        record: SessionRecord | None = None

        if raw_cookie and is_valid_session_id(raw_cookie):
            try:
                record = await store.get(raw_cookie)
            except SessionStoreUnavailableError:
                MetricsRegistry.session_store_failures += 1
                logger.error("session store unavailable resolving session cookie")
                return JSONResponse(
                    {"detail": "Service temporarily unavailable"}, status_code=503
                )
            if record is not None:
                session_id = raw_cookie

        now = clock.now()
        if record is not None and session_id is not None:
            idle_expired = (now - record.last_activity_at) > self.idle_timeout_seconds
            absolute_expired = (
                now - _absolute_anchor(record)
            ) > self.absolute_timeout_seconds
            if idle_expired or absolute_expired:
                try:
                    await store.delete(session_id)
                    if record.user_id:
                        await store.unregister_for_user(record.user_id, session_id)
                except SessionStoreUnavailableError:
                    logger.error(
                        "session store unavailable cleaning up expired session"
                    )
                if idle_expired:
                    MetricsRegistry.session_idle_expirations += 1
                else:
                    MetricsRegistry.session_absolute_expirations += 1
                record = None
                session_id = None

        was_authenticated_user_id = record.user_id if record is not None else None

        if record is None:
            record = SessionRecord(created_at=now, last_activity_at=now)
        else:
            record.last_activity_at = now

        request.scope["session"] = SessionProxy(record)

        response = cast(Response, await call_next(request))

        if record.csrf_token is None:
            record.csrf_token = secrets.token_hex(32)

        now_after = clock.now()
        became_authenticated = (
            was_authenticated_user_id is None and record.user_id is not None
        )
        became_unauthenticated = (
            was_authenticated_user_id is not None and record.user_id is None
        )

        try:
            if became_unauthenticated:
                assert was_authenticated_user_id is not None
                if session_id is not None:
                    await store.delete(session_id)
                    await store.unregister_for_user(
                        was_authenticated_user_id, session_id
                    )
                    MetricsRegistry.session_logout_revocations += 1
                response.delete_cookie(self.cookie_name, path="/")
                return response

            if became_authenticated:
                assert record.user_id is not None
                # Pre-auth identifier must never remain valid post-login:
                # delete it, then mint an entirely new session id.
                if session_id is not None:
                    await store.delete(session_id)
                record.authenticated_at = now_after
                new_session_id = generate_session_id()
                ttl = max(
                    1, min(self.idle_timeout_seconds, self.absolute_timeout_seconds)
                )
                await store.save(new_session_id, record, ttl_seconds=ttl)
                await store.register_for_user(
                    record.user_id, new_session_id, ttl_seconds=ttl
                )
                MetricsRegistry.session_creations += 1
                self._set_cookie(response, new_session_id)
                return response

            # Normal path: same identity as at request start (both
            # authenticated throughout, or anonymous throughout).
            if session_id is None:
                session_id = generate_session_id()
                MetricsRegistry.session_creations += 1

            absolute_remaining = self.absolute_timeout_seconds - (
                now_after - _absolute_anchor(record)
            )
            ttl = max(1, min(self.idle_timeout_seconds, int(absolute_remaining)))

            await store.save(session_id, record, ttl_seconds=ttl)
            if record.user_id is not None:
                await store.register_for_user(
                    record.user_id, session_id, ttl_seconds=ttl
                )
            self._set_cookie(response, session_id)
        except SessionStoreUnavailableError:
            MetricsRegistry.session_store_failures += 1
            logger.error("session store unavailable persisting session")

        return response
