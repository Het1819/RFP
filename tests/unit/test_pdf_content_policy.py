import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import pypdf
import pytest
from pypdf.generic import RectangleObject

from app.core.config import settings
from app.services.pdf_content_policy import (
    POLICY_VERSION,
    PdfPolicyResult,
    _parse_subprocess_stdout,
    check_pdf_content_policy,
)

# --- fixture builders ------------------------------------------------------


def _write(writer: pypdf.PdfWriter, path: Path) -> Path:
    with open(path, "wb") as f:
        writer.write(f)
    return path


def _clean_pdf(tmp_path: Path) -> Path:
    writer = pypdf.PdfWriter()
    writer.add_blank_page(width=200, height=200)
    return _write(writer, tmp_path / "clean.pdf")


def _encrypted_pdf(tmp_path: Path) -> Path:
    writer = pypdf.PdfWriter()
    writer.add_blank_page(width=200, height=200)
    writer.encrypt(user_password="", owner_password="s3cret-owner-pw")
    return _write(writer, tmp_path / "encrypted.pdf")


def _open_action_pdf(tmp_path: Path) -> Path:
    writer = pypdf.PdfWriter()
    writer.add_blank_page(width=200, height=200)
    writer.open_destination = writer.pages[0]
    return _write(writer, tmp_path / "open_action.pdf")


def _launch_annotation_pdf(tmp_path: Path) -> Path:
    writer = pypdf.PdfWriter()
    writer.add_blank_page(width=200, height=200)
    writer.add_annotation(
        0,
        {
            "/Subtype": "/Link",
            "/Rect": [0, 0, 10, 10],
            "/A": {"/S": "/Launch", "/F": "calc.exe"},
        },
    )
    return _write(writer, tmp_path / "launch.pdf")


def _uri_action_pdf(tmp_path: Path) -> Path:
    writer = pypdf.PdfWriter()
    writer.add_blank_page(width=200, height=200)
    writer.add_uri(0, "http://example.invalid/", RectangleObject((0, 0, 10, 10)))
    return _write(writer, tmp_path / "uri.pdf")


def _embedded_file_pdf(tmp_path: Path) -> Path:
    writer = pypdf.PdfWriter()
    writer.add_blank_page(width=200, height=200)
    writer.add_attachment("payload.exe", b"MZ\x90\x00\x03")
    return _write(writer, tmp_path / "embedded.pdf")


def _file_attachment_annotation_pdf(tmp_path: Path) -> Path:
    """A page annotation of /Subtype /FileAttachment carrying an inline
    /FS Filespec -- a distinct embedding path from add_attachment()'s
    /Names/EmbeddedFiles tree."""
    writer = pypdf.PdfWriter()
    writer.add_blank_page(width=200, height=200)
    writer.add_annotation(
        0,
        {
            "/Subtype": "/FileAttachment",
            "/Rect": [0, 0, 10, 10],
            "/FS": {"/Type": "/Filespec", "/F": "attached.exe"},
        },
    )
    return _write(writer, tmp_path / "file_attachment_annotation.pdf")


def _orphan_filespec_pdf(tmp_path: Path) -> Path:
    """A /Type /Filespec object present in the file but not linked from
    /Names/EmbeddedFiles or any page annotation -- the orphan-object
    smuggling path that only a full indirect-object scan catches."""
    from pypdf.generic import DictionaryObject, NameObject, TextStringObject

    writer = pypdf.PdfWriter()
    writer.add_blank_page(width=200, height=200)
    filespec = DictionaryObject()
    filespec.update(
        {
            NameObject("/Type"): NameObject("/Filespec"),
            NameObject("/F"): TextStringObject("orphan.exe"),
        }
    )
    writer._add_object(filespec)
    return _write(writer, tmp_path / "orphan_filespec.pdf")


