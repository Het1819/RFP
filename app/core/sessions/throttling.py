"""Redis-backed login throttling.

Three independent, atomically-enforced limits:

1. account + source-IP  (tightest -- catches credential stuffing on one
   account from one machine)
2. source-IP alone       (catches spray attacks across many accounts)
3. account across IPs    (catches distributed attacks on one account)

No permanent lockout: every counter is a bounded fixed window with its own
TTL, and the maximum cooldown communicated to the client is capped
(``LOGIN_THROTTLE_MAX_COOLDOWN_SECONDS``, default 5 minutes).

Identifier privacy: the submitted email is normalized exactly as the login
route normalizes it, then only an HMAC-SHA256 of the normalized email
(keyed by a dedicated server-side secret, never the session or app secret)
is ever used in a Redis key. The raw email is never stored in a throttle
key, log line, or audit event.
"""

import asyncio
import hashlib
import hmac
from dataclasses import dataclass
from typing import Protocol

from app.core.sessions.clock import Clock, SystemClock

THROTTLE_KEY_PREFIX = "rfp:throttle:"


class ThrottleStoreUnavailableError(Exception):
    """Raised when the throttle counters cannot be reached. Callers must
    fail closed on the security-relevant side: when the throttle store is
    down, login should be refused (503), never silently allowed to bypass
    throttling."""


def normalize_email(email: str) -> str:
    """Must exactly match the normalization the login route applies before
    looking up a user, so throttle keys and account lookups agree."""
    return email.strip().lower()


class ThrottleStore(Protocol):
    async def get_count(self, key: str) -> int: ...

    async def increment(self, key: str, *, window_seconds: int) -> int:
        """Atomically increment key, setting a TTL only when the key is
        newly created (fixed window -- does not reset on every hit)."""
        ...

    async def reset(self, key: str) -> None: ...

    async def ping(self) -> bool: ...


_INCR_WITH_TTL_LUA = """
local count = redis.call('INCR', KEYS[1])
if count == 1 then
    redis.call('EXPIRE', KEYS[1], ARGV[1])
end
return count
"""


class RedisThrottleStore:
    """Production throttle counters. Uses a Lua script so INCR + first-time
    EXPIRE happen as a single atomic Redis operation -- concurrent requests
    cannot race past the limit via separate read/increment/write steps."""

    def __init__(self, redis_url: str) -> None:
        import redis.asyncio as redis_asyncio

        self._client = redis_asyncio.from_url(  # type: ignore[no-untyped-call]
            redis_url, decode_responses=True
        )
        self._incr_script = self._client.register_script(_INCR_WITH_TTL_LUA)

    async def get_count(self, key: str) -> int:
        try:
            raw = await self._client.get(key)
        except Exception as exc:
            raise ThrottleStoreUnavailableError(str(exc)) from exc
        return int(raw) if raw is not None else 0

    async def increment(self, key: str, *, window_seconds: int) -> int:
        try:
            result = await self._incr_script(keys=[key], args=[window_seconds])
        except Exception as exc:
            raise ThrottleStoreUnavailableError(str(exc)) from exc
        return int(result)

    async def reset(self, key: str) -> None:
        try:
            await self._client.delete(key)
        except Exception as exc:
            raise ThrottleStoreUnavailableError(str(exc)) from exc

    async def ping(self) -> bool:
        try:
            return bool(await self._client.ping())
        except Exception as exc:
            raise ThrottleStoreUnavailableError(str(exc)) from exc


