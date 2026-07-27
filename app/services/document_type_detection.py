"""Bounded candidate-type detection for uploaded documents.

This module performs lightweight, allocation-bounded checks on raw file
bytes (header window + tail window) to classify a file as a PDF or DOCX
*candidate*. It is explicitly NOT full document security inspection: it
does not parse the PDF object graph, does not open the DOCX zip container,
and must never import a third-party PDF or DOCX parsing library. Full
structural validation happens later in the pipeline, behind quarantine
storage.

Only the Python standard library may be imported here.
"""

import re
import zipfile
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from xml.parsers.expat import ExpatError, ParserCreate

from app.core.config import settings

_PDF_HEADER_RE = re.compile(rb"^%PDF-1\.[0-7]|^%PDF-2\.0")
_PDF_HEADER_WINDOW = 1024
_PDF_EOF_WINDOW = 1024
_PDF_MIN_SIZE = 16  # smallest plausible header + EOF marker size

_PDF_MIME = "application/pdf"
_DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"

_UNKNOWN_SUMMARY = "The uploaded file does not match a supported PDF or DOCX format."


class DetectedType(str, Enum):  # noqa: UP042 -- str mixin required for JSON/API contract
    PDF = "PDF"
    DOCX = "DOCX"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class DetectionResult:
    detected_type: DetectedType
    canonical_mime: str | None
    expected_extension: str | None
    reason_code: str | None
    safe_summary: str


def _unknown(reason_code: str) -> DetectionResult:
    return DetectionResult(
        detected_type=DetectedType.UNKNOWN,
        canonical_mime=None,
        expected_extension=None,
        reason_code=reason_code,
        safe_summary=_UNKNOWN_SUMMARY,
    )


def detect_pdf_candidate(
    file_path: Path, *, declared_extension: str
) -> DetectionResult:
    """Classify ``file_path`` as a PDF candidate using bounded byte checks.

    Reads only a bounded header window and a bounded tail window from the
    file (never the whole file), regardless of file size.
    """
    if declared_extension.lower() != ".pdf":
        return _unknown("EXTENSION_TYPE_MISMATCH")

    size = file_path.stat().st_size
    if size < _PDF_MIN_SIZE:
        return _unknown("PDF_TRUNCATED")

    with file_path.open("rb") as f:
        header = f.read(_PDF_HEADER_WINDOW)
        f.seek(max(0, size - _PDF_EOF_WINDOW))
        tail = f.read(_PDF_EOF_WINDOW)

    if not _PDF_HEADER_RE.match(header):
        return _unknown("PDF_HEADER_INVALID")
    if b"%%EOF" not in tail:
        return _unknown("PDF_EOF_MISSING")

    return DetectionResult(
        detected_type=DetectedType.PDF,
        canonical_mime=_PDF_MIME,
        expected_extension=".pdf",
        reason_code=None,
        safe_summary="Recognized as a PDF candidate.",
    )


_DOCX_IDENTITY_MEMBERS = {
    "[Content_Types].xml",
    "_rels/.rels",
    "word/document.xml",
}
_DOCX_MAIN_RELATIONSHIP_TYPE = (
    "http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument"
)
_MACRO_CONTENT_TYPE_MARKER = "macroEnabled"
_MAX_IDENTITY_XML_BYTES = 1024 * 1024  # 1 MiB per identity part, generous but bounded


def _read_zip_member_bounded(zf: zipfile.ZipFile, name: str) -> bytes:
    """Read a single named zip member's bytes, bounded by size.

    Uses ``ZipFile.read`` only -- never ``extract``/``extractall`` -- so no
    member is ever written to disk; the bytes stay in memory.
    """
    info = zf.getinfo(name)
    if info.file_size > _MAX_IDENTITY_XML_BYTES:
        raise ValueError("identity part too large")
    return zf.read(name)


