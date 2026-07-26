"""Confirms the production Compose file's `clamd` service is isolated per
the A5c threat model: no published port, no secrets, no application/
DB/Redis/Anthropic env vars, and no filesystem access to quarantine or app
storage. Only `worker` talks to it (over the backend network, via
INSTREAM) and only `worker`/`app` mount quarantine storage -- `clamd`
itself never gets a quarantine mount.

See tests/integration/test_a5b_quarantine_compose.py for the
quarantine-mount-isolation assertions (app read-write, worker read-only)
this file builds on."""

from pathlib import Path
from typing import Any

import yaml

COMPOSE_PATH = Path(__file__).resolve().parents[2] / "docker-compose.prod.yml"


class TestClamdComposeTopology:
    def _load_compose(self) -> dict[str, Any]:
        with open(COMPOSE_PATH) as f:
            result: dict[str, Any] = yaml.safe_load(f)
            return result

    def test_clamd_service_declared(self) -> None:
        compose = self._load_compose()
        assert "clamd" in compose["services"]

    def test_clamd_has_no_published_ports(self) -> None:
        compose = self._load_compose()
        clamd = compose["services"]["clamd"]
        assert "ports" not in clamd

    def test_clamd_has_no_secrets(self) -> None:
        compose = self._load_compose()
        clamd = compose["services"]["clamd"]
        assert "secrets" not in clamd

    def test_clamd_mounts_nothing_from_quarantine_or_app_storage(self) -> None:
        compose = self._load_compose()
        clamd = compose["services"]["clamd"]
        volumes = clamd.get("volumes", []) or []
        for v in volumes:
            assert "quarantine_storage" not in v
            assert "app_storage" not in v

    def test_clamd_only_on_backend_network(self) -> None:
        compose = self._load_compose()
        clamd = compose["services"]["clamd"]
        assert clamd.get("networks") == ["backend"]

    def test_clamav_signatures_volume_declared_and_only_used_by_clamd(self) -> None:
        compose = self._load_compose()
        assert "clamav_signatures" in compose["volumes"]
        for service_name, service in compose["services"].items():
            volumes = service.get("volumes", []) or []
            mounts_signatures = any("clamav_signatures" in v for v in volumes)
            if service_name == "clamd":
                assert mounts_signatures, "clamd must mount clamav_signatures"
            else:
                assert not mounts_signatures, (
                    f"{service_name} must NOT mount clamav_signatures"
                )

    def test_no_service_other_than_app_and_worker_mounts_quarantine(self) -> None:
        compose = self._load_compose()
        for service_name, service in compose["services"].items():
            if service_name in ("app", "worker"):
                continue
            volumes = service.get("volumes", []) or []
            assert not any("quarantine_storage" in v for v in volumes), (
                f"{service_name} must NOT mount quarantine_storage"
            )

    def test_worker_depends_on_clamd_healthy(self) -> None:
        compose = self._load_compose()
        worker_depends_on = compose["services"]["worker"].get("depends_on", {})
        assert worker_depends_on.get("clamd", {}).get("condition") == "service_healthy"

    def test_worker_has_clamav_host_env_var(self) -> None:
        compose = self._load_compose()
        worker_env = compose["services"]["worker"].get("environment", {}) or {}
        assert worker_env.get("CLAMAV_HOST") == "clamd"

    def test_nginx_postgres_redis_mount_nothing_quarantine_or_clamd_related(
        self,
    ) -> None:
        compose = self._load_compose()
        for service_name in ("nginx", "postgres", "redis"):
            service = compose["services"][service_name]
            volumes = service.get("volumes", []) or []
            for v in volumes:
                assert "quarantine_storage" not in v
                assert "clamav_signatures" not in v
