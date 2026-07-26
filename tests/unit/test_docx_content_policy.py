import struct
import zipfile
from pathlib import Path

import pytest

from app.services.docx_content_policy import (
    POLICY_VERSION,
    check_docx_content_policy,
)

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
_DOCUMENT_RELS_XML = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
    "</Relationships>"
)
_DOCUMENT_RELS_EXTERNAL_XML = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
    '<Relationship Id="rId1" '
    'Type="http://schemas.openxmlformats.org/officeDocument/2006/'
    'relationships/hyperlink" '
    'Target="http://evil.example/" TargetMode="External"/>'
    "</Relationships>"
)

_OLE_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"


def _build_minimal_docx(
    tmp_path: Path, name: str = "f.docx", extra_members: dict[str, bytes] | None = None
) -> Path:
    members: dict[str, bytes] = {
        "[Content_Types].xml": _CONTENT_TYPES_XML.encode(),
        "_rels/.rels": _RELS_XML.encode(),
        "word/document.xml": _DOCUMENT_XML.encode(),
        "word/_rels/document.xml.rels": _DOCUMENT_RELS_XML.encode(),
    }
    if extra_members:
        members.update(extra_members)
    path = tmp_path / name
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        for member_name, data in members.items():
            zf.writestr(member_name, data)
    return path


def _corrupt_member_compressed_data(path: Path, member_name: str) -> None:
    """Flip bytes in ``member_name``'s DEFLATE-compressed data in place.

    Leaves the ZIP central directory (and therefore the member's declared
    ``file_size``/``compress_size``/CRC metadata) untouched, so the
    corruption is only discoverable by actually decompressing the member --
    exactly the "central directory intact, compressed bytes corrupted"
    scenario that triggers a raw ``zlib.error`` if a caller only guards
    against ``zipfile.BadZipFile``/``OSError``.
    """
    with zipfile.ZipFile(path) as zf:
        info = zf.getinfo(member_name)
    raw = bytearray(path.read_bytes())
    local_header_fixed_size = 30
    name_len = len(member_name.encode())
    extra_len = struct.unpack(
        "<H", raw[info.header_offset + 28 : info.header_offset + 30]
    )[0]
    data_start = info.header_offset + local_header_fixed_size + name_len + extra_len
    data_end = min(data_start + info.compress_size, data_start + 16)
    for i in range(data_start, data_end):
        raw[i] ^= 0xFF
    path.write_bytes(bytes(raw))