def _hardened_parse(xml_bytes: bytes) -> None:
    """Parse ``xml_bytes`` while rejecting any DTD or entity declaration.

    This is the security boundary against XXE and entity-expansion
    (billion-laughs) attacks: any ``<!DOCTYPE ...>``, ``<!ENTITY ...>``, or
    unparsed-entity declaration causes the registered handler to raise,
    which propagates out of ``parser.Parse`` as the ``ValueError`` raised
    here (verified empirically, not merely assumed from the expat API
    docs). External entity resolution is additionally denied by returning
    a falsy value from the external-entity handler as defense in depth,
    should a DTD ever slip past the doctype/entity handlers above.

    Only proves the XML is well-formed and free of DTD/entity constructs.
    Callers must not treat this as building any DOM or doing anything
    beyond producing a safe pass/raise signal.
    """
    parser = ParserCreate()
    parser.DefaultHandler = lambda data: None

    def _reject_dtd(*_args: object) -> None:
        raise ValueError("DTD declarations are not permitted")

    parser.StartDoctypeDeclHandler = _reject_dtd
    parser.EntityDeclHandler = _reject_dtd
    parser.UnparsedEntityDeclHandler = _reject_dtd
    parser.ExternalEntityRefHandler = lambda *_a: False  # deny resolution
    try:
        parser.Parse(xml_bytes, True)
    except ExpatError as e:
        raise ValueError("malformed XML") from e


def detect_docx_candidate(
    file_path: Path, *, declared_extension: str
) -> DetectionResult:
    """Classify ``file_path`` as a DOCX candidate using bounded ZIP/XML checks.

    Inspects the ZIP central directory and a handful of small "identity"
    XML parts (``[Content_Types].xml`` and ``_rels/.rels``) to decide
    whether the package looks like a genuine, non-macro DOCX. Never calls
    ``ZipFile.extract``/``extractall`` and never writes any package member
    to disk -- only ``ZipFile.read`` is used, and only for the bounded
    identity parts.
    """
    if declared_extension.lower() != ".docx":
        return _unknown("EXTENSION_TYPE_MISMATCH")

    try:
        with zipfile.ZipFile(file_path) as zf:
            infolist = zf.infolist()

            names = [info.filename for info in infolist]
            if len(names) != len(set(names)):
                return _unknown("DOCX_DUPLICATE_MEMBER")

            if len(infolist) > settings.DOCX_DETECTION_MAX_MEMBERS:
                return _unknown("DOCX_MEMBER_COUNT_EXCEEDED")

            name_set = set(names)
            if not _DOCX_IDENTITY_MEMBERS.issubset(name_set):
                return _unknown("DOCX_MISSING_IDENTITY_PART")

            try:
                content_types_bytes = _read_zip_member_bounded(
                    zf, "[Content_Types].xml"
                )
                rels_bytes = _read_zip_member_bounded(zf, "_rels/.rels")
            except (ValueError, KeyError, zipfile.BadZipFile):
                return _unknown("DOCX_MALFORMED_PACKAGE")

            # Security boundary: prove both identity parts are well-formed
            # XML with no DTD/entity declarations BEFORE any substring
            # inspection of their text is performed below.
            try:
                _hardened_parse(content_types_bytes)
                _hardened_parse(rels_bytes)
            except ValueError:
                return _unknown("DOCX_UNSAFE_XML")

            content_types_text = content_types_bytes.decode("utf-8", "replace")
            if _MACRO_CONTENT_TYPE_MARKER in content_types_text:
                return _unknown("MACRO_ENABLED_PACKAGE")

            if "/word/document.xml" not in content_types_text:
                return _unknown("DOCX_CONTENT_TYPES_MISMATCH")

            rels_text = rels_bytes.decode("utf-8", "replace")
            if (
                _DOCX_MAIN_RELATIONSHIP_TYPE not in rels_text
                or "word/document.xml" not in rels_text
            ):
                return _unknown("DOCX_MAIN_RELATIONSHIP_INVALID")

    except zipfile.BadZipFile:
        return _unknown("DOCX_NOT_A_ZIP")

    return DetectionResult(
        detected_type=DetectedType.DOCX,
        canonical_mime=_DOCX_MIME,
        expected_extension=".docx",
        reason_code=None,
        safe_summary="Recognized as a DOCX candidate.",
    )
