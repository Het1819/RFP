import inspect
import zipfile
from pathlib import Path

import pytest

from app.services.document_type_detection import (
    DetectedType,
    detect_docx_candidate,
    detect_pdf_candidate,
)


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


_CONTENT_TYPES_XML = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
    '<Default Extension="rels" '
    'ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
    '<Default Extension="xml" ContentType="application/xml"/>'
    '<Override PartName="/word/document.xml" '
    'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
    "</Types>"
)
_RELS_XML = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
    '<Relationship Id="rId1" '
    'Type="http://schemas.openxmlformats.org/officeDocument/2006/'
    'relationships/officeDocument" '
    'Target="word/document.xml"/>'
    "</Relationships>"
)
_DOCUMENT_XML = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
    "<w:body><w:p/></w:body></w:document>"
)


def _build_minimal_docx(
    tmp_path: Path, name: str = "f.docx", **overrides: bytes
) -> Path:
    members: dict[str, bytes | None] = {
        "[Content_Types].xml": _CONTENT_TYPES_XML.encode(),
        "_rels/.rels": _RELS_XML.encode(),
        "word/document.xml": _DOCUMENT_XML.encode(),
    }
    members.update(overrides)
    path = tmp_path / name
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        for member_name, data in members.items():
            if data is not None:
                zf.writestr(member_name, data)
    return path


class TestDetectDocxCandidate:
    def test_valid_minimal_docx_detected(self, tmp_path: Path) -> None:
        path = _build_minimal_docx(tmp_path)
        result = detect_docx_candidate(path, declared_extension=".docx")
        assert result.detected_type == DetectedType.DOCX
        assert result.canonical_mime is not None

    def test_generic_zip_renamed_docx_fails(self, tmp_path: Path) -> None:
        path = tmp_path / "f.docx"
        with zipfile.ZipFile(path, "w") as zf:
            zf.writestr("readme.txt", "just a zip")
        result = detect_docx_candidate(path, declared_extension=".docx")
        assert result.detected_type == DetectedType.UNKNOWN

    def test_missing_content_types_fails(self, tmp_path: Path) -> None:
        path = _build_minimal_docx(tmp_path, **{"[Content_Types].xml": None})
        result = detect_docx_candidate(path, declared_extension=".docx")
        assert result.detected_type == DetectedType.UNKNOWN

    def test_missing_rels_fails(self, tmp_path: Path) -> None:
        path = _build_minimal_docx(tmp_path, **{"_rels/.rels": None})
        result = detect_docx_candidate(path, declared_extension=".docx")
        assert result.detected_type == DetectedType.UNKNOWN

    def test_missing_document_xml_fails(self, tmp_path: Path) -> None:
        path = _build_minimal_docx(tmp_path, **{"word/document.xml": None})
        result = detect_docx_candidate(path, declared_extension=".docx")
        assert result.detected_type == DetectedType.UNKNOWN

    def test_wrong_main_relationship_fails(self, tmp_path: Path) -> None:
        bad_rels = _RELS_XML.replace(
            "officeDocument/2006/relationships/officeDocument",
            "officeDocument/2006/relationships/image",
        )
        path = _build_minimal_docx(tmp_path, **{"_rels/.rels": bad_rels.encode()})
        result = detect_docx_candidate(path, declared_extension=".docx")
        assert result.detected_type == DetectedType.UNKNOWN

    def test_macro_enabled_content_type_fails(self, tmp_path: Path) -> None:
        macro_content_types = _CONTENT_TYPES_XML.replace(
            "wordprocessingml.document.main+xml",
            "wordprocessingml.document.macroEnabled.main+xml",
        )
        path = _build_minimal_docx(
            tmp_path, **{"[Content_Types].xml": macro_content_types.encode()}
        )
        result = detect_docx_candidate(path, declared_extension=".docx")
        assert result.detected_type == DetectedType.UNKNOWN
        assert result.reason_code == "MACRO_ENABLED_PACKAGE"

    def test_duplicate_member_names_fail(self, tmp_path: Path) -> None:
        path = tmp_path / "f.docx"
        with zipfile.ZipFile(path, "w") as zf:
            zf.writestr("[Content_Types].xml", _CONTENT_TYPES_XML)
            zf.writestr("_rels/.rels", _RELS_XML)
            zf.writestr("word/document.xml", _DOCUMENT_XML)
            zf.writestr("word/document.xml", _DOCUMENT_XML)  # duplicate
        result = detect_docx_candidate(path, declared_extension=".docx")
        assert result.detected_type == DetectedType.UNKNOWN

    def test_excessive_preliminary_member_count_fails(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from app.core import config

        monkeypatch.setattr(config.settings, "DOCX_DETECTION_MAX_MEMBERS", 3)
        path = _build_minimal_docx(
            tmp_path,
            **{f"word/extra{i}.xml": b"<x/>" for i in range(5)},
        )
        result = detect_docx_candidate(path, declared_extension=".docx")
        assert result.detected_type == DetectedType.UNKNOWN

    def test_corrupt_zip_fails_safely(self, tmp_path: Path) -> None:
        path = tmp_path / "f.docx"
        path.write_bytes(b"PK\x03\x04not a real zip central directory garbage")
        result = detect_docx_candidate(path, declared_extension=".docx")
        assert result.detected_type == DetectedType.UNKNOWN

    def test_dtd_in_identity_xml_fails(self, tmp_path: Path) -> None:
        """A real XXE payload (external-entity file read) embedded in an
        identity part must be rejected, not merely a bare DOCTYPE."""
        malicious_rels = (
            '<?xml version="1.0"?>'
            '<!DOCTYPE foo [<!ENTITY xxe SYSTEM "file:///etc/passwd">]>' + _RELS_XML
        )
        path = _build_minimal_docx(tmp_path, **{"_rels/.rels": malicious_rels.encode()})
        result = detect_docx_candidate(path, declared_extension=".docx")
        assert result.detected_type == DetectedType.UNKNOWN
        assert result.reason_code == "DOCX_UNSAFE_XML"

    def test_billion_laughs_entity_expansion_fails(self, tmp_path: Path) -> None:
        """Internal entity-expansion (billion-laughs style) DoS payload
        must also be rejected by the DTD/entity-declaration handlers."""
        malicious_rels = (
            '<?xml version="1.0"?>'
            "<!DOCTYPE lolz ["
            '<!ENTITY lol "lol">'
            '<!ENTITY lol2 "&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;">'
            "]>" + _RELS_XML
        )
        path = _build_minimal_docx(tmp_path, **{"_rels/.rels": malicious_rels.encode()})
        result = detect_docx_candidate(path, declared_extension=".docx")
        assert result.detected_type == DetectedType.UNKNOWN

    def test_valid_docx_with_pdf_display_extension_fails(self, tmp_path: Path) -> None:
        path = _build_minimal_docx(tmp_path, name="f.pdf")
        result = detect_docx_candidate(path, declared_extension=".pdf")
        assert result.detected_type == DetectedType.UNKNOWN
        assert result.reason_code == "EXTENSION_TYPE_MISMATCH"

    def test_no_member_is_extracted_to_disk(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import zipfile as zf_module

        def _fail(*a: object, **kw: object) -> None:
            raise AssertionError("extract/extractall must never be called")

        monkeypatch.setattr(zf_module.ZipFile, "extract", _fail)
        monkeypatch.setattr(zf_module.ZipFile, "extractall", _fail)
        path = _build_minimal_docx(tmp_path)
        result = detect_docx_candidate(path, declared_extension=".docx")
        assert result.detected_type == DetectedType.DOCX
