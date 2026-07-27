"""Tests for app.services.quarantine_storage: filename normalization,
streaming upload writes (hashing/size enforcement/exclusive creation),
and storage-identifier path resolution (containment/symlink rejection)."""

import hashlib
import io
import os
import unicodedata
import uuid
from pathlib import Path

import pytest
from fastapi import UploadFile
from starlette.datastructures import Headers

from app.core.config import settings
from app.services.quarantine_storage import (
    QuarantineStorageError,
    delete_quarantine_file,
    normalize_display_filename,
    resolve_quarantine_path,
    stream_upload_to_quarantine,
)


def _upload(content: bytes, filename: str = "x.pdf") -> UploadFile:
    return UploadFile(
        filename=filename,
        file=io.BytesIO(content),
        headers=Headers({"content-type": "application/pdf"}),
    )


class TestNormalizeDisplayFilename:
    def test_windows_path_reduced_to_basename(self) -> None:
        assert normalize_display_filename(r"C:\Users\evil\..\rfp.pdf") == "rfp.pdf"

    def test_posix_path_reduced_to_basename(self) -> None:
        assert normalize_display_filename("/etc/passwd/../rfp.pdf") == "rfp.pdf"

    def test_nul_and_control_characters_removed(self) -> None:
        assert normalize_display_filename("rfp\x00\x01.pdf") == "rfp.pdf"

    def test_unicode_normalized_to_nfc(self) -> None:
        # "e" + combining acute accent (NFD) -> should normalize to NFC "é"
        decomposed = "re\u0301sume\u0301.pdf"
        result = normalize_display_filename(decomposed)
        assert result == unicodedata.normalize("NFC", decomposed)

    def test_overlong_filename_is_bounded(self) -> None:
        long_name = ("a" * 500) + ".pdf"
        result = normalize_display_filename(long_name)
        assert len(result) <= 255
        assert result.endswith(".pdf")

    def test_empty_or_unsafe_name_uses_fallback(self) -> None:
        assert normalize_display_filename("") == "Uploaded document"
        assert normalize_display_filename(None) == "Uploaded document"
        assert normalize_display_filename("....") == "Uploaded document"

    def test_trailing_dots_and_spaces_trimmed(self) -> None:
        assert normalize_display_filename("rfp.pdf   ...") == "rfp.pdf"

    def test_safe_pdf_suffix_preserved(self) -> None:
        assert normalize_display_filename("My RFP Response.pdf").endswith(".pdf")

    def test_safe_docx_suffix_preserved(self) -> None:
        assert (
            normalize_display_filename("Knowledge Doc.DOCX").lower().endswith(".docx")
        )


class TestStreamUploadToQuarantine:
    def test_empty_upload_fails(self) -> None:
        with pytest.raises(QuarantineStorageError) as exc:
            stream_upload_to_quarantine(_upload(b""))
        assert exc.value.code == "EMPTY_FILE"

    def test_empty_upload_leaves_no_partial_file(self) -> None:
        before = set(Path(settings.QUARANTINE_STORAGE_PATH).glob("*"))
        with pytest.raises(QuarantineStorageError):
            stream_upload_to_quarantine(_upload(b""))
        after = set(Path(settings.QUARANTINE_STORAGE_PATH).glob("*"))
        assert before == after

    def test_valid_small_upload_succeeds(self) -> None:
        content = b"%PDF-1.4\n" + b"x" * 1000 + b"\n%%EOF"
        result = stream_upload_to_quarantine(_upload(content))
        assert result.byte_size == len(content)
        assert result.sha256_digest == hashlib.sha256(content).hexdigest()
        assert result.storage_path.exists()
        assert result.header_bytes == content[:1024]
        assert result.tail_bytes == content[-1024:]
        result.storage_path.unlink()

    def test_oversized_upload_fails_without_full_buffering(self) -> None:
        oversized = b"x" * (settings.MAX_UPLOAD_SIZE + 1)
        before = set(Path(settings.QUARANTINE_STORAGE_PATH).glob("*"))
        with pytest.raises(QuarantineStorageError) as exc:
            stream_upload_to_quarantine(_upload(oversized))
        assert exc.value.code == "TOO_LARGE"
        after = set(Path(settings.QUARANTINE_STORAGE_PATH).glob("*"))
        assert before == after  # partial file cleaned up

    def test_generated_filename_contains_no_submitted_filename(self) -> None:
        content = b"%PDF-1.4\nx\n%%EOF"
        upload = _upload(content, filename="super-secret-client-name.pdf")
        result = stream_upload_to_quarantine(upload)
        assert "super-secret-client-name" not in str(result.storage_path)
        assert result.storage_path.suffix == ".upload"
        assert str(result.storage_id) in result.storage_path.name
        result.storage_path.unlink()

    def test_existing_file_is_never_overwritten(self, monkeypatch) -> None:
        content = b"%PDF-1.4\nx\n%%EOF"
        other_content = b"%PDF-1.4\nDIFFERENT\n%%EOF"
        fixed_id = uuid.uuid4()
        monkeypatch.setattr("uuid.uuid4", lambda: fixed_id)
        result1 = stream_upload_to_quarantine(_upload(content))
        assert result1.storage_id == fixed_id

        with pytest.raises(QuarantineStorageError) as exc:
            # Second call with the same monkeypatched uuid4 must not
            # silently overwrite the first file.
            stream_upload_to_quarantine(_upload(other_content))
        assert exc.value.code == "IDENTIFIER_COLLISION"

        # Original content must be untouched.
        assert result1.storage_path.read_bytes() == content
        result1.storage_path.unlink()

    def test_file_permissions_are_restrictive_on_posix(self) -> None:
        if os.name != "posix":
            pytest.skip("POSIX-only permission check")
        content = b"%PDF-1.4\nx\n%%EOF"
        result = stream_upload_to_quarantine(_upload(content))
        mode = result.storage_path.stat().st_mode & 0o777
        assert mode == 0o600
        result.storage_path.unlink()

    def test_read_failure_cleans_up_partial_file(self) -> None:
        class _BoomFile:
            def read(self, _size: int) -> bytes:
                raise OSError("boom")

        upload = UploadFile(
            filename="x.pdf",
            file=_BoomFile(),  # type: ignore[arg-type]
            headers=Headers({"content-type": "application/pdf"}),
        )
        before = set(Path(settings.QUARANTINE_STORAGE_PATH).glob("*"))
        with pytest.raises(QuarantineStorageError) as exc:
            stream_upload_to_quarantine(upload)
        assert exc.value.code == "READ_FAILURE"
        after = set(Path(settings.QUARANTINE_STORAGE_PATH).glob("*"))
        assert before == after

    def test_custom_max_size_enforced(self) -> None:
        content = b"x" * 100
        with pytest.raises(QuarantineStorageError) as exc:
            stream_upload_to_quarantine(_upload(content), max_size=10)
        assert exc.value.code == "TOO_LARGE"


