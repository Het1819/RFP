"""Resource-bounded PDF plain-text extractor using PyMuPDF."""

from pathlib import Path

import fitz

from app.parser_service.config import (
    MAX_CHARS_PER_UNIT,
    MAX_TOTAL_CHARS,
    MAX_UNITS,
)
from app.parser_service.contracts import ParserUnit
from app.parser_service.normalizer import compute_sha256, normalize_text


class PDFExtractorError(Exception):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(message)


def extract_pdf_units(file_path: Path) -> tuple[list[ParserUnit], int, int]:
    """Extract page units from a PDF document.

    Each physical page maps to exactly one ParserUnit:
      - sequence: 1-based page index
      - unit_kind: "PDF_PAGE"
      - source_locator: "page_{sequence}"
    """
    try:
        doc = fitz.open(str(file_path))
    except Exception as err:
        raise PDFExtractorError("PDF_CORRUPT", f"Failed to open PDF: {err}") from err

    try:
        if doc.is_encrypted:
            raise PDFExtractorError("PDF_ENCRYPTED", "Encrypted PDF is not supported")

        page_count = doc.page_count
        if page_count > MAX_UNITS:
            raise PDFExtractorError(
                "PAGE_LIMIT_EXCEEDED",
                f"PDF page count ({page_count}) exceeds limit of {MAX_UNITS}",
            )

        units: list[ParserUnit] = []
        total_chars = 0

        for page_idx in range(page_count):
            page_num = page_idx + 1
            page = doc.load_page(page_idx)
            raw_text = page.get_text("text", sort=True)
            norm_text = normalize_text(raw_text)

            if len(norm_text) > MAX_CHARS_PER_UNIT:
                norm_text = norm_text[:MAX_CHARS_PER_UNIT]

            total_chars += len(norm_text)
            if total_chars > MAX_TOTAL_CHARS:
                raise PDFExtractorError(
                    "OUTPUT_LIMIT_EXCEEDED",
                    f"Total output characters ({total_chars}) exceeds limit",
                )

            unit = ParserUnit(
                sequence=page_num,
                unit_kind="PDF_PAGE",
                source_locator=f"page_{page_num}",
                content=norm_text,
                content_sha256=compute_sha256(norm_text),
            )
            units.append(unit)

        return units, len(units), total_chars
    finally:
        doc.close()
