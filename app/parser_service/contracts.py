"""Parser service API contracts and Pydantic schemas."""

from typing import Literal

from pydantic import BaseModel, Field


class ParserUnit(BaseModel):
    sequence: int = Field(..., description="1-based sequence index")
    unit_kind: Literal["PDF_PAGE", "DOCX_LOGICAL_CHUNK"] = Field(
        ..., description="Provenance unit kind"
    )
    source_locator: str = Field(
        ..., description="Source locator string (e.g. 'page_1' or 'chunk_1')"
    )
    content: str = Field(..., description="Normalized plain text content")
    content_sha256: str = Field(
        ..., description="SHA-256 digest of normalized unit content"
    )


class ParserResponse(BaseModel):
    protocol_version: str = "1.0"
    parser_name: str = "rfp-isolated-parser"
    parser_version: str = "1.0.0"
    document_type: Literal["PDF", "DOCX"] = Field(...)
    units: list[ParserUnit] = Field(default_factory=list)
    total_units: int = Field(..., ge=0)
    total_characters: int = Field(..., ge=0)


class ParserErrorResponse(BaseModel):
    error_code: str = Field(..., description="Fixed safe error code")
    message: str = Field(..., description="Safe error message")
    request_id: str | None = None
