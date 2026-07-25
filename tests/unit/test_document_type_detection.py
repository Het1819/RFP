import inspect
from pathlib import Path

import pytest

from app.services.document_type_detection import DetectedType, detect_pdf_candidate


def _write(tmp_path: Path, content: bytes, name: str = "f.pdf") -> Path:
    p = tmp_path / name
    p.write_bytes(content)
    return p


class TestDetectPdfCandidate:
    def test_valid_pdf_candidate_detected(self, tmp_path: Path) -> None:
        content = b"%PDF-1.4\n" + b"1 0 obj\n<<>>\nendobj\n" + b"%%EOF"
        result = detect_pdf_candidate(
            _write(tmp_path, content), declared_extension=".pdf"
        )
        assert result.detected_type == DetectedType.PDF
        assert result.canonical_mime == "application/pdf"

    def test_arbitrary_bytes_renamed_pdf_fails(self, tmp_path: Path) -> None:
        content = b"this is just plain text pretending to be a pdf file padding"
        result = detect_pdf_candidate(
            _write(tmp_path, content), declared_extension=".pdf"
        )
        assert result.detected_type == DetectedType.UNKNOWN
        assert result.reason_code is not None

    def test_missing_header_fails(self, tmp_path: Path) -> None:
        content = b"no header here\n" + b"x" * 100 + b"\n%%EOF"
        result = detect_pdf_candidate(
            _write(tmp_path, content), declared_extension=".pdf"
        )
        assert result.detected_type == DetectedType.UNKNOWN

    def test_missing_eof_marker_fails(self, tmp_path: Path) -> None:
        content = b"%PDF-1.4\n" + b"x" * 100
        result = detect_pdf_candidate(
            _write(tmp_path, content), declared_extension=".pdf"
        )
        assert result.detected_type == DetectedType.UNKNOWN

    def test_truncated_candidate_fails(self, tmp_path: Path) -> None:
        result = detect_pdf_candidate(
            _write(tmp_path, b"%PDF-1."), declared_extension=".pdf"
        )
        assert result.detected_type == DetectedType.UNKNOWN

    def test_valid_pdf_with_docx_display_extension_fails(self, tmp_path: Path) -> None:
        content = b"%PDF-1.4\nx\n%%EOF"
        result = detect_pdf_candidate(
            _write(tmp_path, content, name="f.docx"), declared_extension=".docx"
        )
        assert result.detected_type == DetectedType.UNKNOWN
        assert result.reason_code == "EXTENSION_TYPE_MISMATCH"

    def test_detection_does_not_import_pymupdf(self) -> None:
        import app.services.document_type_detection as mod

        src = inspect.getsource(mod)
        assert "import fitz" not in src
        assert "pymupdf" not in src.lower()

    @pytest.mark.parametrize("version", [b"%PDF-1.0", b"%PDF-1.7", b"%PDF-2.0"])
    def test_accepted_header_versions(self, tmp_path: Path, version: bytes) -> None:
        content = version + b"\nx\n%%EOF"
        result = detect_pdf_candidate(
            _write(tmp_path, content), declared_extension=".pdf"
        )
        assert result.detected_type == DetectedType.PDF
