"""Host-header allowlist enforcement.

Runs before authentication, session lookup, or route processing: an
invalid Host is rejected immediately, so a request that doesn't match a
known application hostname never touches Redis, the database, or any
route handler.

When no ALLOWED_HOSTS are configured (development/test), this middleware
is a no-op -- production-like environments are required (by
Settings.validate_production_hardening) to configure ALLOWED_HOSTS
explicitly, so the restriction only ever activates where it was
deliberately turned on.
"""

from typing import Any, cast

from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response
from starlette.types import ASGIApp

# Infra/health paths are checked directly over loopback (by the container's
# own Docker healthcheck, or internally over the private backend network),
# never through a browser -- their caller has no reason to send a Host
# header matching the public hostname. Exempting them matches the same
# paths ServerSessionMiddleware already treats as non-authenticating.
_EXEMPT_PATHS = {"/healthz", "/health", "/readyz", "/metrics"}


class HostValidationMiddleware(BaseHTTPMiddleware):
    def __init__(self, app: ASGIApp, *, allowed_hosts: list[str]) -> None:
        super().__init__(app)
        self.allowed_hosts = {h.lower() for h in allowed_hosts}

    async def dispatch(self, request: Request, call_next: Any) -> Response:
        if not self.allowed_hosts or request.url.path in _EXEMPT_PATHS:
            return cast(Response, await call_next(request))

        host_header = request.headers.get("host", "")
        hostname = host_header.split(":", 1)[0].strip().lower()

        if not hostname or hostname not in self.allowed_hosts:
            return JSONResponse({"detail": "Invalid host"}, status_code=400)

        return cast(Response, await call_next(request))