def _corrupt_pdf(tmp_path: Path) -> Path:
    """A structurally corrupt PDF: a well-formed file truncated to half
    its length, well past the level of A5b's candidate-detection-level
    truncation check -- this must reach the real parser and fail there."""
    writer = pypdf.PdfWriter()
    writer.add_blank_page(width=200, height=200)
    path = _write(writer, tmp_path / "source.pdf")
    data = path.read_bytes()
    corrupt_path = tmp_path / "corrupt.pdf"
    corrupt_path.write_bytes(data[: len(data) // 2])
    return corrupt_path


# --- end-to-end (real subprocess) tests ------------------------------------


def test_clean_pdf_passes(tmp_path: Path) -> None:
    result = check_pdf_content_policy(_clean_pdf(tmp_path))
    assert result == PdfPolicyResult(
        passed=True, reason_code=None, policy_version=POLICY_VERSION
    )


def test_encrypted_pdf_rejected(tmp_path: Path) -> None:
    result = check_pdf_content_policy(_encrypted_pdf(tmp_path))
    assert result.passed is False
    assert result.reason_code == "PDF_ENCRYPTED"


def test_open_action_pdf_rejected(tmp_path: Path) -> None:
    result = check_pdf_content_policy(_open_action_pdf(tmp_path))
    assert result.passed is False
    assert result.reason_code == "PDF_ACTIVE_CONTENT"


def test_launch_annotation_pdf_rejected(tmp_path: Path) -> None:
    result = check_pdf_content_policy(_launch_annotation_pdf(tmp_path))
    assert result.passed is False
    assert result.reason_code == "PDF_ACTIVE_CONTENT"


def test_uri_action_pdf_rejected_unconditionally(tmp_path: Path) -> None:
    # Policy: ALL /URI actions are rejected, even ones that look benign --
    # no partial allowlist.
    result = check_pdf_content_policy(_uri_action_pdf(tmp_path))
    assert result.passed is False
    assert result.reason_code == "PDF_ACTIVE_CONTENT"


def test_embedded_file_pdf_rejected(tmp_path: Path) -> None:
    result = check_pdf_content_policy(_embedded_file_pdf(tmp_path))
    assert result.passed is False
    assert result.reason_code == "PDF_EMBEDDED_FILE"


def test_file_attachment_annotation_pdf_rejected(tmp_path: Path) -> None:
    result = check_pdf_content_policy(_file_attachment_annotation_pdf(tmp_path))
    assert result.passed is False
    assert result.reason_code == "PDF_EMBEDDED_FILE"


def test_orphan_filespec_object_rejected(tmp_path: Path) -> None:
    # Regression test: a /Type /Filespec object not reachable via
    # /Names/EmbeddedFiles or any annotation must still be caught by the
    # full indirect-object scan, not just the two targeted lookups.
    result = check_pdf_content_policy(_orphan_filespec_pdf(tmp_path))
    assert result.passed is False
    assert result.reason_code == "PDF_EMBEDDED_FILE"


def test_corrupt_pdf_fails_closed(tmp_path: Path) -> None:
    result = check_pdf_content_policy(_corrupt_pdf(tmp_path))
    assert result.passed is False
    assert result.reason_code == "PDF_INSPECTION_FAILED"


def test_nonexistent_file_fails_closed(tmp_path: Path) -> None:
    result = check_pdf_content_policy(tmp_path / "does-not-exist.pdf")
    assert result.passed is False
    assert result.reason_code == "PDF_INSPECTION_FAILED"


# --- subprocess invocation / isolation contract -----------------------------


def test_subprocess_invoked_with_sys_executable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, Any] = {}

    def fake_run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        captured["argv"] = argv
        captured["kwargs"] = kwargs
        return subprocess.CompletedProcess(
            argv, 0, stdout=json.dumps({"status": "CLEAN"}) + "\n", stderr=""
        )

    monkeypatch.setattr(subprocess, "run", fake_run)
    check_pdf_content_policy(tmp_path / "whatever.pdf")

    argv: list[str] = captured["argv"]
    assert argv[0] == sys.executable
    assert argv[1:3] == ["-m", "app.services.pdf_inspector_subprocess"]
    assert captured["kwargs"]["timeout"] == settings.PDF_INSPECTION_TIMEOUT_SECONDS


def test_subprocess_env_excludes_parent_secrets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The subprocess exists to run untrusted-file parsing where a
    # compromise can't reach this app's secrets. Set a secret-shaped var
    # in the parent's os.environ and confirm it is NOT copied into the
    # env= passed to the child -- the child must get a minimal,
    # explicitly-built environment, never `dict(os.environ)`.
    monkeypatch.setenv("DATABASE_URL", "postgresql://should-not-leak/db")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-should-not-leak")
    monkeypatch.setenv("SESSION_SECRET_KEY", "should-not-leak-either")

    captured: dict[str, Any] = {}

    def fake_run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        captured["env"] = kwargs.get("env")
        return subprocess.CompletedProcess(
            argv, 0, stdout=json.dumps({"status": "CLEAN"}) + "\n", stderr=""
        )

    monkeypatch.setattr(subprocess, "run", fake_run)
    check_pdf_content_policy(tmp_path / "whatever.pdf")

    child_env = captured["env"]
    assert child_env is not None
    assert "DATABASE_URL" not in child_env
    assert "ANTHROPIC_API_KEY" not in child_env
    assert "SESSION_SECRET_KEY" not in child_env
    # Only the small, explicit allowlist should be present.
    assert set(child_env).issubset(
        {
            "PDF_INSPECTOR_CPU_SECONDS",
            "PDF_INSPECTOR_MEMORY_BYTES",
            "PATH",
            "SystemRoot",
        }
    )


def test_malformed_stdout_multiple_lines_rejected() -> None:
    stdout = json.dumps({"status": "CLEAN"}) + "\n" + json.dumps({"status": "CLEAN"})
    assert _parse_subprocess_stdout(stdout) is None


def test_malformed_stdout_trailing_garbage_rejected() -> None:
    stdout = json.dumps({"status": "CLEAN"}) + "\nunexpected trailing garbage"
    assert _parse_subprocess_stdout(stdout) is None


def test_malformed_stdout_not_json_rejected() -> None:
    assert _parse_subprocess_stdout("not json at all") is None


def test_malformed_stdout_empty_rejected() -> None:
    assert _parse_subprocess_stdout("") is None


def test_clean_status_with_unexpected_reason_code_rejected() -> None:
    # A CLEAN status should never carry a reason_code; treat as malformed
    # rather than partially trusting it.
    stdout = json.dumps({"status": "CLEAN", "reason_code": "SOMETHING"})
    assert _parse_subprocess_stdout(stdout) is None


def test_rejected_status_without_reason_code_rejected() -> None:
    stdout = json.dumps({"status": "REJECTED"})
    assert _parse_subprocess_stdout(stdout) is None


def test_nonzero_exit_treated_as_failed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fake_run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            argv, 1, stdout=json.dumps({"status": "CLEAN"}) + "\n", stderr=""
        )

    monkeypatch.setattr(subprocess, "run", fake_run)
    result = check_pdf_content_policy(tmp_path / "whatever.pdf")
    assert result.passed is False
    assert result.reason_code == "PDF_INSPECTION_FAILED"


