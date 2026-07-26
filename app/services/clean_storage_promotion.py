"""Crash-safe clean-storage promotion service.

Moves validated, malware-clean documents from quarantine storage to clean
storage (LOCAL_STORAGE_PATH) with atomic staged copy, SHA-256/size verification,
fsync guarantees, PostgreSQL row locking, and crash-consistent status transitions.

Design invariants:
- Only documents in CLEAN_PENDING_PROMOTION, PROMOTING, or PROMOTION_FAILED
  can be promoted.
- Filesystem promotion uses staged copy to a `.partial` file in clean storage,
  computes SHA-256 and byte length during copy, fsyncs the file and directory,
  and atomically renames `.partial` to the final opaque identifier filename.
- Source quarantine file is opened with O_RDONLY | O_NOFOLLOW and checked
  against symlinks, path traversal, digest drift, and size mismatch.
- Destination creation uses O_CREAT | O_EXCL | O_NOFOLLOW with file mode 0600.
  Existing destination files are never overwritten; if a complete file exists
  with matching digest and size, promotion is treated as idempotently complete.
- State sequence:
  1. Lock row, transition CLEAN_PENDING_PROMOTION -> PROMOTING, commit.
  2. Perform staged filesystem promotion and verification.
  3. Lock row in fresh transaction, update file_path / clean_storage_identifier,
     transition PROMOTING -> CLEAN, commit.
  4. Attempt quarantine source file deletion. If deletion fails, mark
     cleanup_pending = True without invalidating CLEAN state.
- On failure at any step, the `.partial` file is removed, quarantine source is
  preserved, and status transitions to PROMOTION_FAILED with a fixed reason code.
"""

from __future__ import annotations

import hashlib
import os
import stat
import uuid
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.document import Document
from app.models.project import ProposalProject
from app.services.ingestion_state import (
    IngestionStateError,
    IngestionStatus,
    transition,
)
from app.services.quarantine_storage import resolve_quarantine_path

_CHUNK_SIZE = 65536
_CLEAN_SUFFIX = ".clean"
_PARTIAL_SUFFIX = ".partial"

# Fixed reason codes
PROMOTION_SOURCE_MISSING = "PROMOTION_SOURCE_MISSING"
PROMOTION_SOURCE_INVALID = "PROMOTION_SOURCE_INVALID"
PROMOTION_DIGEST_MISMATCH = "PROMOTION_DIGEST_MISMATCH"
PROMOTION_DESTINATION_CONFLICT = "PROMOTION_DESTINATION_CONFLICT"
PROMOTION_COPY_FAILED = "PROMOTION_COPY_FAILED"
PROMOTION_FSYNC_FAILED = "PROMOTION_FSYNC_FAILED"
PROMOTION_DATABASE_FAILED = "PROMOTION_DATABASE_FAILED"
PROMOTION_TENANT_MISMATCH = "PROMOTION_TENANT_MISMATCH"


class PromotionError(Exception):
    """Raised when clean storage promotion fails."""

    def __init__(self, reason_code: str, message: str) -> None:
        super().__init__(message)
        self.reason_code = reason_code


def _resolve_clean_storage_root() -> Path:
    """Resolve and validate clean storage root directory."""
    root = Path(settings.LOCAL_STORAGE_PATH).resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root


def resolve_clean_path(identifier: str) -> Path:
    """Resolve a clean storage identifier back to an absolute filesystem path.

    Confirms containment within `LOCAL_STORAGE_PATH` and symlink rejection.
    """
    root = _resolve_clean_storage_root()
    clean_path = (root / identifier).resolve()

    try:
        clean_path.relative_to(root)
    except ValueError as err:
        raise PromotionError(
            PROMOTION_SOURCE_INVALID,
            f"Storage identifier {identifier!r} escapes clean root",
        ) from err

    if clean_path.is_symlink():
        raise PromotionError(
            PROMOTION_SOURCE_INVALID,
            f"Clean storage path for {identifier!r} is a symlink",
        )

    return clean_path


