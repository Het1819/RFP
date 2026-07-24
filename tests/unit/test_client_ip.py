"""Trusted-proxy-aware client IP resolution."""

import pytest

from app.core.client_ip import parse_trusted_proxy_ips, resolve_client_ip


class _FakeClient:
    def __init__(self, host: str) -> None:
        self.host = host


class _FakeHeaders(dict):
    def get(self, key, default=None):
        return super().get(key.lower(), default)


class _FakeRequest:
    def __init__(self, peer: str | None, headers: dict[str, str] | None = None) -> None:
        self.client = _FakeClient(peer) if peer is not None else None
        self.headers = _FakeHeaders({k.lower(): v for k, v in (headers or {}).items()})


TRUSTED = frozenset({"172.28.0.10"})


def test_parse_trusted_proxy_ips_basic():
    assert parse_trusted_proxy_ips("172.28.0.10") == frozenset({"172.28.0.10"})
    assert parse_trusted_proxy_ips("172.28.0.10, 172.28.0.11") == frozenset(
        {"172.28.0.10", "172.28.0.11"}
    )


def test_parse_trusted_proxy_ips_empty():
    assert parse_trusted_proxy_ips(None) == frozenset()
    assert parse_trusted_proxy_ips("") == frozenset()


@pytest.mark.parametrize("wildcard", ["*", "0.0.0.0/0", "::/0", "0.0.0.0", "::"])
def test_parse_trusted_proxy_ips_rejects_wildcards(wildcard):
    with pytest.raises(ValueError, match="wildcard"):
        parse_trusted_proxy_ips(wildcard)


def test_parse_trusted_proxy_ips_rejects_cidr():
    with pytest.raises(ValueError, match="CIDR"):
        parse_trusted_proxy_ips("172.28.0.0/24")


def test_parse_trusted_proxy_ips_rejects_garbage():
    with pytest.raises(ValueError, match="invalid trusted-proxy IP"):
        parse_trusted_proxy_ips("not-an-ip")


def test_untrusted_peer_ignores_forwarded_header():
    request = _FakeRequest("1.2.3.4", {"X-Forwarded-For": "9.9.9.9"})
    assert resolve_client_ip(request, TRUSTED) == "1.2.3.4"


def test_untrusted_peer_cannot_spoof_even_with_valid_looking_header():
    request = _FakeRequest("203.0.113.5", {"X-Forwarded-For": "172.28.0.10"})
    assert resolve_client_ip(request, TRUSTED) == "203.0.113.5"


def test_trusted_peer_uses_forwarded_header():
    request = _FakeRequest("172.28.0.10", {"X-Forwarded-For": "8.8.8.8"})
    assert resolve_client_ip(request, TRUSTED) == "8.8.8.8"


def test_trusted_peer_falls_back_when_header_absent():
    request = _FakeRequest("172.28.0.10")
    assert resolve_client_ip(request, TRUSTED) == "172.28.0.10"


@pytest.mark.parametrize(
    "malformed",
    [
        "8.8.8.8, 9.9.9.9",  # chain -- rejected in single-proxy topology
        "8.8.8.8:9000",  # port included
        "",  # empty
        "not-an-ip",
        "a" * 100,  # oversized
    ],
)
def test_trusted_peer_rejects_malformed_forwarded_value(malformed):
    request = _FakeRequest("172.28.0.10", {"X-Forwarded-For": malformed})
    # Fails closed to the direct (trusted proxy) peer address, never a
    # guess at the malformed value.
    assert resolve_client_ip(request, TRUSTED) == "172.28.0.10"


def test_trusted_peer_accepts_valid_ipv6():
    request = _FakeRequest("172.28.0.10", {"X-Forwarded-For": "2001:db8::1"})
    assert resolve_client_ip(request, TRUSTED) == "2001:db8::1"


def test_no_trusted_proxies_configured_never_honors_forwarded_header():
    request = _FakeRequest("172.28.0.10", {"X-Forwarded-For": "8.8.8.8"})
    assert resolve_client_ip(request, frozenset()) == "172.28.0.10"


def test_no_client_returns_unknown():
    request = _FakeRequest(None)
    assert resolve_client_ip(request, TRUSTED) == "unknown"


def test_two_distinct_clients_behind_trusted_proxy_resolve_distinctly():
    r1 = _FakeRequest("172.28.0.10", {"X-Forwarded-For": "8.8.8.8"})
    r2 = _FakeRequest("172.28.0.10", {"X-Forwarded-For": "9.9.9.9"})
    assert resolve_client_ip(r1, TRUSTED) != resolve_client_ip(r2, TRUSTED)
