"""Resource-bounded DOCX logical-chunk extractor using python-docx."""

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import docx
from docx.oxml.table import CT_Tbl
from docx.oxml.text.paragraph import CT_P
from docx.table import Table
from docx.text.paragraph import Paragraph

from app.parser_service.config import (
    CHUNK_TARGET_CHARS,
    MAX_CHARS_PER_UNIT,
    MAX_TOTAL_CHARS,
    MAX_UNITS,
)
from app.parser_service.contracts import ParserUnit
from app.parser_service.normalizer import compute_sha256, normalize_text


class DOCXExtractorError(Exception):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(message)


def _iter_block_items(doc: Any) -> Iterator[Any]:
    """Iterate paragraphs and tables in document body order."""
    body = doc.element.body
    for child in body.iterchildren():
        if isinstance(child, CT_P):
            yield Paragraph(child, doc)
        elif isinstance(child, CT_Tbl):
            yield Table(child, doc)


def _extract_table_text(table: Table) -> str:
    """Extract table cell text in deterministic row/column order."""
    rows_text: list[str] = []
    for row in table.rows:
        cell_texts = [cell.text.strip() for cell in row.cells]
        row_str = " | ".join(filter(None, cell_texts))
        if row_str:
            rows_text.append(row_str)
    return "\n".join(rows_text)


def extract_docx_units(file_path: Path) -> tuple[list[ParserUnit], int, int]:
    """Extract logical chunk units from a DOCX document.

    Each logical chunk maps to one ParserUnit:
      - sequence: 1-based chunk index
      - unit_kind: "DOCX_LOGICAL_CHUNK"
      - source_locator: "chunk_{sequence}"
    """
    try:
        doc = docx.Document(str(file_path))
    except Exception as err:
        raise DOCXExtractorError(
            "DOCX_CORRUPT", f"Failed to open DOCX document: {err}"
        ) from err

    blocks: list[str] = []
    for block in _iter_block_items(doc):
        if isinstance(block, Paragraph):
            text = block.text.strip()
            if text:
                blocks.append(text)
        elif isinstance(block, Table):
            tbl_text = _extract_table_text(block)
            if tbl_text:
                blocks.append(tbl_text)

    if not blocks:
        # Empty document still produces 1 empty chunk unit
        empty_unit = ParserUnit(
            sequence=1,
            unit_kind="DOCX_LOGICAL_CHUNK",
            source_locator="chunk_1",
            content="",
            content_sha256=compute_sha256(""),
        )
        return [empty_unit], 1, 0

    # Group block strings into chunks of approximately CHUNK_TARGET_CHARS
    chunks: list[str] = []
    current_chunk: list[str] = []
    current_len = 0

    for block_str in blocks:
        if current_len > 0 and (current_len + len(block_str) > CHUNK_TARGET_CHARS):
            chunks.append("\n\n".join(current_chunk))
            current_chunk = [block_str]
            current_len = len(block_str)
        else:
            current_chunk.append(block_str)
            current_len += len(block_str)

    if current_chunk:
        chunks.append("\n\n".join(current_chunk))

    if len(chunks) > MAX_UNITS:
        raise DOCXExtractorError(
            "CHUNK_LIMIT_EXCEEDED",
            f"DOCX chunk count ({len(chunks)}) exceeds limit of {MAX_UNITS}",
        )

    units: list[ParserUnit] = []
    total_chars = 0

    for idx, raw_chunk in enumerate(chunks):
        chunk_num = idx + 1
        norm_chunk = normalize_text(raw_chunk)

        if len(norm_chunk) > MAX_CHARS_PER_UNIT:
            norm_chunk = norm_chunk[:MAX_CHARS_PER_UNIT]

        total_chars += len(norm_chunk)
        if total_chars > MAX_TOTAL_CHARS:
            raise DOCXExtractorError(
                "OUTPUT_LIMIT_EXCEEDED",
                f"Total output characters ({total_chars}) exceeds limit",
            )

        unit = ParserUnit(
            sequence=chunk_num,
            unit_kind="DOCX_LOGICAL_CHUNK",
            source_locator=f"chunk_{chunk_num}",
            content=norm_chunk,
            content_sha256=compute_sha256(norm_chunk),
        )
        units.append(unit)

    return units, len(units), total_chars
