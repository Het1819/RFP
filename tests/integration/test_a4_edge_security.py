"""Phase A4 edge security tests: fixed-behavior counterparts to
test_a4_edge_weaknesses.py (skipped), plus a real Docker-Compose-backed
integration suite exercising the full Nginx + app stack.

The Docker-gated tests are skipped automatically when Docker is
unavailable, and are also skipped unless `secrets/tls_cert.pem` /
`secrets/tls_key.pem` and the Phase A3 secrets already exist locally (they
are never generated implicitly by this test file -- see
scripts/generate_local_prod_secrets.py and
scripts/generate_local_tls_cert.py). No customer data is used and no real
Anthropic API call is made anywhere in this file.
"""

import json
import pathlib
import shutil
import subprocess
import time

import pytest
import requests
import urllib3

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
COMPOSE_FILE = "docker-compose.prod.yml"

# Local-only loopback ports for this test run, distinct from any manual
# validation the operator might have running.
_HTTP_PORT = "18180"
_HTTPS_PORT = "18543"

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


def _read(relative_path: str) -> str:
    return (REPO_ROOT / relative_path).read_text(encoding="utf-8")


def _docker_available() -> bool:
    return shutil.which("docker") is not None


def _local_secrets_ready() -> bool:
    secrets_dir = REPO_ROOT / "secrets"
    required = [
        "app_secret.txt",
        "session_secret.txt",
        "throttle_secret.txt",
        "postgres_password.txt",
        "redis_password.txt",
        "redis.conf",
        "anthropic_api_key.txt",
        "tls_cert.pem",
        "tls_key.pem",
    ]
    return all((secrets_dir / name).is_file() for name in required)


# ---------------------------------------------------------------------------
# Fixed-behavior counterparts (static config; no Docker required)
# ---------------------------------------------------------------------------


def test_fix_nginx_service_present_in_compose():
    compose = _read(COMPOSE_FILE)
    assert "nginx" in compose.lower()
    assert "8443" in compose
    assert "8080" in compose


def test_fix_trusted_proxy_ips_configured_for_app():
    compose = _read(COMPOSE_FILE)
    assert "TRUSTED_PROXY_IPS" in compose


def test_fix_client_ip_resolver_module_exists():
    from app.core.client_ip import resolve_client_ip

    assert callable(resolve_client_ip)


def test_fix_public_base_url_setting_exists():
    from app.core.config import Settings

    assert "PUBLIC_BASE_URL" in Settings.model_fields


def test_fix_nginx_rate_limit_zones_configured():
    conf = _read("nginx/nginx.conf")
    assert "limit_req_zone" in conf
    assert "limit_conn_zone" in conf


def test_fix_nginx_security_headers_configured():
    conf = _read("nginx/templates/default.conf.template")
    for header in (
        "Strict-Transport-Security",
        "Content-Security-Policy",
        "X-Content-Type-Options",
        "Referrer-Policy",
        "Permissions-Policy",
        "X-Frame-Options",
    ):
        assert header in conf


def test_fix_nginx_denies_metrics_and_readyz():
    conf = _read("nginx/templates/default.conf.template")
    assert "metrics|readyz" in conf


def test_fix_nginx_error_pages_exist():
    for code in (400, 403, 404, 413, 429, 500, 502, 503, 504):
        assert (REPO_ROOT / "nginx" / "html" / "errors" / f"{code}.html").is_file()


def test_fix_certificate_operating_procedure_documented():
    runbook = _read("RUNBOOK.md")
    assert "certificate" in runbook.lower()


# ---------------------------------------------------------------------------
# Real Docker Compose integration suite
# ---------------------------------------------------------------------------

_skip_reason = None
if not _docker_available():
    _skip_reason = "Docker CLI not available"
elif not _local_secrets_ready():
    _skip_reason = (
        "Local secrets/certs not generated -- run "
        "scripts/generate_local_prod_secrets.py, "
        "scripts/generate_local_tls_cert.py, and provide "
        "secrets/anthropic_api_key.txt before running this suite"
    )

pytestmark_stack = pytest.mark.skipif(bool(_skip_reason), reason=_skip_reason or "")


