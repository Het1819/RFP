from app.core.database import get_db
from app.core.readiness import ReadinessCheckResult
from app.main import app


def test_healthz(client):
    """Proves that /healthz returns 200 OK."""
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_readyz_success(client, monkeypatch):
    """Proves that /readyz returns 200 OK when database is reachable.

    No real clamd is running in the test environment, so the scanner
    connectivity check is stubbed healthy here -- clamd-down behavior is
    covered separately in tests/unit/test_a5c_readiness.py.
    """

    def fake_check_clamav_connectivity() -> ReadinessCheckResult:
        return ReadinessCheckResult(True, "scanner ready")

    monkeypatch.setattr(
        "app.main.check_clamav_connectivity", fake_check_clamav_connectivity
    )

    response = client.get("/readyz")
    assert response.status_code == 200
    assert response.json() == {"status": "ready"}


def test_readyz_db_failure(client):
    """Proves that /readyz returns 503 Service Unavailable when DB fails."""
    from unittest.mock import MagicMock

    mock_session = MagicMock()
    mock_session.execute.side_effect = Exception("Simulated DB Connection Failure")

    app.dependency_overrides[get_db] = lambda: mock_session
    try:
        response = client.get("/readyz")
        assert response.status_code == 503
        assert "Database not ready" in response.json()["detail"]
    finally:
        app.dependency_overrides.clear()


def test_readyz_quarantine_storage_failure(client, monkeypatch):
    """Proves that /readyz returns 503 when quarantine storage is
    unavailable, even though the database and session store are healthy,
    and that liveness (/healthz) stays unaffected."""

    def fake_check_quarantine_storage() -> ReadinessCheckResult:
        return ReadinessCheckResult(False, "quarantine storage unavailable")

    monkeypatch.setattr(
        "app.main.check_quarantine_storage", fake_check_quarantine_storage
    )

    response = client.get("/readyz")
    assert response.status_code == 503
    assert "Quarantine storage not ready" in response.json()["detail"]

    liveness_response = client.get("/healthz")
    assert liveness_response.status_code == 200
    assert liveness_response.json() == {"status": "ok"}
