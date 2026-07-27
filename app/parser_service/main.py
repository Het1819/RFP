"""Isolated Document Parser FastAPI service."""

import asyncio
import hashlib
import tempfile
import uuid
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, File, Form, Header, UploadFile, status
from fastapi.responses import JSONResponse

from app.parser_service.config import (
    DOCX_MIME,
    MAX_FILE_SIZE_BYTES,
    MAX_PARSE_TIME_SECONDS,
    PARSER_NAME,
    PARSER_VERSION,
    PDF_MIME,
    PROTOCOL_VERSION,
)
from app.parser_service.contracts import (
    ParserErrorResponse,
    ParserResponse,
)
from app.parser_service.docx_extractor import (
    DOCXExtractorError,
    extract_docx_units,
)
from app.parser_service.pdf_extractor import (
    PDFExtractorError,
    extract_pdf_units,
)

app = FastAPI(
    title="RFP Isolated Parser Service",
    description="Resource-bounded isolated parser service.",
    version=PARSER_VERSION,
    docs_url=None,  # No interactive swagger docs in production isolation
    redoc_url=None,
)


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    """Basic health check endpoint without document processing capability."""
    return {
        "status": "ok",
        "service": PARSER_NAME,
        "version": PARSER_VERSION,
    }


def _error_response(
    status_code: int, error_code: str, message: str, request_id: str | None = None
) -> JSONResponse:
    err_body = ParserErrorResponse(
        error_code=error_code,
        message=message,
        request_id=request_id,
    )
    return JSONResponse(status_code=status_code, content=err_body.model_dump())


@app.post(
    "/parse",
    response_model=ParserResponse,
    responses={
        400: {"model": ParserErrorResponse},
        413: {"model": ParserErrorResponse},
        422: {"model": ParserErrorResponse},
        500: {"model": ParserErrorResponse},
    },
)
async def parse_document(
    file: UploadFile = File(...),
    content_type: str | None = Form(None),
    expected_sha256: str | None = Form(None),
    expected_size_bytes: int | None = Form(None),
    protocol_version: str | None = Form(None),
    request_id: str | None = Form(None),
    x_content_type: str | None = Header(None, alias="X-Content-Type"),
    x_expected_sha256: str | None = Header(None, alias="X-Expected-SHA256"),
    x_expected_size_bytes: int | None = Header(None, alias="X-Expected-Size-Bytes"),
    x_protocol_version: str | None = Header(None, alias="X-Protocol-Version"),
    x_request_id: str | None = Header(None, alias="X-Request-ID"),
) -> ParserResponse | JSONResponse:
    """Parse a bounded stream of clean PDF or DOCX bytes."""
    # Resolve parameters from form body or HTTP headers
    effective_content_type = content_type or x_content_type or file.content_type or ""
    effective_sha256 = (expected_sha256 or x_expected_sha256 or "").lower().strip()
    effective_size = (
        expected_size_bytes
        if expected_size_bytes is not None
        else x_expected_size_bytes
    )
    req_id = request_id or x_request_id or str(uuid.uuid4())
    prot_ver = protocol_version or x_protocol_version or PROTOCOL_VERSION

    # 1. Validate content type
    if effective_content_type not in (PDF_MIME, DOCX_MIME):
        return _error_response(
            status.HTTP_400_BAD_REQUEST,
            "UNSUPPORTED_CONTENT_TYPE",
            f"Unsupported content type '{effective_content_type}'",
            request_id=req_id,
        )

    # 2. Validate mandatory parameters
    if not effective_sha256 or len(effective_sha256) != 64:
        return _error_response(
            status.HTTP_400_BAD_REQUEST,
            "INVALID_SHA256_PARAMETER",
            "A valid 64-character hex expected_sha256 is required",
            request_id=req_id,
        )

    if effective_size is None or effective_size <= 0:
        return _error_response(
            status.HTTP_400_BAD_REQUEST,
            "INVALID_SIZE_PARAMETER",
            "A positive expected_size_bytes is required",
            request_id=req_id,
        )

    if effective_size > MAX_FILE_SIZE_BYTES:
        return _error_response(
            status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            "FILE_TOO_LARGE",
            f"Expected file size ({effective_size}) exceeds limit",
            request_id=req_id,
        )

    # 3. Stream into bounded temporary file in /tmp
    tmp_dir = tempfile.gettempdir()
    tmp_path = Path(tmp_dir) / f"parse_{uuid.uuid4().hex}.tmp"

    hasher = hashlib.sha256()
    received_bytes = 0

    try:
        with open(tmp_path, "wb") as f_out:
            while True:
                chunk = await file.read(64 * 1024)
                if not chunk:
                    break
                received_bytes += len(chunk)
                if received_bytes > MAX_FILE_SIZE_BYTES:
                    return _error_response(
                        status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                        "FILE_TOO_LARGE",
                        f"Streamed bytes ({received_bytes}) exceeded limit",
                        request_id=req_id,
                    )
                hasher.update(chunk)
                f_out.write(chunk)

        # 4. Verify size and digest match expected values
        if received_bytes != effective_size:
            return _error_response(
                status.HTTP_400_BAD_REQUEST,
                "SIZE_MISMATCH",
                f"Streamed bytes ({received_bytes}) != expected ({effective_size})",
                request_id=req_id,
            )

        calculated_sha256 = hasher.hexdigest().lower()
        if calculated_sha256 != effective_sha256:
            return _error_response(
                status.HTTP_400_BAD_REQUEST,
                "DIGEST_MISMATCH",
                "Streamed SHA-256 digest does not match expected_sha256",
                request_id=req_id,
            )

        # 5. Extract plain-text units with timeout
        doc_type_label: Literal["PDF", "DOCX"] = (
            "PDF" if effective_content_type == PDF_MIME else "DOCX"
        )

        try:
            if effective_content_type == PDF_MIME:
                units, total_units, total_chars = await asyncio.wait_for(
                    asyncio.to_thread(extract_pdf_units, tmp_path),
                    timeout=MAX_PARSE_TIME_SECONDS,
                )
            else:
                units, total_units, total_chars = await asyncio.wait_for(
                    asyncio.to_thread(extract_docx_units, tmp_path),
                    timeout=MAX_PARSE_TIME_SECONDS,
                )
        except TimeoutError:
            return _error_response(
                status.HTTP_408_REQUEST_TIMEOUT,
                "PARSE_TIMEOUT",
                f"Document parsing timed out after {MAX_PARSE_TIME_SECONDS}s",
                request_id=req_id,
            )
        except PDFExtractorError as err:
            return _error_response(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                err.code,
                err.message,
                request_id=req_id,
            )
        except DOCXExtractorError as err:
            return _error_response(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                err.code,
                err.message,
                request_id=req_id,
            )
        except Exception as err:
            return _error_response(
                status.HTTP_500_INTERNAL_SERVER_ERROR,
                "PARSER_INTERNAL_ERROR",
                f"Parsing failed: {type(err).__name__}",
                request_id=req_id,
            )

        return ParserResponse(
            protocol_version=prot_ver,
            parser_name=PARSER_NAME,
            parser_version=PARSER_VERSION,
            document_type=doc_type_label,
            units=units,
            total_units=total_units,
            total_characters=total_chars,
        )

    finally:
        # Guaranteed cleanup of temporary input file
        if tmp_path.exists():
            try:
                tmp_path.unlink()
            except Exception:
                pass