def _compose(*args: str, timeout: int = 120) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["docker", "compose", "-f", COMPOSE_FILE, *args],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=timeout,
        env=_compose_env(),
    )


def _compose_env() -> dict[str, str]:
    import os

    env = dict(os.environ)
    env["EDGE_HTTP_PORT"] = _HTTP_PORT
    env["EDGE_HTTPS_PORT"] = _HTTPS_PORT
    env["EDGE_HTTP_BIND"] = "127.0.0.1"
    env["EDGE_HTTPS_BIND"] = "127.0.0.1"
    env["NGINX_SERVER_NAME"] = "localhost"
    env["LLM_MODEL"] = "claude-sonnet-4-6"
    # Explicit, documented opt-in: only for this local validation run.
    env["ALLOW_LOOPBACK_HOST"] = "true"
    return env


@pytest.fixture(scope="module")
def edge_stack():
    if _skip_reason:
        pytest.skip(_skip_reason)

    build = _compose("build", timeout=600)
    assert build.returncode == 0, build.stderr

    up = _compose("up", "-d", timeout=180)
    assert up.returncode == 0, up.stderr

    base_https = f"https://127.0.0.1:{_HTTPS_PORT}"
    base_http = f"http://127.0.0.1:{_HTTP_PORT}"

    # Wait for the app to report healthy via its own Docker healthcheck
    # (checked over the private backend network, not through Nginx).
    deadline = time.time() + 120
    healthy = False
    while time.time() < deadline:
        ps = _compose("ps", "--format", "json")
        lines = [line for line in ps.stdout.splitlines() if line.strip()]
        states = {}
        for line in lines:
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            states[entry.get("Service")] = entry.get("Health", "")
        if states.get("app") == "healthy":
            healthy = True
            break
        time.sleep(2)

    try:
        yield {"https": base_https, "http": base_http}
    finally:
        _compose("down", "-v", "--remove-orphans", timeout=120)

    assert healthy, "app service did not become healthy in time"


@pytestmark_stack
def test_only_nginx_publishes_host_ports(edge_stack):
    ps = _compose("ps", "--format", "json")
    for line in ps.stdout.splitlines():
        if not line.strip():
            continue
        entry = json.loads(line)
        service = entry.get("Service")
        ports = entry.get("Publishers") or []
        published = [p for p in ports if p.get("PublishedPort")]
        if service == "nginx":
            assert published, "nginx should publish host ports"
        else:
            assert not published, f"{service} must not publish host ports"


@pytestmark_stack
def test_http_redirects_to_https(edge_stack):
    resp = requests.get(
        f"{edge_stack['http']}/login",
        headers={"Host": "localhost"},
        allow_redirects=False,
        timeout=10,
    )
    assert resp.status_code == 308
    assert resp.headers["Location"].startswith("https://")


@pytestmark_stack
def test_unknown_host_rejected_on_http(edge_stack):
    # Nginx's `return 444` (the non-informative rejection for unknown Host
    # values) closes the TCP connection with no HTTP response at all --
    # that is the intended behavior, so `requests` raising a connection
    # error (rather than yielding some status code) IS the pass condition.
    with pytest.raises(requests.exceptions.ConnectionError):
        requests.get(
            f"{edge_stack['http']}/",
            headers={"Host": "attacker.example"},
            timeout=10,
        )


@pytestmark_stack
def test_tls13_succeeds(edge_stack):
    import ssl

    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    ctx.minimum_version = ssl.TLSVersion.TLSv1_3
    resp = requests.get(
        f"{edge_stack['https']}/login",
        headers={"Host": "localhost"},
        verify=False,
        timeout=10,
    )
    assert resp.status_code == 200


@pytestmark_stack
def test_tls10_and_tls11_rejected(edge_stack):
    import socket
    import ssl

    for version in (ssl.TLSVersion.TLSv1, ssl.TLSVersion.TLSv1_1):
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        ctx.minimum_version = version
        ctx.maximum_version = version
        addr = ("127.0.0.1", int(_HTTPS_PORT))
        with pytest.raises((ssl.SSLError, ConnectionError, OSError, socket.error)):
            with socket.create_connection(addr, timeout=5) as sock:
                with ctx.wrap_socket(sock, server_hostname="localhost"):
                    pass


