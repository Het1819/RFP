from app.core.database import get_db
from app.main import app


def test_healthz(client):
    """Proves that /healthz returns 200 OK."""
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_readyz_success(client):
    """Proves that /readyz returns 200 OK when database is reachable."""
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
