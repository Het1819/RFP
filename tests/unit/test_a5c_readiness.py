from app.core.readiness import ReadinessCheckResult, check_clamav_connectivity


class TestClamavConnectivityReadinessCheck:
    def test_healthy_clamd_reports_healthy(self, monkeypatch) -> None:
        monkeypatch.setattr(
            "app.services.clamav_client.check_connectivity", lambda: True
        )

        result = check_clamav_connectivity()

        assert result.healthy is True

    def test_down_clamd_reports_unhealthy_with_fixed_detail(self, monkeypatch) -> None:
        monkeypatch.setattr(
            "app.services.clamav_client.check_connectivity", lambda: False
        )

        result = check_clamav_connectivity()

        assert result.healthy is False
        assert result.detail == "scanner unavailable"


class TestReadyzScannerCheck:
    def test_readyz_reports_ready_when_scanner_healthy(self, client, monkeypatch):
        """Proves /readyz returns 200 when the scanner check reports
        healthy (in addition to database/session store/quarantine
        storage already being healthy in the test environment)."""

        def fake_check_clamav_connectivity() -> ReadinessCheckResult:
            return ReadinessCheckResult(True, "scanner ready")

        monkeypatch.setattr(
            "app.main.check_clamav_connectivity", fake_check_clamav_connectivity
        )

        response = client.get("/readyz")
        assert response.status_code == 200
        assert response.json() == {"status": "ready"}

    def test_readyz_scanner_down_fails_readiness_but_login_still_serves(
        self, client, monkeypatch
    ):
        """Proves that when the scanner is down, /readyz returns a
        non-200 response distinct from quarantine-storage's failure
        detail, while an unrelated plain route (GET /login) still
        returns 200 in the same test run -- readiness of the scanner
        must never gate unrelated app routes."""

        def fake_check_clamav_connectivity() -> ReadinessCheckResult:
            return ReadinessCheckResult(False, "scanner unavailable")

        monkeypatch.setattr(
            "app.main.check_clamav_connectivity", fake_check_clamav_connectivity
        )

        response = client.get("/readyz")
        assert response.status_code == 503
        assert "Scanner not ready" in response.json()["detail"]

        login_response = client.get("/login")
        assert login_response.status_code == 200

        liveness_response = client.get("/healthz")
        assert liveness_response.status_code == 200
        assert liveness_response.json() == {"status": "ok"}
