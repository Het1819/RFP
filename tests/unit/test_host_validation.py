"""Host-header allowlist middleware."""

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.host_validation import HostValidationMiddleware


def _build_app(allowed_hosts: list[str]) -> FastAPI:
    app = FastAPI()

    @app.get("/ping")
    def ping() -> dict[str, str]:
        return {"status": "ok"}

    app.add_middleware(HostValidationMiddleware, allowed_hosts=allowed_hosts)
    return app


def test_matching_host_allowed():
    client = TestClient(_build_app(["rfp.example.com"]))
    resp = client.get("/ping", headers={"Host": "rfp.example.com"})
    assert resp.status_code == 200


def test_unknown_host_rejected():
    client = TestClient(_build_app(["rfp.example.com"]))
    resp = client.get("/ping", headers={"Host": "evil.example.com"})
    assert resp.status_code == 400


def test_host_with_port_matches_hostname_only():
    client = TestClient(_build_app(["rfp.example.com"]))
    resp = client.get("/ping", headers={"Host": "rfp.example.com:8443"})
    assert resp.status_code == 200


def test_no_allowlist_configured_is_a_no_op():
    client = TestClient(_build_app([]))
    resp = client.get("/ping", headers={"Host": "anything.example"})
    assert resp.status_code == 200


def test_readyz_exempt_even_with_wrong_host():
    # Docker's own healthcheck hits /readyz over loopback with whatever
    # Host curl defaults to (e.g. "127.0.0.1:8000"), never the public
    # hostname -- it must never be blocked by this middleware.
    app = _build_app(["rfp.example.com"])

    @app.get("/readyz")
    def readyz() -> dict[str, str]:
        return {"status": "ready"}

    client = TestClient(app)
    resp = client.get("/readyz", headers={"Host": "127.0.0.1:8000"})
    assert resp.status_code == 200


def test_missing_host_header_rejected_when_allowlist_configured():
    client = TestClient(_build_app(["rfp.example.com"]))
    # httpx/TestClient always sends a Host header, so simulate an empty one.
    resp = client.get("/ping", headers={"Host": ""})
    assert resp.status_code == 400


def test_host_check_runs_before_route_handler():
    calls: list[str] = []
    app = FastAPI()

    @app.get("/side-effect")
    def handler() -> dict[str, str]:
        calls.append("handler-ran")
        return {"status": "ok"}

    app.add_middleware(HostValidationMiddleware, allowed_hosts=["rfp.example.com"])
    client = TestClient(app)
    resp = client.get("/side-effect", headers={"Host": "attacker.example"})
    assert resp.status_code == 400
    assert calls == []
