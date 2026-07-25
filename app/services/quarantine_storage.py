"""Quarantine storage service.

Security-critical module: owns filename normalization (for *display*
purposes only), UUID-based storage identifiers, streaming upload writes
with SHA-256/size computation, secure exclusive file creation (never
overwrites an existing file), and path-containment / symlink-rejection
when resolving a storage identifier back to a filesystem path.

Design invariants:
- The attacker-controlled original filename is used ONLY to derive a
  display name (never a storage path component).
- Storage identifiers are server-generated UUIDs; the on-disk filename is
  always `{uuid}.upload` under `settings.QUARANTINE_STORAGE_PATH`.
- Upload size is enforced incrementally, chunk by chunk, while streaming
  to disk -- the full payload is never buffered in memory and an
  oversized upload fails after at most one extra chunk beyond the limit.
- File creation uses `os.O_CREAT | os.O_EXCL` (plus `os.O_NOFOLLOW` where
  available) so a pre-existing path -- including an attacker-planted
  symlink -- can never be silently overwritten or followed on creation.
- `resolve_quarantine_path` validates the identifier as a UUID and
  confirms the resolved path is a direct, non-symlink child of the
  quarantine root before it is used for any filesystem operation.
"""

from __future__ import annotations

import hashlib
import os
import unicodedata
import uuid
from dataclasses import dataclass
from pathlib import Path, PureWindowsPath

from fastapi import UploadFile

from app.core.config import settings

_SAFE_SUFFIXES = (".pdf", ".docx")
_FALLBACK_NAME = "Uploaded document"

_STORAGE_SUFFIX = ".upload"
_PARTIAL_SUFFIX = ".partial"
_HEADER_WINDOW_BYTES = 1024
_TAIL_WINDOW_BYTES = 1024


def normalize_display_filename(original: str | None) -> str:
    """Reduce an untrusted, attacker-controlled filename to a safe display
    name. This value is NEVER used to build a filesystem path -- storage
    paths are always derived from a server-generated UUID."""
    if not original:
        return _FALLBACK_NAME

    # Strip both POSIX and Windows path components regardless of host OS.
    basename = PureWindowsPath(original).name
    basename = basename.rsplit("/", 1)[-1]

    # Remove NUL and ASCII control characters (including DEL).
    basename = "".join(ch for ch in basename if ord(ch) >= 0x20 and ch != "\x7f")

    basename = unicodedata.normalize("NFC", basename)
    basename = basename.strip().rstrip(". ").strip()

    if not basename or basename.strip(". ") == "":
        return _FALLBACK_NAME

    suffix = ""
    lower = basename.lower()
    for safe in _SAFE_SUFFIXES:
        if lower.endswith(safe):
            suffix = basename[-len(safe) :]
            basename = basename[: -len(safe)]
            break

    max_len = settings.MAX_DISPLAY_FILENAME_LENGTH
    budget = max_len - len(suffix)
    if budget < 1:
        return _FALLBACK_NAME
    basename = basename[:budget]

    result = (basename + suffix).strip().rstrip(". ").strip()
    return result if result else _FALLBACK_NAME


class QuarantineStorageError(Exception):
    """Raised for any quarantine storage failure. `.code` is a fixed,
    machine-readable identifier -- never raw exception text -- so callers
    can branch on failure type without leaking internals."""

    def __init__(self, code: str, message: str = "") -> None:
        self.code = code
        super().__init__(message or code)


@dataclass(frozen=True)
class QuarantineWriteResult:
    storage_id: uuid.UUID
    storage_path: Path
    sha256_digest: str
    byte_size: int
    header_bytes: bytes
    tail_bytes: bytes


def _quarantine_root() -> Path:
    root = Path(settings.QUARANTINE_STORAGE_PATH)
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    return root.resolve()


def resolve_quarantine_path(storage_id: uuid.UUID | str) -> Path:
    """Validate `storage_id` as a UUID and resolve it to a path directly
    under the quarantine root. Rejects malformed identifiers, path
    traversal, and symlinks before any attacker-influenced value can
    reach a filesystem call that follows it."""
    try:
        validated_id = (
            storage_id
            if isinstance(storage_id, uuid.UUID)
            else uuid.UUID(str(storage_id))
        )
    except (ValueError, AttributeError, TypeError) as exc:
        raise QuarantineStorageError("INVALID_IDENTIFIER") from exc

    root = _quarantine_root()
    unresolved = root / f"{validated_id}{_STORAGE_SUFFIX}"

    # Symlink-ness MUST be checked on the unresolved path. `Path.resolve()`
    # fully dereferences symlinks (including in the final path component,
    # like `os.path.realpath`), so checking `is_symlink()` on an
    # already-resolved path can never be true -- it would silently follow
    # an attacker-planted symlink instead of rejecting it.
    if unresolved.is_symlink():
        raise QuarantineStorageError("SYMLINK_REJECTED")

    candidate = unresolved.resolve()
    if candidate.parent != root:
        raise QuarantineStorageError("PATH_ESCAPE")
    return candidate


