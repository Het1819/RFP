"""Historical regression evidence: these tests prove the Phase A4 edge
security weaknesses BEFORE the fix. Run against the unmodified A3 tip
(commit c09ce40cc5c067cb98906ebe6695eea062637b2e): all assertions here
passed, confirming each gap. They are skipped once A4 lands -- the fixed
counterparts live in test_a4_edge_security.py.

Static/config checks only; no external target is exercised.
"""

import pathlib

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]

pytestmark = pytest.mark.skip(
    reason=(
        "Phase A4 pre-fix evidence only (recorded: 14/14 passed against A3 "
        "tip c09ce40cc5c067cb98906ebe6695eea062637b2e). Superseded by the "
        "Nginx edge boundary in Phase A4 -- see test_a4_edge_security.py."
    )
)


def _read(relative_path: str) -> str:
    return (REPO_ROOT / relative_path).read_text(encoding="utf-8")


def test_weakness_1_no_tls_termination():
    compose = _read("docker-compose.prod.yml")
    assert "443" not in compose
    assert "ssl_certificate" not in compose


def test_weakness_2_no_https_redirect():
    assert not (REPO_ROOT / "nginx").exists()


def test_weakness_3_no_nginx_reverse_proxy():
    compose = _read("docker-compose.prod.yml")
    assert "nginx" not in compose.lower()


def test_weakness_4_no_trusted_proxy_allowlist():
    from app.core.config import Settings

    assert not hasattr(Settings, "TRUSTED_PROXY_IPS")


def test_weakness_5_no_safe_forwarded_client_ip_resolution():
    with pytest.raises(ImportError):
        import app.core.client_ip  # noqa: F401


def test_weakness_6_app_port_directly_reachable_not_loopback_only_edge():
    compose = _read("docker-compose.prod.yml")
    assert "127.0.0.1:8000:8000" in compose  # app itself still holds the port


def test_weakness_7_no_public_base_url_validation():
    from app.core.config import Settings

    assert not hasattr(Settings, "PUBLIC_BASE_URL")


def test_weakness_8_through_13_no_security_headers_middleware_or_config():
    # No Nginx config directory means none of HSTS, CSP, X-Frame-Options,
    # X-Content-Type-Options, Referrer-Policy, or Permissions-Policy are
    # emitted anywhere in the stack yet.
    assert not (REPO_ROOT / "nginx" / "conf.d").exists()


def test_weakness_14_no_edge_upload_size_enforcement():
    compose = _read("docker-compose.prod.yml")
    assert "client_max_body_size" not in compose


def test_weakness_15_16_no_edge_rate_or_connection_limits():
    compose = _read("docker-compose.prod.yml")
    assert "limit_req" not in compose
    assert "limit_conn" not in compose


def test_weakness_17_metrics_and_readyz_not_restricted_at_any_edge():
    # /metrics and /readyz are plain FastAPI routes with no boundary in
    # front of them in the A3 stack (app itself was the only exposed port).
    import inspect

    import app.main as main_module

    source = inspect.getsource(main_module)
    assert '@app.get("/metrics")' in source
    assert '@app.get("/readyz")' in source
    # No middleware or proxy config anywhere restricts external access.


def test_weakness_18_no_protection_against_direct_port_bypass():
    compose = _read("docker-compose.prod.yml")
    # The app service itself publishes a host port -- nothing sits in
    # front of it to make direct access impossible.
    assert 'ports:\n      - "127.0.0.1:8000:8000"' in compose


def test_weakness_19_no_generic_proxy_error_pages():
    assert not (REPO_ROOT / "nginx").exists()


def test_weakness_20_no_certificate_operating_procedure_documented():
    runbook = _read("RUNBOOK.md")
    assert "certificate" not in runbook.lower()
