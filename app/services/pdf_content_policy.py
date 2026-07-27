"""Parent-process orchestrator for the isolated PDF content-policy
inspector.

This module never imports `pypdf` itself -- it only spawns
`app.services.pdf_inspector_subprocess` as a separate OS process (via
`sys.executable`, never a shell) and parses its single-line JSON result.
Any anomaly -- a wall-clock timeout, a non-zero exit code, or malformed
stdout -- is treated identically to an explicit `FAILED` result: this
function always fails closed, never approximates a PASS.

`result.stdout`/`result.stderr` from the subprocess are never logged
verbatim, even on failure -- they could theoretically contain bytes
derived from the untrusted file if the subprocess ever misbehaved. Only
the parsed, fixed reason code is logged.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from app.core.config import settings

logger = logging.getLogger(__name__)

# Bump whenever the set of checks in pdf_inspector_subprocess.py changes,
# so callers/audit logs can tell which policy version produced a result.
POLICY_VERSION = "1"

# Repo root (three levels up from this file: app/services/<this file>).
# Passed as the subprocess's cwd below so `-m app.services.
# pdf_inspector_subprocess` resolves correctly regardless of this
# process's own current working directory -- currently correct by
# relying on cwd implicitly (no PYTHONPATH is passed to the minimal
# subprocess environment), but fragile if the parent process's cwd ever
# changes. An explicit cwd= removes that assumption entirely.
_REPO_ROOT = Path(__file__).resolve().parents[2]

_FAILED_REASON = "PDF_INSPECTION_FAILED"


@dataclass(frozen=True)
class PdfPolicyResult:
    passed: bool
    # PDF_ENCRYPTED, PDF_ACTIVE_CONTENT, PDF_EMBEDDED_FILE,
    # PDF_INSPECTION_FAILED
    reason_code: str | None
    policy_version: str


def _failed(reason_code: str = _FAILED_REASON) -> PdfPolicyResult:
    return PdfPolicyResult(
        passed=False, reason_code=reason_code, policy_version=POLICY_VERSION
    )


def _parse_subprocess_stdout(stdout: str) -> PdfPolicyResult | None:
    """Parse exactly one JSON line from `stdout`. Returns None (caller
    fails closed) if there is not exactly one line, if it isn't valid
    JSON, or if the JSON doesn't have the expected shape."""
    lines = [line for line in stdout.splitlines() if line.strip()]
    if len(lines) != 1:
        return None
    try:
        payload = json.loads(lines[0])
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None

    status = payload.get("status")
    reason_code = payload.get("reason_code")
    if reason_code is not None and not isinstance(reason_code, str):
        return None

    if status == "CLEAN":
        if reason_code is not None:
            return None
        return PdfPolicyResult(
            passed=True, reason_code=None, policy_version=POLICY_VERSION
        )
    if status == "REJECTED":
        if not isinstance(reason_code, str) or not reason_code:
            return None
        return _failed(reason_code)
    if status == "FAILED":
        return _failed(reason_code if isinstance(reason_code, str) else _FAILED_REASON)
    return None


def _minimal_subprocess_env() -> dict[str, str]:
    """Build the smallest environment that lets `sys.executable` actually
    start and run the inspector module. Deliberately does NOT inherit the
    parent's environment (`os.environ`) -- this app's config reads
    secrets (DB/session/API keys, Redis URLs) from environment variables,
    and the whole point of this subprocess is to run untrusted-file
    parsing somewhere those secrets are unreachable even if `pypdf` were
    ever exploited by a malicious PDF."""
    env: dict[str, str] = {
        "PDF_INSPECTOR_CPU_SECONDS": str(settings.PDF_INSPECTOR_CPU_SECONDS),
        "PDF_INSPECTOR_MEMORY_BYTES": str(settings.PDF_INSPECTOR_MEMORY_BYTES),
    }
    path = os.environ.get("PATH")
    if path:
        env["PATH"] = path
    if sys.platform == "win32":
        # Required for the Windows CRT/loader to start python.exe and
        # resolve system DLLs at all -- not needed for anything this
        # subprocess actually does with the untrusted file.
        system_root = os.environ.get("SystemRoot")
        if system_root:
            env["SystemRoot"] = system_root
    return env


def check_pdf_content_policy(file_path: Path) -> PdfPolicyResult:
    """Spawns the isolated inspector subprocess with a hard wall-clock
    timeout; a timeout, non-zero exit, or malformed JSON output is
    treated identically to an explicit FAILED result -- fail closed,
    never approximate a PASS."""
    argv = [
        sys.executable,
        "-m",
        "app.services.pdf_inspector_subprocess",
        str(file_path),
    ]
    try:
        result = subprocess.run(
            argv,
            capture_output=True,
            timeout=settings.PDF_INSPECTION_TIMEOUT_SECONDS,
            text=True,
            errors="replace",
            env=_minimal_subprocess_env(),
            cwd=_REPO_ROOT,
        )
    except subprocess.TimeoutExpired:
        # subprocess.run's `timeout` kills the child (and any pipe
        # deadlock) before raising this -- see module docstring; a test
        # exercises this to confirm no process is left behind.
        logger.warning(
            "PDF inspector subprocess timed out after %s seconds",
            settings.PDF_INSPECTION_TIMEOUT_SECONDS,
        )
        return _failed()
    except OSError:
        logger.warning("PDF inspector subprocess failed to start")
        return _failed()

    if result.returncode != 0:
        logger.warning("PDF inspector subprocess exited nonzero: %s", result.returncode)
        return _failed()

    parsed = _parse_subprocess_stdout(result.stdout)
    if parsed is None:
        logger.warning("PDF inspector subprocess produced malformed output")
        return _failed()

    if not parsed.passed:
        logger.info("PDF content policy rejected: %s", parsed.reason_code)

    return parsed
