"""Confirms the production Compose file gives quarantine storage its own
dedicated volume, mounted only into `app` (read-write) and `worker`
(read-only) -- never into `nginx`, `postgres`, or `redis`. As of A5c,
`worker` runs the malware/content-policy scanning stage and needs to read
quarantined files, but must never be able to write into quarantine
storage. See tests/integration/test_a5c_compose_topology.py for the
clamd-specific topology assertions added in A5c."""

from pathlib import Path
from typing import Any

import yaml

COMPOSE_PATH = Path(__file__).resolve().parents[2] / "docker-compose.prod.yml"


class TestQuarantineComposeMountIsolation:
    def _load_compose(self) -> dict[str, Any]:
        with open(COMPOSE_PATH) as f:
            result: dict[str, Any] = yaml.safe_load(f)
            return result

    def test_quarantine_volume_declared(self) -> None:
        compose = self._load_compose()
        assert "quarantine_storage" in compose["volumes"]

    def test_only_app_and_worker_mount_quarantine(self) -> None:
        compose = self._load_compose()
        for service_name, service in compose["services"].items():
            volumes = service.get("volumes", []) or []
            mounts_quarantine = any("quarantine_storage" in v for v in volumes)
            if service_name in ("app", "worker"):
                assert mounts_quarantine, (
                    f"{service_name} must mount quarantine_storage"
                )
            else:
                assert not mounts_quarantine, (
                    f"{service_name} must NOT mount quarantine_storage"
                )

    def test_worker_quarantine_mount_is_read_only(self) -> None:
        compose = self._load_compose()
        worker_volumes = compose["services"]["worker"].get("volumes", []) or []
        quarantine_mount = next(
            (v for v in worker_volumes if v.startswith("quarantine_storage:")), None
        )
        assert quarantine_mount is not None
        assert quarantine_mount.endswith(":ro"), (
            f"worker's quarantine mount must be read-only, got: {quarantine_mount}"
        )

    def test_app_quarantine_env_var_matches_mount_target(self) -> None:
        compose = self._load_compose()
        app_service = compose["services"]["app"]
        volumes = app_service.get("volumes", []) or []
        quarantine_mount = next(
            (v for v in volumes if v.startswith("quarantine_storage:")), None
        )
        assert quarantine_mount is not None
        mount_target = quarantine_mount.split(":", 1)[1]

        env = app_service.get("environment", {}) or {}
        assert env.get("QUARANTINE_STORAGE_PATH") == mount_target
