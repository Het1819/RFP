"""Typed loading of secrets from mounted files (Docker Compose secrets).

Local Compose secrets are plain mounted files on the container filesystem --
this module reads them safely, but mounting a file is NOT itself an
enterprise secret manager and is NOT proof of encrypted storage. Do not
claim otherwise in documentation.
"""

from pathlib import Path

# Generous bound for a secret value (API keys, passwords, hex/urlsafe
# tokens). Anything larger almost certainly indicates a misconfigured mount
# (e.g. an entire file mounted at the wrong path) and is rejected rather
# than silently truncated.
MAX_SECRET_FILE_BYTES = 8192


class SecretFileError(ValueError):
    """Raised for any problem reading a secret file. Messages may reference
    the file PATH (not sensitive) but must never include file CONTENTS."""


def read_secret_file(path: str | Path) -> str:
    """Read and return a secret value from a mounted file.

    Trims exactly one trailing newline (LF or CRLF) -- the kind editors and
    `printf`/`echo` commonly add -- and nothing else. Rejects missing,
    empty, oversized, or non-regular-file paths without ever including the
    file's contents in the resulting error.
    """
    p = Path(path)

    if not p.exists():
        raise SecretFileError(f"secret file not found: {p}")
    if p.is_dir():
        raise SecretFileError(f"secret path is a directory, not a file: {p}")
    if not p.is_file():
        raise SecretFileError(f"secret path is not a regular file: {p}")

    size = p.stat().st_size
    if size == 0:
        raise SecretFileError(f"secret file is empty: {p}")
    if size > MAX_SECRET_FILE_BYTES:
        raise SecretFileError(f"secret file exceeds maximum allowed size: {p}")

    try:
        raw = p.read_bytes()
    except OSError as exc:
        raise SecretFileError(f"secret file could not be read: {p}") from exc

    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise SecretFileError(f"secret file is not valid utf-8: {p}") from exc

    if text.endswith("\r\n"):
        text = text[:-2]
    elif text.endswith("\n"):
        text = text[:-1]

    if not text:
        raise SecretFileError(f"secret file is empty after trimming: {p}")

    return text
