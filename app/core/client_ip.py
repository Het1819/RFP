"""Centralized, trusted-proxy-aware client IP resolution.

Exactly one reverse proxy (Nginx) is trusted in the A4 topology. All
security-sensitive code -- login throttling, authentication audit events,
security logs, and any future abuse control -- MUST resolve the client IP
through `resolve_client_ip()` rather than reading `request.client.host` or
any `X-Forwarded-*`/`Forwarded` header directly.

Trust model
-----------
1. Read the direct ASGI peer address.
2. If that peer is not an exact configured trusted-proxy IP, forwarded
   headers are ignored entirely and the direct peer address is used --
   an untrusted client cannot spoof its way past this by sending
   `X-Forwarded-For` itself.
3. If the peer IS the trusted proxy, exactly one syntactically valid IP is
   accepted from `X-Forwarded-For`. Anything else (empty, malformed,
   multiple comma-separated hops, a value with a port, an oversized
   header) fails closed to the direct peer address rather than guessing.

Nginx is configured (see nginx/conf.d) to set a single authoritative
`X-Forwarded-For` value from its own observed connection -- it does not
append to whatever an inbound client already sent -- so "exactly one
value" is the correct and only expected shape from the trusted hop.
"""

import ipaddress
from typing import cast

# Longest possible textual IPv6 literal (with zone id headroom); anything
# longer is treated as malformed/oversized rather than parsed.
MAX_FORWARDED_IP_LENGTH = 45


def parse_trusted_proxy_ips(raw: str | None) -> frozenset[str]:
    """Parse a comma-separated list of exact trusted-proxy IPs.

    Rejects wildcards and CIDR ranges outright -- this project's trust
    model is "exactly these IPs," never a subnet or `0.0.0.0/0`/`::/0`.
    """
    if not raw or not raw.strip():
        return frozenset()

    result: set[str] = set()
    for entry in raw.split(","):
        entry = entry.strip()
        if not entry:
            continue
        if entry in ("*", "0.0.0.0/0", "::/0", "0.0.0.0", "::"):
            raise ValueError(f"wildcard trusted-proxy value is not allowed: {entry!r}")
        if "/" in entry:
            raise ValueError(
                f"CIDR ranges are not allowed for TRUSTED_PROXY_IPS: {entry!r}"
            )
        try:
            ipaddress.ip_address(entry)
        except ValueError as exc:
            raise ValueError(f"invalid trusted-proxy IP: {entry!r}") from exc
        result.add(entry)
    return frozenset(result)


def _validate_single_forwarded_ip(value: str) -> str | None:
    """Return the validated IP, or None if the value is not exactly one
    syntactically valid IPv4/IPv6 address (no port, no chain, no junk)."""
    value = value.strip()
    if not value or len(value) > MAX_FORWARDED_IP_LENGTH:
        return None
    if "," in value:
        return None  # a forwarded chain -- not the single-hop shape we trust
    try:
        ipaddress.ip_address(value)
    except ValueError:
        return None
    return value


def resolve_client_ip(request: object, trusted_proxy_ips: frozenset[str]) -> str:
    """Resolve the real client IP for security-sensitive decisions.

    `request` is any object exposing `.client.host` (or None) and
    `.headers.get(name)` -- i.e. a Starlette/FastAPI Request. Typed as
    `object` here to avoid a hard Starlette import dependency in this
    narrowly-scoped module; callers pass a real Request.
    """
    client = getattr(request, "client", None)
    raw_peer = getattr(client, "host", None) if client is not None else None
    if not raw_peer:
        return "unknown"
    peer = cast(str, raw_peer)

    if not trusted_proxy_ips or peer not in trusted_proxy_ips:
        # Untrusted (or unconfigured-trust) peer: never honor forwarded
        # headers, regardless of what the client claims.
        return peer

    headers = getattr(request, "headers", None)
    forwarded = headers.get("x-forwarded-for") if headers is not None else None
    if not forwarded:
        return peer

    validated = _validate_single_forwarded_ip(forwarded)
    return validated if validated is not None else peer
