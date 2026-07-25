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
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

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
