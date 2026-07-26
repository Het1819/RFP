"""Confirms the production Compose file gives quarantine storage its own
dedicated volume, mounted ONLY into `app` -- not `worker`, `nginx`,
`postgres`, or `redis`. Worker has no security-processing stage
implemented yet in A5b, so it must never gain filesystem access to
quarantined (unscanned, untrusted) uploads."""

from pathlib import Path

import yaml

COMPOSE_PATH = Path(__file__).resolve().parents[2] / "docker-compose.prod.yml"


class TestQuarantineComposeMountIsolation:
    def _load_compose(self) -> dict:
        with open(COMPOSE_PATH) as f:
            return yaml.safe_load(f)

    def test_quarantine_volume_declared(self) -> None:
        compose = self._load_compose()
        assert "quarantine_storage" in compose["volumes"]

    def test_only_app_mounts_quarantine(self) -> None:
        compose = self._load_compose()
        for service_name, service in compose["services"].items():
            volumes = service.get("volumes", []) or []
            mounts_quarantine = any("quarantine_storage" in v for v in volumes)
            if service_name == "app":
                assert mounts_quarantine, "app must mount quarantine_storage"
            else:
                assert not mounts_quarantine, (
                    f"{service_name} must NOT mount quarantine_storage"
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