def stream_upload_to_quarantine(
    upload: UploadFile, *, max_size: int | None = None
) -> QuarantineWriteResult:
    """Stream `upload` to a new quarantine file, computing its SHA-256
    digest and size incrementally. Enforces `max_size` (default
    `settings.MAX_UPLOAD_SIZE`) chunk-by-chunk so an oversized upload
    never causes full buffering. Always cleans up any partial file it
    created on failure. Raises `QuarantineStorageError` on any failure."""
    limit = max_size if max_size is not None else settings.MAX_UPLOAD_SIZE
    storage_id = uuid.uuid4()
    final_path = resolve_quarantine_path(storage_id)
    partial_path = final_path.with_name(final_path.name + _PARTIAL_SUFFIX)

    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        fd = os.open(partial_path, flags, 0o600)
    except FileExistsError as exc:
        raise QuarantineStorageError("IDENTIFIER_COLLISION") from exc
    except OSError as exc:
        raise QuarantineStorageError("WRITE_FAILURE") from exc

    hasher = hashlib.sha256()
    total = 0
    header_bytes = b""
    tail_window = b""

    try:
        with os.fdopen(fd, "wb") as handle:
            chunk_size = settings.QUARANTINE_CHUNK_SIZE_BYTES
            while True:
                try:
                    chunk = upload.file.read(chunk_size)
                except Exception as exc:
                    raise QuarantineStorageError("READ_FAILURE") from exc
                if not chunk:
                    break
                total += len(chunk)
                if total > limit:
                    raise QuarantineStorageError("TOO_LARGE")
                hasher.update(chunk)
                if len(header_bytes) < _HEADER_WINDOW_BYTES:
                    header_bytes += chunk[: _HEADER_WINDOW_BYTES - len(header_bytes)]
                tail_window = (tail_window + chunk)[-_TAIL_WINDOW_BYTES:]
                handle.write(chunk)
            handle.flush()
            os.fsync(handle.fileno())

        if total == 0:
            raise QuarantineStorageError("EMPTY_FILE")

        # Defense in depth: the O_CREAT|O_EXCL open above is the real
        # collision guarantee for the `.partial` name. This check catches
        # the (practically impossible outside of tests) case of a UUID
        # collision on the *final* name, and behaves consistently across
        # platforms even though POSIX os.rename() would otherwise silently
        # replace an existing destination while Windows would raise. This
        # is a non-atomic check-then-act (TOCTOU): another process could
        # create `final_path` between this check and the `os.rename()`
        # below. That race is acceptable here because `storage_id` is a
        # CSPRNG-random UUID (uuid4()), never attacker-controlled, so no
        # adversary can predict or target the exact `final_path` to win it.
        if final_path.exists():
            raise QuarantineStorageError("IDENTIFIER_COLLISION")
        os.rename(partial_path, final_path)

        try:
            dir_fd = os.open(final_path.parent, os.O_RDONLY)
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)
        except OSError:
            # Best-effort: some platforms (e.g. Windows) don't support
            # fsync-ing a directory handle at all.
            pass

        return QuarantineWriteResult(
            storage_id=storage_id,
            storage_path=final_path,
            sha256_digest=hasher.hexdigest(),
            byte_size=total,
            header_bytes=header_bytes,
            tail_bytes=tail_window,
        )
    except QuarantineStorageError:
        partial_path.unlink(missing_ok=True)
        raise
    except Exception as exc:
        partial_path.unlink(missing_ok=True)
        raise QuarantineStorageError("WRITE_FAILURE") from exc


def delete_quarantine_file(storage_id: uuid.UUID) -> None:
    """Delete a quarantine file, idempotent if already gone. Path is
    containment-checked via `resolve_quarantine_path` before deletion;
    `unlink` on a symlink removes the link itself rather than following
    it, but `resolve_quarantine_path` already rejects symlinks outright."""
    path = resolve_quarantine_path(storage_id)
    path.unlink(missing_ok=True)
