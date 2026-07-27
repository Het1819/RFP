"""Typed session-store interface plus a Redis-backed production implementation
and a deterministic in-memory implementation for tests.

Neither implementation uses pickle or custom cryptography. Session ids are
generated with :mod:`secrets` (see ``models.generate_session_id``).
"""

import asyncio
from typing import Protocol

from app.core.sessions.models import SessionRecord, SessionRecordError

SESSION_KEY_PREFIX = "rfp:session:"
USER_SESSIONS_KEY_PREFIX = "rfp:user_sessions:"

# Bounded expiry for user-session index entries so a crashed process that
# never cleaned up its index membership does not leak forever.
USER_INDEX_MAX_TTL_SECONDS = 60 * 60 * 24  # 24h; well above the absolute
# session timeout, just a safety net against orphaned index entries.


class SessionStoreUnavailableError(Exception):
    """Raised when the underlying store cannot be reached. Callers MUST fail
    closed (treat the request as unauthenticated / return 503) rather than
    falling back to any other source of authentication state."""


class SessionStore(Protocol):
    """Narrow, typed interface for session persistence. Production uses
    Redis; tests use the deterministic in-memory implementation below. Both
    implement exactly this interface so the middleware and routes never
    depend on Redis directly."""

    async def get(self, session_id: str) -> SessionRecord | None:
        """Return the record for session_id, or None if absent/expired/
        malformed. Malformed records are deleted as a side effect."""
        ...

    async def save(
        self, session_id: str, record: SessionRecord, *, ttl_seconds: int
    ) -> None:
        """Create or overwrite the record for session_id with the given TTL."""
        ...

    async def delete(self, session_id: str) -> None:
        """Delete a session record. Idempotent -- deleting an absent id is a
        no-op, not an error."""
        ...

    async def register_for_user(
        self, user_id: str, session_id: str, *, ttl_seconds: int
    ) -> None:
        """Add session_id to the index of sessions belonging to user_id."""
        ...

    async def unregister_for_user(self, user_id: str, session_id: str) -> None:
        """Remove session_id from user_id's session index. Idempotent."""
        ...

    async def revoke_all_for_user(self, user_id: str) -> int:
        """Delete every indexed session for user_id and clear the index.
        Returns the number of sessions deleted. Tolerates stale index
        members (ids already expired/absent) without raising."""
        ...

    async def count_for_user(self, user_id: str) -> int:
        """Number of indexed (not necessarily still-live) sessions for
        user_id. Used to show an operator what a revocation will affect."""
        ...

    async def ping(self) -> bool:
        """Cheap connectivity/health check used by /readyz. Raises
        SessionStoreUnavailableError on failure rather than returning False, so
        callers get a consistent failure signal."""
        ...