class TestResolveQuarantinePath:
    def test_valid_uuid_resolves_under_root(self) -> None:
        storage_id = uuid.uuid4()
        path = resolve_quarantine_path(storage_id)
        root = Path(settings.QUARANTINE_STORAGE_PATH).resolve()
        assert path.parent == root
        assert path.name == f"{storage_id}.upload"

    def test_string_uuid_accepted(self) -> None:
        storage_id = uuid.uuid4()
        path = resolve_quarantine_path(str(storage_id))
        assert path.name == f"{storage_id}.upload"

    def test_symlink_destination_rejected(self, tmp_path) -> None:
        if os.name != "posix":
            pytest.skip("POSIX-only symlink check")
        real_target = tmp_path / "outside.txt"
        real_target.write_text("nope")
        storage_id = uuid.uuid4()
        link_path = Path(settings.QUARANTINE_STORAGE_PATH) / f"{storage_id}.upload"
        Path(settings.QUARANTINE_STORAGE_PATH).mkdir(parents=True, exist_ok=True)
        os.symlink(real_target, link_path)
        try:
            with pytest.raises(QuarantineStorageError) as exc:
                resolve_quarantine_path(storage_id)
            assert exc.value.code == "SYMLINK_REJECTED"
        finally:
            link_path.unlink()

    @pytest.mark.parametrize(
        "bad_id",
        [
            "../../etc/passwd",
            "..\\..\\windows\\system32",
            "C:\\evil",
            "id\x00with-nul",
            "not-a-uuid-at-all",
            "",
            "   ",
            "00000000-0000-0000-0000-00000000000g",  # invalid hex digit
            "../" * 50 + "etc/passwd",
            "\x00\x00\x00\x00-0000-0000-0000-000000000000",
        ],
    )
    def test_path_traversal_and_malformed_identifiers_rejected(
        self, bad_id: str
    ) -> None:
        with pytest.raises((QuarantineStorageError, ValueError)):
            resolve_quarantine_path(bad_id)  # type: ignore[arg-type]

    def test_symlink_pointing_inside_root_is_still_rejected(
        self, tmp_path: Path
    ) -> None:
        # Regression test for the symlink-check-ordering bug: a symlink at
        # the storage-id path that points to ANOTHER file *inside* the
        # quarantine root must still be rejected. Checking `is_symlink()`
        # after `.resolve()` would make this pass containment silently
        # (both paths are under root) and return the linked-to file's real
        # path with no rejection at all -- `is_symlink()` on an
        # already-dereferenced path is always False. On POSIX this is
        # exercised directly via os.symlink(); skipped on Windows where
        # symlink creation typically requires elevated privileges.
        if os.name != "posix":
            pytest.skip("POSIX-only symlink check")
        root = Path(settings.QUARANTINE_STORAGE_PATH)
        root.mkdir(parents=True, exist_ok=True)
        real_target = root / "inside-target.upload"
        real_target.write_text("real file inside root")
        storage_id = uuid.uuid4()
        link_path = root / f"{storage_id}.upload"
        os.symlink(real_target, link_path)
        try:
            with pytest.raises(QuarantineStorageError) as exc:
                resolve_quarantine_path(storage_id)
            assert exc.value.code == "SYMLINK_REJECTED"
        finally:
            link_path.unlink()
            real_target.unlink()


class TestDeleteQuarantineFile:
    def test_delete_removes_existing_file(self) -> None:
        content = b"%PDF-1.4\nx\n%%EOF"
        result = stream_upload_to_quarantine(_upload(content))
        assert result.storage_path.exists()
        delete_quarantine_file(result.storage_id)
        assert not result.storage_path.exists()

    def test_delete_is_idempotent_when_already_gone(self) -> None:
        storage_id = uuid.uuid4()
        # No file was ever created for this id.
        delete_quarantine_file(storage_id)  # must not raise
        delete_quarantine_file(storage_id)  # calling again must not raise

    def test_delete_rejects_invalid_identifier(self) -> None:
        with pytest.raises((QuarantineStorageError, ValueError)):
            delete_quarantine_file("not-a-uuid")  # type: ignore[arg-type]