class TestCheckDocxContentPolicy:
    def test_clean_minimal_docx_passes(self, tmp_path: Path) -> None:
        path = _build_minimal_docx(tmp_path)
        result = check_docx_content_policy(path)
        assert result.passed is True
        assert result.reason_code is None
        assert result.policy_version == POLICY_VERSION

    def test_macro_content_type_marker_fails(self, tmp_path: Path) -> None:
        macro_content_types = _CONTENT_TYPES_XML.replace(
            "wordprocessingml.document.main+xml",
            "wordprocessingml.document.macroEnabled.main+xml",
        )
        path = _build_minimal_docx(
            tmp_path,
            extra_members={"[Content_Types].xml": macro_content_types.encode()},
        )
        result = check_docx_content_policy(path)
        assert result.passed is False
        assert result.reason_code == "DOCX_MACRO_PRESENT"

    def test_vba_project_member_fails(self, tmp_path: Path) -> None:
        path = _build_minimal_docx(
            tmp_path, extra_members={"word/vbaProject.bin": b"\x00" * 16}
        )
        result = check_docx_content_policy(path)
        assert result.passed is False
        assert result.reason_code == "DOCX_MACRO_PRESENT"

    def test_embedded_ole_object_fails(self, tmp_path: Path) -> None:
        path = _build_minimal_docx(
            tmp_path,
            extra_members={"word/embeddings/oleObject1.bin": _OLE_MAGIC + b"\x00" * 32},
        )
        result = check_docx_content_policy(path)
        assert result.passed is False
        assert result.reason_code == "DOCX_OLE_PRESENT"

    def test_non_ole_embedding_passes(self, tmp_path: Path) -> None:
        path = _build_minimal_docx(
            tmp_path,
            extra_members={
                "word/embeddings/image1.png": b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
            },
        )
        result = check_docx_content_policy(path)
        assert result.passed is True

    def test_corrupted_embedded_member_compressed_data_fails_safely(
        self, tmp_path: Path
    ) -> None:
        """A member whose central-directory entry is intact but whose
        DEFLATE-compressed bytes are corrupted must be rejected with
        DOCX_MALFORMED_PACKAGE, not raise an unhandled zlib.error."""
        member_name = "word/embeddings/oleObject1.bin"
        path = _build_minimal_docx(
            tmp_path,
            extra_members={member_name: _OLE_MAGIC + b"\x00" * 512},
        )
        _corrupt_member_compressed_data(path, member_name)
        result = check_docx_content_policy(path)
        assert result.passed is False
        assert result.reason_code == "DOCX_MALFORMED_PACKAGE"

    def test_corrupted_document_rels_compressed_data_fails_safely(
        self, tmp_path: Path
    ) -> None:
        """Same corruption scenario against the bounded-XML-member read
        path (word/_rels/document.xml.rels) must also fail closed."""
        member_name = "word/_rels/document.xml.rels"
        path = _build_minimal_docx(
            tmp_path,
            extra_members={member_name: _DOCUMENT_RELS_XML.encode() * 20},
        )
        _corrupt_member_compressed_data(path, member_name)
        result = check_docx_content_policy(path)
        assert result.passed is False
        assert result.reason_code == "DOCX_MALFORMED_PACKAGE"

    def test_external_relationship_single_quoted_target_mode_fails(
        self, tmp_path: Path
    ) -> None:
        single_quoted_rels = _DOCUMENT_RELS_EXTERNAL_XML.replace(
            'TargetMode="External"', "TargetMode='External'"
        )
        path = _build_minimal_docx(
            tmp_path,
            extra_members={"word/_rels/document.xml.rels": single_quoted_rels.encode()},
        )
        result = check_docx_content_policy(path)
        assert result.passed is False
        assert result.reason_code == "DOCX_EXTERNAL_RELATIONSHIP"

    def test_external_relationship_fails(self, tmp_path: Path) -> None:
        path = _build_minimal_docx(
            tmp_path,
            extra_members={
                "word/_rels/document.xml.rels": _DOCUMENT_RELS_EXTERNAL_XML.encode()
            },
        )
        result = check_docx_content_policy(path)
        assert result.passed is False
        assert result.reason_code == "DOCX_EXTERNAL_RELATIONSHIP"

    def test_path_traversal_member_name_fails(self, tmp_path: Path) -> None:
        path = _build_minimal_docx(
            tmp_path, extra_members={"word/../../../etc/passwd": b"x"}
        )
        result = check_docx_content_policy(path)
        assert result.passed is False
        assert result.reason_code == "DOCX_MALFORMED_PACKAGE"

    def test_absolute_unix_path_member_name_fails(self, tmp_path: Path) -> None:
        path = _build_minimal_docx(tmp_path, extra_members={"/etc/passwd": b"x"})
        result = check_docx_content_policy(path)
        assert result.passed is False
        assert result.reason_code == "DOCX_MALFORMED_PACKAGE"

    def test_windows_drive_letter_member_name_fails(self, tmp_path: Path) -> None:
        path = _build_minimal_docx(
            tmp_path, extra_members={"C:\\Windows\\System32\\evil.dll": b"x"}
        )
        result = check_docx_content_policy(path)
        assert result.passed is False
        assert result.reason_code == "DOCX_MALFORMED_PACKAGE"

    def test_duplicate_member_names_fail(self, tmp_path: Path) -> None:
        path = tmp_path / "f.docx"
        with zipfile.ZipFile(path, "w") as zf:
            zf.writestr("[Content_Types].xml", _CONTENT_TYPES_XML)
            zf.writestr("_rels/.rels", _RELS_XML)
            zf.writestr("word/document.xml", _DOCUMENT_XML)
            zf.writestr("word/document.xml", _DOCUMENT_XML)  # duplicate
        result = check_docx_content_policy(path)
        assert result.passed is False
        assert result.reason_code == "DOCX_MALFORMED_PACKAGE"

    def test_excessive_member_count_fails(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from app.core import config

        monkeypatch.setattr(config.settings, "DOCX_DETECTION_MAX_MEMBERS", 3)
        path = _build_minimal_docx(
            tmp_path,
            extra_members={f"word/extra{i}.xml": b"<x/>" for i in range(5)},
        )
        result = check_docx_content_policy(path)
        assert result.passed is False
        assert result.reason_code == "DOCX_ARCHIVE_LIMIT"

    def test_excessive_declared_uncompressed_size_fails(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from app.core import config

        monkeypatch.setattr(config.settings, "DOCX_MAX_UNCOMPRESSED_TOTAL_BYTES", 100)
        path = _build_minimal_docx(
            tmp_path, extra_members={"word/big.xml": b"x" * 1000}
        )
        result = check_docx_content_policy(path)
        assert result.passed is False
        assert result.reason_code == "DOCX_ARCHIVE_LIMIT"

    def test_extreme_compression_ratio_fails(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from app.core import config

        # Raise the total-bytes cap so only the ratio check can trigger,
        # then force a low ratio cap against genuinely-compressible data.
        monkeypatch.setattr(
            config.settings, "DOCX_MAX_UNCOMPRESSED_TOTAL_BYTES", 100 * 1024 * 1024
        )
        monkeypatch.setattr(config.settings, "DOCX_MAX_COMPRESSION_RATIO", 5)
        highly_compressible = b"A" * 1_000_000
        path = _build_minimal_docx(
            tmp_path, extra_members={"word/bomb.xml": highly_compressible}
        )
        result = check_docx_content_policy(path)
        assert result.passed is False
        assert result.reason_code == "DOCX_ARCHIVE_LIMIT"

    def test_encrypted_entry_flag_bit_fails(self, tmp_path: Path) -> None:
        path = _build_minimal_docx(tmp_path)
        with zipfile.ZipFile(path, "a") as zf:
            zf.writestr("word/secret.xml", b"<x/>")
            info = zf.infolist()[-1]
            info.flag_bits |= 0x1

        result = check_docx_content_policy(path)
        assert result.passed is False
        assert result.reason_code == "DOCX_MALFORMED_PACKAGE"

    def test_corrupt_central_directory_fails_safely(self, tmp_path: Path) -> None:
        path = tmp_path / "f.docx"
        path.write_bytes(b"PK\x03\x04not a real zip central directory garbage")
        result = check_docx_content_policy(path)
        assert result.passed is False
        assert result.reason_code == "DOCX_MALFORMED_PACKAGE"

    def test_nested_zip_member_fails(self, tmp_path: Path) -> None:
        nested_zip_bytes = b"PK\x05\x06" + b"\x00" * 18
        path = _build_minimal_docx(
            tmp_path, extra_members={"word/embedded_package.zip": nested_zip_bytes}
        )
        result = check_docx_content_policy(path)
        assert result.passed is False
        assert result.reason_code == "DOCX_MALFORMED_PACKAGE"

    def test_dtd_in_document_rels_fails(self, tmp_path: Path) -> None:
        malicious_rels = (
            '<?xml version="1.0"?>'
            '<!DOCTYPE foo [<!ENTITY xxe SYSTEM "file:///etc/passwd">]>'
            + _DOCUMENT_RELS_XML
        )
        path = _build_minimal_docx(
            tmp_path,
            extra_members={"word/_rels/document.xml.rels": malicious_rels.encode()},
        )
        result = check_docx_content_policy(path)
        assert result.passed is False
        assert result.reason_code == "DOCX_MALFORMED_PACKAGE"

    def test_nonexistent_file_fails_safely(self, tmp_path: Path) -> None:
        result = check_docx_content_policy(tmp_path / "does-not-exist.docx")
        assert result.passed is False
        assert result.reason_code == "DOCX_MALFORMED_PACKAGE"

    def test_no_member_is_extracted_to_disk(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import zipfile as zf_module

        def _fail(*a: object, **kw: object) -> None:
            raise AssertionError("extract/extractall must never be called")

        monkeypatch.setattr(zf_module.ZipFile, "extract", _fail)
        monkeypatch.setattr(zf_module.ZipFile, "extractall", _fail)
        path = _build_minimal_docx(tmp_path)
        result = check_docx_content_policy(path)
        assert result.passed is True
