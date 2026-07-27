"""Readiness checks for dependencies that `/readyz` must verify.

Kept separate from `app/main.py` so individual checks (e.g. quarantine
storage) can be unit tested without spinning up the FastAPI app or its
other dependencies (database, Redis session store).

These checks are for the readiness probe ONLY -- liveness (`/healthz`)
must never depend on any of them, since liveness answers "is the process
running" while readiness answers "can this instance safely serve
traffic right now".
"""

import os
import uuid
from dataclasses import dataclass
from pathlib import Path

from app.core.config import settings
from app.services import clamav_client


@dataclass(frozen=True)
class ReadinessCheckResult:
    healthy: bool
    detail: str


def check_quarantine_storage() -> ReadinessCheckResult:
    """Confirm quarantine storage is a real, non-symlink directory that is
    actually writable, by creating and deleting a small generated-name
    probe file. Never opens, reads, or scans any uploaded document -- the
    probe file is created fresh with a random name and immediately
    removed. The detail message never includes the configured host path,
    so a failure never leaks filesystem layout into logs or API
    responses.
    """
    root = Path(settings.QUARANTINE_STORAGE_PATH)

    if not root.exists():
        return ReadinessCheckResult(False, "quarantine storage unavailable")
    if root.is_symlink():
        return ReadinessCheckResult(False, "quarantine storage unavailable")
    if not root.is_dir():
        return ReadinessCheckResult(False, "quarantine storage unavailable")

    probe_name = f".readyz-probe-{uuid.uuid4().hex}"
    probe_path = root / probe_name
    try:
        fd = os.open(probe_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.close(fd)
        probe_path.unlink()
    except OSError:
        return ReadinessCheckResult(False, "quarantine storage unavailable")

    return ReadinessCheckResult(True, "quarantine storage ready")


def check_clamav_connectivity() -> ReadinessCheckResult:
    """Confirm the configured `clamd` daemon is reachable via a PING/PONG
    handshake, bounded by `CLAMAV_CONNECT_TIMEOUT_SECONDS`. Never scans a
    file -- delegates entirely to `clamav_client.check_connectivity()`
    (Task 2's protocol client), which owns the socket lifecycle. The
    detail message never includes the configured host/port or any real
    error detail, matching `check_quarantine_storage`'s no-internal-detail
    convention, so a failure never leaks deployment details into logs or
    API responses.
    """
    if clamav_client.check_connectivity():
        return ReadinessCheckResult(True, "scanner ready")
    return ReadinessCheckResult(False, "scanner unavailable")