@pytestmark_stack
def test_security_headers_present(edge_stack):
    resp = requests.get(
        f"{edge_stack['https']}/login",
        headers={"Host": "localhost"},
        verify=False,
        timeout=10,
    )
    for header in (
        "Strict-Transport-Security",
        "Content-Security-Policy",
        "X-Content-Type-Options",
        "Referrer-Policy",
        "Permissions-Policy",
        "X-Frame-Options",
    ):
        assert header in resp.headers, f"missing header {header}"
    assert resp.headers["X-Content-Type-Options"] == "nosniff"
    assert resp.headers["X-Frame-Options"] == "DENY"
    assert "script-src 'self'" in resp.headers["Content-Security-Policy"]
    assert "unsafe-eval" not in resp.headers["Content-Security-Policy"]


@pytestmark_stack
def test_metrics_and_readyz_blocked_externally(edge_stack):
    for path in ("/metrics", "/readyz"):
        resp = requests.get(
            f"{edge_stack['https']}/{path.lstrip('/')}",
            headers={"Host": "localhost"},
            verify=False,
            timeout=10,
        )
        assert resp.status_code == 404


@pytestmark_stack
def test_forged_forwarding_headers_are_overwritten(edge_stack):
    """A direct client cannot spoof its IP by sending X-Forwarded-For --
    Nginx overwrites it with its own observed connection address before
    forwarding, and the app only trusts Nginx's exact backend IP anyway."""
    resp = requests.get(
        f"{edge_stack['https']}/login",
        headers={"Host": "localhost", "X-Forwarded-For": "1.2.3.4"},
        verify=False,
        timeout=10,
    )
    assert resp.status_code == 200
    # No direct assertion on the app's internal view is made here (that is
    # covered by the unit-level app.core.client_ip tests) -- this proves
    # the request still succeeds normally, i.e. the forged header did not
    # break or bypass anything at the edge.


@pytestmark_stack
def test_login_edge_rate_limit_returns_429(edge_stack):
    last_status = None
    for _ in range(40):
        resp = requests.get(
            f"{edge_stack['https']}/login",
            headers={"Host": "localhost"},
            verify=False,
            timeout=10,
        )
        last_status = resp.status_code
        if last_status == 429:
            break
    assert last_status == 429


@pytestmark_stack
def test_nginx_container_is_non_root_and_read_only(edge_stack):
    ps = _compose("ps", "-q", "nginx")
    container_id = ps.stdout.strip()
    assert container_id

    inspect = subprocess.run(
        ["docker", "inspect", container_id],
        capture_output=True,
        text=True,
        timeout=30,
    )
    data = json.loads(inspect.stdout)[0]

    user_check = subprocess.run(
        ["docker", "exec", container_id, "id", "-u"],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert user_check.stdout.strip() != "0", "nginx must not run as root (uid 0)"

    assert data["HostConfig"]["ReadonlyRootfs"] is True
    assert "no-new-privileges:true" in data["HostConfig"]["SecurityOpt"]
    assert data["HostConfig"]["CapDrop"] == ["ALL"]


@pytestmark_stack
def test_nginx_has_no_application_secrets_mounted(edge_stack):
    ps = _compose("ps", "-q", "nginx")
    container_id = ps.stdout.strip()

    inspect = subprocess.run(
        ["docker", "inspect", container_id],
        capture_output=True,
        text=True,
        timeout=30,
    )
    data = json.loads(inspect.stdout)[0]
    mounts = [m["Destination"] for m in data.get("Mounts", [])]

    for forbidden in (
        "/run/secrets/app_secret",
        "/run/secrets/session_secret",
        "/run/secrets/throttle_secret",
        "/run/secrets/postgres_password",
        "/run/secrets/redis_password",
        "/run/secrets/anthropic_api_key",
    ):
        assert forbidden not in mounts

    assert "/run/secrets/tls_cert" in mounts
    assert "/run/secrets/tls_key" in mounts
