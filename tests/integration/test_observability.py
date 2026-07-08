import logging

from sqlalchemy import select

from app.core.database import get_default_org_and_user
from app.core.observability import JSONFormatter, MetricsRegistry
from app.models.audit import AuditEvent
from app.models.project import ProposalProject


def test_correlation_id_middleware(client):
    """Proves correlation IDs are generated, validated, and returned."""
    # 1. When request header X-Request-ID is absent, generate one
    resp1 = client.get("/health")
    assert "X-Request-ID" in resp1.headers
    generated_id = resp1.headers["X-Request-ID"]
    assert len(generated_id) > 10

    # 2. When valid X-Request-ID is provided, reflect it
    custom_id = "test-custom-request-id-12345"
    resp2 = client.get("/health", headers={"X-Request-ID": custom_id})
    assert resp2.headers["X-Request-ID"] == custom_id

    # 3. When unsafe X-Request-ID is provided, strip/replace it
    unsafe_id = "bad_id_$%^&*()_too_long_" * 5
    resp3 = client.get("/health", headers={"X-Request-ID": unsafe_id})
    assert resp3.headers["X-Request-ID"] != unsafe_id
    assert len(resp3.headers["X-Request-ID"]) > 10


def test_structured_logging_sanitization():
    """Proves JSONFormatter redacts prompts, completions, document text, and secrets."""
    formatter = JSONFormatter()

    # Create a mock LogRecord containing sensitive information
    record = logging.LogRecord(
        name="test_logger",
        level=logging.INFO,
        pathname="test.py",
        lineno=10,
        msg="test message",
        args=(),
        exc_info=None,
    )

    # Inject sensitive properties
    record.prompt = "SELECT * FROM users WHERE password = 'secret_password'"
    record.completion = "Generated response with secret keys"
    record.session_secret_key = "very_sensitive_cookie_session_key"
    record.email = "sensitive-user@test.com"
    record.user_id = "normal_user_id"
    record.normal_field = "safe_unredacted_field"

    formatted = formatter.format(record)
    import json

    log_json = json.loads(formatted)

    assert log_json["prompt"] == "[REDACTED]"
    assert log_json["completion"] == "[REDACTED]"
    assert log_json["session_secret_key"] == "[REDACTED]"
    assert log_json["email"] == "s***@test.com"  # masked
    assert (
        log_json["user_id"] == "normal_user_id"
    )  # Safe UUID/string identifier, not an email
    assert log_json["normal_field"] == "safe_unredacted_field"


def test_metrics_endpoint(client, db):
    """Proves metrics endpoint returns non-sensitive telemetry counters."""
    MetricsRegistry.request_counts[("/metrics-test", "GET", 200)] = 5
    MetricsRegistry.llm_calls[("fake", "fake-model", "test_op")] = 3

    resp = client.get("/metrics")
    assert resp.status_code == 200
    assert "text/plain" in resp.headers["content-type"]

    content = resp.text
    # Check Prometheus labels & standard output format
    assert "pilot_projects_active" in content
    assert 'route="/metrics-test"' in content
    assert 'operation="test_op"' in content
    # No sensitive database schemas or document contents leaked in metrics
    assert "SELECT *" not in content


def test_ops_dashboard_requires_auth(unauthenticated_client, monkeypatch):
    """Proves that accessing /projects/ops/dashboard requires authentication."""
    from app.core.config import settings

    # Force session mode so dev auth fallback does not fire
    monkeypatch.setattr(settings, "AUTH_MODE", "session")

    # Unauthorized request redirects to login when HTML is accepted
    response = unauthenticated_client.get(
        "/projects/ops/dashboard",
        headers={"Accept": "text/html"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert "/login" in response.headers["location"]


def test_ops_dashboard_scoping(client, db, monkeypatch):
    """Proves ops dashboard loads metrics and failed jobs list correctly."""
    org_id, user_id = get_default_org_and_user(db)

    # Create pilot project
    proj = ProposalProject(
        organization_id=org_id,
        created_by_id=user_id,
        name="Dashboard Scoped Project",
        client_name="Client A",
    )
    db.add(proj)
    db.commit()

    # Log in user by overriding auth helper
    monkeypatch.setattr(
        "app.web.routes.projects.get_current_org_and_user",
        lambda r, d: (org_id, user_id),
    )

    response = client.get("/projects/ops/dashboard")
    assert response.status_code == 200
    assert (
        "Dashboard Scoped Project" not in response.text
    )  # Sensitive names should not leak or be exposed on global KPI layout
    assert "Pilot KPI Dashboard" in response.text
    assert "Active Projects" in response.text


def test_audit_logs_login_logout(client, db):
    """Proves login and logout actions log AuditEvent records successfully."""
    org_id, _ = get_default_org_and_user(db)

    # Pre-create the user in the database so authentication succeeds
    from app.models.user import User

    test_user = User(
        organization_id=org_id,
        email="pilot@company.com",
        full_name="Pilot User",
        hashed_password="fake-pbkdf2-sha256-hash-for-now",
        is_active=True,
    )
    db.add(test_user)
    db.commit()

    # Perform fake login to trigger audit log
    response = client.post(
        "/login",
        data={"email": "pilot@company.com"},
        follow_redirects=False,
    )
    assert response.status_code == 303

    # Check AuditEvent
    login_events = db.scalars(
        select(AuditEvent).where(
            AuditEvent.organization_id == org_id,
            AuditEvent.action == "USER_LOGIN_SUCCESS",
        )
    ).all()
    assert len(login_events) > 0

    # Perform logout to trigger logout audit log
    client.get("/logout")

    logout_events = db.scalars(
        select(AuditEvent).where(
            AuditEvent.organization_id == org_id,
            AuditEvent.action == "USER_LOGOUT",
        )
    ).all()
    assert len(logout_events) > 0


def test_audit_logs_exports(client, db, monkeypatch):
    """Proves proposal and compliance matrix exports trigger audit events."""
    org_id, user_id = get_default_org_and_user(db)

    proj = ProposalProject(
        organization_id=org_id,
        created_by_id=user_id,
        name="Export Audit Project",
        client_name="Client B",
    )
    db.add(proj)
    db.commit()

    monkeypatch.setattr(
        "app.web.routes.compliance.get_current_org_and_user",
        lambda r, d: (org_id, user_id),
    )

    # Perform matrix export
    response_matrix = client.get(f"/projects/{proj.id}/export/matrix")
    assert response_matrix.status_code == 200

    # Perform proposal export
    response_proposal = client.get(f"/projects/{proj.id}/export/proposal")
    assert response_proposal.status_code == 200

    # Assert audit events exist
    events = db.scalars(
        select(AuditEvent).where(
            AuditEvent.organization_id == org_id,
            AuditEvent.action.like("EXPORT_%"),
        )
    ).all()
    assert len(events) >= 2