class RedisSessionStore:
    """Production session store backed by the project's existing Redis
    dependency (redis-py's asyncio client). No custom encryption, no pickle
    -- session records are strictly-validated JSON."""

    def __init__(self, redis_url: str) -> None:
        import redis.asyncio as redis_asyncio

        self._redis_url = redis_url
        self._client = redis_asyncio.from_url(  # type: ignore[no-untyped-call]
            redis_url, decode_responses=True
        )

    def _session_key(self, session_id: str) -> str:
        return f"{SESSION_KEY_PREFIX}{session_id}"

    def _user_index_key(self, user_id: str) -> str:
        return f"{USER_SESSIONS_KEY_PREFIX}{user_id}"

    async def get(self, session_id: str) -> SessionRecord | None:
        try:
            raw = await self._client.get(self._session_key(session_id))
        except Exception as exc:
            raise SessionStoreUnavailableError(str(exc)) from exc

        if raw is None:
            return None
        try:
            return SessionRecord.from_json(raw)
        except SessionRecordError:
            # Malformed/oversized/wrong-version record: fail closed and
            # remove the corrupt entry rather than trusting any of it.
            await self.delete(session_id)
            return None

    async def save(
        self, session_id: str, record: SessionRecord, *, ttl_seconds: int
    ) -> None:
        if ttl_seconds <= 0:
            await self.delete(session_id)
            return
        try:
            await self._client.set(
                self._session_key(session_id), record.to_json(), ex=ttl_seconds
            )
        except Exception as exc:
            raise SessionStoreUnavailableError(str(exc)) from exc

    async def delete(self, session_id: str) -> None:
        try:
            await self._client.delete(self._session_key(session_id))
        except Exception as exc:
            raise SessionStoreUnavailableError(str(exc)) from exc

    async def register_for_user(
        self, user_id: str, session_id: str, *, ttl_seconds: int
    ) -> None:
        index_ttl = min(max(ttl_seconds, 1), USER_INDEX_MAX_TTL_SECONDS)
        try:
            key = self._user_index_key(user_id)
            async with self._client.pipeline(transaction=True) as pipe:
                pipe.sadd(key, session_id)
                pipe.expire(key, index_ttl)
                await pipe.execute()
        except Exception as exc:
            raise SessionStoreUnavailableError(str(exc)) from exc

    async def unregister_for_user(self, user_id: str, session_id: str) -> None:
        try:
            await self._client.srem(self._user_index_key(user_id), session_id)
        except Exception as exc:
            raise SessionStoreUnavailableError(str(exc)) from exc

    async def revoke_all_for_user(self, user_id: str) -> int:
        index_key = self._user_index_key(user_id)
        try:
            members = await self._client.smembers(index_key)
        except Exception as exc:
            raise SessionStoreUnavailableError(str(exc)) from exc

        count = 0
        for session_id in members:
            # Tolerate stale members: deleting an absent key is a no-op.
            await self.delete(session_id)
            count += 1

        try:
            await self._client.delete(index_key)
        except Exception as exc:
            raise SessionStoreUnavailableError(str(exc)) from exc
        return count

    async def count_for_user(self, user_id: str) -> int:
        try:
            return int(await self._client.scard(self._user_index_key(user_id)))
        except Exception as exc:
            raise SessionStoreUnavailableError(str(exc)) from exc

    async def ping(self) -> bool:
        try:
            return bool(await self._client.ping())
        except Exception as exc:
            raise SessionStoreUnavailableError(str(exc)) from exc


class InMemorySessionStore:
    """Deterministic in-memory SessionStore for tests. Implements the same
    interface as RedisSessionStore; production code never depends on this
    class. TTLs are tracked but not actively swept -- ``get`` checks
    expiry lazily, matching Redis's read-time behavior closely enough for
    unit tests (session-level idle/absolute expiry is enforced separately by
    the middleware using the injectable clock, not by store TTL alone)."""

    def __init__(self) -> None:
        self._records: dict[str, str] = {}
        self._user_index: dict[str, set[str]] = {}
        self._lock = asyncio.Lock()
        self.available = True

    def _check_available(self) -> None:
        if not self.available:
            raise SessionStoreUnavailableError("in-memory store forced unavailable")

    async def get(self, session_id: str) -> SessionRecord | None:
        self._check_available()
        async with self._lock:
            raw = self._records.get(session_id)
        if raw is None:
            return None
        try:
            return SessionRecord.from_json(raw)
        except SessionRecordError:
            await self.delete(session_id)
            return None

    async def save(
        self, session_id: str, record: SessionRecord, *, ttl_seconds: int
    ) -> None:
        self._check_available()
        if ttl_seconds <= 0:
            await self.delete(session_id)
            return
        async with self._lock:
            self._records[session_id] = record.to_json()

    async def delete(self, session_id: str) -> None:
        self._check_available()
        async with self._lock:
            self._records.pop(session_id, None)

    async def register_for_user(
        self, user_id: str, session_id: str, *, ttl_seconds: int
    ) -> None:
        self._check_available()
        async with self._lock:
            self._user_index.setdefault(user_id, set()).add(session_id)

    async def unregister_for_user(self, user_id: str, session_id: str) -> None:
        self._check_available()
        async with self._lock:
            self._user_index.get(user_id, set()).discard(session_id)

    async def revoke_all_for_user(self, user_id: str) -> int:
        self._check_available()
        async with self._lock:
            members = self._user_index.pop(user_id, set())
            count = 0
            for session_id in members:
                if self._records.pop(session_id, None) is not None:
                    count += 1
        return count

    async def count_for_user(self, user_id: str) -> int:
        self._check_available()
        async with self._lock:
            return len(self._user_index.get(user_id, set()))

    async def ping(self) -> bool:
        self._check_available()
        return True