class InMemoryThrottleStore:
    """Deterministic in-memory ThrottleStore for tests. Uses an
    asyncio.Lock so concurrent increments within the same test process
    cannot race, mirroring the atomicity Redis's Lua script provides in
    production."""

    def __init__(self, clock: Clock | None = None) -> None:
        self._counts: dict[str, tuple[int, float]] = {}
        self._lock = asyncio.Lock()
        self._clock: Clock = clock or SystemClock()
        self.available = True

    def _check_available(self) -> None:
        if not self.available:
            raise ThrottleStoreUnavailableError(
                "in-memory throttle store forced unavailable"
            )

    async def get_count(self, key: str) -> int:
        self._check_available()
        async with self._lock:
            entry = self._counts.get(key)
            if entry is None:
                return 0
            count, expires_at = entry
            if self._clock.now() >= expires_at:
                del self._counts[key]
                return 0
            return count

    async def increment(self, key: str, *, window_seconds: int) -> int:
        self._check_available()
        async with self._lock:
            now = self._clock.now()
            entry = self._counts.get(key)
            if entry is None or now >= entry[1]:
                count = 1
                self._counts[key] = (count, now + window_seconds)
            else:
                count = entry[0] + 1
                self._counts[key] = (count, entry[1])
            return count

    async def reset(self, key: str) -> None:
        self._check_available()
        async with self._lock:
            self._counts.pop(key, None)

    async def ping(self) -> bool:
        self._check_available()
        return True


@dataclass
class ThrottleDecision:
    allowed: bool
    retry_after_seconds: int | None = None
    # Internal-only; never place in the HTTP response body or logs -- doing
    # so would reveal which limit tripped.
    _internal_reason: str | None = None


class LoginThrottle:
    """Combines identifier derivation with the three independent limits."""

    def __init__(
        self,
        store: ThrottleStore,
        secret: str,
        *,
        account_ip_max: int,
        account_ip_window_seconds: int,
        ip_max: int,
        ip_window_seconds: int,
        account_max: int,
        account_window_seconds: int,
        max_cooldown_seconds: int,
    ) -> None:
        self._store = store
        self._secret = secret
        self._account_ip_max = account_ip_max
        self._account_ip_window = account_ip_window_seconds
        self._ip_max = ip_max
        self._ip_window = ip_window_seconds
        self._account_max = account_max
        self._account_window = account_window_seconds
        self._max_cooldown = max_cooldown_seconds

    def _account_component(self, normalized_email: str) -> str:
        return hmac.new(
            self._secret.encode(), normalized_email.encode(), hashlib.sha256
        ).hexdigest()

    def _keys(self, normalized_email: str, source_ip: str) -> tuple[str, str, str]:
        acct = self._account_component(normalized_email)
        ip_key = f"{THROTTLE_KEY_PREFIX}ip:{source_ip}"
        acct_ip_key = f"{THROTTLE_KEY_PREFIX}acct_ip:{acct}:{source_ip}"
        acct_key = f"{THROTTLE_KEY_PREFIX}acct:{acct}"
        return ip_key, acct_ip_key, acct_key

    async def check(self, *, normalized_email: str, source_ip: str) -> ThrottleDecision:
        ip_key, acct_ip_key, acct_key = self._keys(normalized_email, source_ip)

        if await self._store.get_count(ip_key) >= self._ip_max:
            return ThrottleDecision(False, self._max_cooldown, "ip")
        if await self._store.get_count(acct_ip_key) >= self._account_ip_max:
            return ThrottleDecision(False, self._max_cooldown, "account_ip")
        if await self._store.get_count(acct_key) >= self._account_max:
            return ThrottleDecision(False, self._max_cooldown, "account")
        return ThrottleDecision(True)

    async def record_failure(self, *, normalized_email: str, source_ip: str) -> None:
        ip_key, acct_ip_key, acct_key = self._keys(normalized_email, source_ip)
        await self._store.increment(ip_key, window_seconds=self._ip_window)
        await self._store.increment(acct_ip_key, window_seconds=self._account_ip_window)
        await self._store.increment(acct_key, window_seconds=self._account_window)

    async def record_success(self, *, normalized_email: str, source_ip: str) -> None:
        _ip_key, acct_ip_key, acct_key = self._keys(normalized_email, source_ip)
        await self._store.reset(acct_ip_key)
        await self._store.reset(acct_key)
        # Deliberately NOT resetting the IP-wide counter: one successful
        # login must not erase an abusive IP's failure history and allow it
        # to bypass IP-level throttling against other accounts.