def _fsync_dir(dir_path: Path) -> None:
    """Flush directory metadata to disk on POSIX hosts."""
    if os.name == "nt":
        return
    try:
        fd = os.open(dir_path, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    except OSError:
        pass


def _perform_staged_copy(
    src_path: Path,
    dst_root: Path,
    clean_identifier: str,
    expected_digest: str,
    expected_size: int,
) -> Path:
    """Perform atomic staged copy of quarantine file into clean storage.

    Returns absolute path to the finalized clean file.
    """
    final_dst_path = dst_root / clean_identifier
    partial_dst_path = dst_root / (clean_identifier + _PARTIAL_SUFFIX)

    # Containment checks
    if final_dst_path.resolve().parent != dst_root.resolve():
        raise PromotionError(
            PROMOTION_SOURCE_INVALID, "Destination path escapes clean storage root"
        )

    # Check if final destination already exists (Idempotency / Conflict check)
    if final_dst_path.exists():
        if final_dst_path.is_symlink():
            raise PromotionError(
                PROMOTION_DESTINATION_CONFLICT, "Existing destination is a symlink"
            )
        st = final_dst_path.stat()
        if st.st_size == expected_size:
            h = hashlib.sha256()
            with open(final_dst_path, "rb") as f:
                while chunk := f.read(_CHUNK_SIZE):
                    h.update(chunk)
            if h.hexdigest().lower() == expected_digest.lower():
                # Idempotent completion
                return final_dst_path

        raise PromotionError(
            PROMOTION_DESTINATION_CONFLICT,
            f"Destination {clean_identifier} exists with conflicting content",
        )

    # Open source with O_RDONLY and O_NOFOLLOW
    flags_src = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags_src |= os.O_NOFOLLOW

    try:
        src_fd = os.open(src_path, flags_src)
    except OSError as err:
        raise PromotionError(
            PROMOTION_SOURCE_MISSING, f"Cannot open source file: {err}"
        ) from err

    try:
        st_src = os.fstat(src_fd)
        if not stat.S_ISREG(st_src.st_mode):
            raise PromotionError(
                PROMOTION_SOURCE_INVALID, "Source is not a regular file"
            )
        if st_src.st_size != expected_size:
            raise PromotionError(
                PROMOTION_DIGEST_MISMATCH,
                f"Source size {st_src.st_size} != expected {expected_size}",
            )

        # Open partial destination with O_CREAT | O_EXCL | O_NOFOLLOW, mode 0600
        flags_dst = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        if hasattr(os, "O_NOFOLLOW"):
            flags_dst |= os.O_NOFOLLOW
        if hasattr(os, "O_BINARY"):
            flags_dst |= os.O_BINARY

        try:
            dst_fd = os.open(
                partial_dst_path,
                flags_dst,
                stat.S_IRUSR | stat.S_IWUSR,
            )
        except OSError as err:
            raise PromotionError(
                PROMOTION_COPY_FAILED, f"Cannot create partial destination: {err}"
            ) from err

        hasher = hashlib.sha256()
        copied_bytes = 0

        try:
            while True:
                chunk = os.read(src_fd, _CHUNK_SIZE)
                if not chunk:
                    break
                hasher.update(chunk)
                copied_bytes += len(chunk)
                os.write(dst_fd, chunk)

            if copied_bytes != expected_size:
                raise PromotionError(
                    PROMOTION_DIGEST_MISMATCH,
                    f"Copied bytes {copied_bytes} != expected {expected_size}",
                )

            actual_digest = hasher.hexdigest().lower()
            if actual_digest != expected_digest.lower():
                raise PromotionError(
                    PROMOTION_DIGEST_MISMATCH,
                    f"Digest mismatch: {actual_digest} != {expected_digest}",
                )

            # Flush file to disk
            try:
                os.fsync(dst_fd)
            except OSError as err:
                raise PromotionError(
                    PROMOTION_FSYNC_FAILED, f"Destination fsync failed: {err}"
                ) from err

        finally:
            os.close(dst_fd)

    finally:
        os.close(src_fd)

    # Atomic rename from .partial to final destination name
    try:
        os.replace(partial_dst_path, final_dst_path)
    except OSError as err:
        if partial_dst_path.exists():
            try:
                partial_dst_path.unlink()
            except OSError:
                pass
        raise PromotionError(
            PROMOTION_COPY_FAILED, f"Atomic rename failed: {err}"
        ) from err

    _fsync_dir(dst_root)
    return final_dst_path


def _cleanup_partial_file(dst_root: Path, clean_identifier: str) -> None:
    """Best-effort removal of a residual .partial file."""
    partial_path = dst_root / (clean_identifier + _PARTIAL_SUFFIX)
    if partial_path.exists():
        try:
            partial_path.unlink()
        except OSError:
            pass


def promote_document(
    db: Session,
    document_id: uuid.UUID,
    *,
    org_id: uuid.UUID,
    user_id: uuid.UUID | None = None,
) -> Document:
    """Execute crash-safe clean-storage promotion for `document_id`.

    Locks document row, validates status/digest/size, performs staged copy
    to clean storage, updates database to CLEAN, and cleans up quarantine file.
    """
    # 1. Lock document and validate state
    doc = (
        db.query(Document).filter(Document.id == document_id).with_for_update().first()
    )
    if not doc:
        raise PromotionError(
            PROMOTION_SOURCE_MISSING, f"Document {document_id} not found"
        )

    # Confirm tenant/project ownership
    project = (
        db.query(ProposalProject).filter(ProposalProject.id == doc.project_id).first()
    )
    if not project or project.organization_id != org_id:
        raise PromotionError(
            PROMOTION_TENANT_MISMATCH,
            f"Tenant {org_id} does not own document {document_id}",
        )

    current_status = doc.ingestion_status
    if current_status not in (
        IngestionStatus.CLEAN_PENDING_PROMOTION,
        IngestionStatus.PROMOTING,
        IngestionStatus.PROMOTION_FAILED,
    ):
        raise IngestionStateError(
            f"Cannot promote document in status {current_status!r}"
        )

    # Digest and size validations
    if not doc.sha256_digest or doc.file_size_bytes is None:
        raise PromotionError(
            PROMOTION_SOURCE_INVALID,
            "Document missing sha256_digest or file_size_bytes",
        )

    expected_digest = doc.sha256_digest
    expected_size = doc.file_size_bytes

    # Transition to PROMOTING and commit
    now = datetime.now(UTC)
    doc.promotion_started_at = now
    doc.promotion_attempt_count = (doc.promotion_attempt_count or 0) + 1
    transition(
        db,
        doc,
        IngestionStatus.PROMOTING,
        org_id=org_id,
        user_id=user_id,
        audit_detail={
            "promotion_attempt": doc.promotion_attempt_count,
            "source_digest": expected_digest,
        },
    )

    clean_root = _resolve_clean_storage_root()
    clean_identifier = doc.clean_storage_identifier or (
        str(uuid.uuid4()) + _CLEAN_SUFFIX
    )

    # 2. Perform staged copy & verification
    try:
        try:
            storage_id = doc.file_path.removesuffix(".upload") if doc.file_path else ""
            quarantine_src_path = resolve_quarantine_path(storage_id)
        except Exception as err:
            raise PromotionError(
                PROMOTION_SOURCE_INVALID, f"Quarantine path resolution failed: {err}"
            ) from err

        if not quarantine_src_path.exists():
            raise PromotionError(
                PROMOTION_SOURCE_MISSING,
                f"Quarantine source file missing at {doc.file_path}",
            )

        final_clean_path = _perform_staged_copy(
            quarantine_src_path,
            clean_root,
            clean_identifier,
            expected_digest,
            expected_size,
        )
    except PromotionError as err:
        _cleanup_partial_file(clean_root, clean_identifier)
        # Lock document again to record PROMOTION_FAILED
        doc = (
            db.query(Document)
            .filter(Document.id == document_id)
            .with_for_update()
            .first()
        )
        if doc and doc.ingestion_status == IngestionStatus.PROMOTING:
            transition(
                db,
                doc,
                IngestionStatus.PROMOTION_FAILED,
                org_id=org_id,
                user_id=user_id,
                reason_code=err.reason_code,
                safe_summary=str(err),
            )
        raise
    except Exception as err:
        _cleanup_partial_file(clean_root, clean_identifier)
        doc = (
            db.query(Document)
            .filter(Document.id == document_id)
            .with_for_update()
            .first()
        )
        if doc and doc.ingestion_status == IngestionStatus.PROMOTING:
            transition(
                db,
                doc,
                IngestionStatus.PROMOTION_FAILED,
                org_id=org_id,
                user_id=user_id,
                reason_code=PROMOTION_COPY_FAILED,
                safe_summary=f"Unexpected copy failure: {err}",
            )
        raise PromotionError(
            PROMOTION_COPY_FAILED, f"Unexpected copy failure: {err}"
        ) from err

    # 3. Lock document in fresh query and transition to CLEAN
    doc = (
        db.query(Document).filter(Document.id == document_id).with_for_update().first()
    )
    if not doc or doc.ingestion_status != IngestionStatus.PROMOTING:
        raise PromotionError(
            PROMOTION_DATABASE_FAILED, "Document state changed during promotion"
        )

    # Relative path from clean root or full path
    rel_clean_path = str(final_clean_path.relative_to(clean_root))
    doc.file_path = rel_clean_path
    doc.clean_storage_identifier = clean_identifier
    doc.promotion_completed_at = datetime.now(UTC)

    transition(
        db,
        doc,
        IngestionStatus.CLEAN,
        org_id=org_id,
        user_id=user_id,
        audit_detail={
            "clean_storage_identifier": clean_identifier,
            "clean_file_path": rel_clean_path,
        },
    )

    # 4. Quarantine source cleanup
    cleanup_success = False
    try:
        if quarantine_src_path.exists() and not quarantine_src_path.is_symlink():
            quarantine_src_path.unlink()
            cleanup_success = True
            _fsync_dir(quarantine_src_path.parent)
    except OSError:
        cleanup_success = False

    if not cleanup_success:
        # Mark cleanup_pending = True without invalidating CLEAN state
        doc = (
            db.query(Document)
            .filter(Document.id == document_id)
            .with_for_update()
            .first()
        )
        if doc:
            doc.cleanup_pending = True
            db.commit()

    assert doc is not None
    return doc