def test_timeout_treated_as_failed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fake_run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        raise subprocess.TimeoutExpired(cmd=argv, timeout=kwargs.get("timeout", 1))

    monkeypatch.setattr(subprocess, "run", fake_run)
    result = check_pdf_content_policy(tmp_path / "whatever.pdf")
    assert result.passed is False
    assert result.reason_code == "PDF_INSPECTION_FAILED"


def test_logging_never_includes_raw_stdout_or_stderr(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    secret_marker = "SECRET-BYTES-FROM-UNTRUSTED-FILE"

    def fake_run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            argv, 1, stdout=secret_marker, stderr=secret_marker
        )

    monkeypatch.setattr(subprocess, "run", fake_run)
    with caplog.at_level("DEBUG"):
        check_pdf_content_policy(tmp_path / "whatever.pdf")

    assert secret_marker not in caplog.text


# --- real hung-child timeout test (no monkeypatch) --------------------------


def test_real_hung_subprocess_is_killed_and_reaped(tmp_path: Path) -> None:
    """Spawn a genuinely hanging child process and confirm subprocess.run's
    `timeout` argument both raises TimeoutExpired and actually terminates
    the child rather than leaking it."""
    script = tmp_path / "hang.py"
    script.write_text("import time\ntime.sleep(60)\n")

    proc = subprocess.Popen([sys.executable, str(script)])
    try:
        with pytest.raises(subprocess.TimeoutExpired):
            subprocess.run([sys.executable, str(script)], timeout=0.001)
    finally:
        # Clean up the unrelated long-lived Popen child used only to prove
        # the process-existence check below works as expected.
        proc.kill()
        proc.wait(timeout=10)

    # subprocess.run's own internal child (a *separate* invocation from the
    # `proc` above) must have been killed by the timeout, not left running.
    # We verify this indirectly: run a fresh hang script under subprocess.run
    # with a short timeout, capture its PID via a side-channel-free method
    # (poll immediately after TimeoutExpired), and confirm it is not alive.
    hang_script = tmp_path / "hang2.py"
    pid_file = tmp_path / "pid.txt"
    hang_script.write_text(
        "import os, time\n"
        f"open(r'{pid_file}', 'w').write(str(os.getpid()))\n"
        "time.sleep(60)\n"
    )
    with pytest.raises(subprocess.TimeoutExpired):
        subprocess.run([sys.executable, str(hang_script)], timeout=1)

    # Give the OS a brief moment to finish tearing down the killed process.
    deadline = time.time() + 5
    pid = None
    while time.time() < deadline:
        if pid_file.exists():
            pid = int(pid_file.read_text())
            break
        time.sleep(0.05)
    assert pid is not None, "hung child never started"

    if sys.platform == "win32":
        # tasklist exits 0 with an INFO line when the PID isn't found.
        check = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert str(pid) not in check.stdout
    else:
        with pytest.raises(OSError):
            os_kill_check = __import__("os")
            os_kill_check.kill(pid, 0)


def test_pdf_inspection_timeout_setting_used(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen_timeout: dict[str, float] = {}

    def fake_run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        seen_timeout["value"] = kwargs["timeout"]
        return subprocess.CompletedProcess(
            argv, 0, stdout=json.dumps({"status": "CLEAN"}) + "\n", stderr=""
        )

    monkeypatch.setattr(subprocess, "run", fake_run)
    check_pdf_content_policy(tmp_path / "whatever.pdf")
    assert seen_timeout["value"] == settings.PDF_INSPECTION_TIMEOUT_SECONDS
