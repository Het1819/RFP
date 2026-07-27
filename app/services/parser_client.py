"""Async client for communicating with the isolated document parser service."""

import hashlib
import os
import stat
from pathlib import Path
from typing import IO

import httpx
from pydantic import ValidationError

from app.core.config import settings
from app.parser_service.config import (
    MAX_TOTAL_CHARS,
    MAX_UNITS,
    PARSER_NAME,
    PROTOCOL_VERSION,
)
from app.parser_service.contracts import ParserErrorResponse, ParserResponse


class ParserClientError(Exception):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(message)


def _resolve_and_verify_clean_file(
    clean_identifier: str, expected_sha256: str, expected_size: int
) -> tuple[IO[bytes], int]:
    """Safely verify clean file path containment, symlinks, size, and digest.
    Returns (file_object, size) bound to the exact O_NOFOLLOW file descriptor.
    """
    clean_root = Path(settings.LOCAL_STORAGE_PATH).resolve()
    target_path = (clean_root / clean_identifier).resolve()

    # Path containment check
    try:
        target_path.relative_to(clean_root)
    except ValueError as err:
        raise ParserClientError(
            "PARSER_INPUT_CHANGED",
            f"Path containment violation for clean file: {clean_identifier}",
        ) from err

    if not target_path.exists():
        raise ParserClientError(
            "PARSER_INPUT_CHANGED",
            f"Clean source file missing at {clean_identifier}",
        )

    # Use O_NOFOLLOW to reject symlinks
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW

    try:
        fd = os.open(target_path, flags)
    except OSError as err:
        raise ParserClientError(
            "PARSER_INPUT_CHANGED",
            f"Failed to open clean file (symlink or permission error): {err}",
        ) from err

    try:
        st_initial = os.fstat(fd)
        if not stat.S_ISREG(st_initial.st_mode):
            raise ParserClientError(
                "PARSER_INPUT_CHANGED", "Clean source is not a regular file"
            )

        if st_initial.st_size != expected_size:
            msg = (
                f"Clean file size on disk ({st_initial.st_size}) "
                f"!= expected ({expected_size})"
            )
            raise ParserClientError("PARSER_INPUT_CHANGED", msg)

        hasher = hashlib.sha256()
        while True:
            chunk = os.read(fd, 64 * 1024)
            if not chunk:
                break
            hasher.update(chunk)

        calculated_sha256 = hasher.hexdigest().lower()
        if calculated_sha256 != expected_sha256.lower():
            raise ParserClientError(
                "PARSER_INPUT_CHANGED",
                "Clean file SHA-256 digest on disk differs from expected digest",
            )

        # Rewind file descriptor
        os.lseek(fd, 0, os.SEEK_SET)

        # Verify descriptor inode, device, and size after read
        st_final = os.fstat(fd)
        if (
            st_final.st_size != st_initial.st_size
            or st_final.st_ino != st_initial.st_ino
            or st_final.st_dev != st_initial.st_dev
        ):
            raise ParserClientError(
                "PARSER_INPUT_CHANGED",
                "Clean file descriptor state changed during verification",
            )

        file_obj = os.fdopen(fd, "rb", closefd=True)
        return file_obj, st_final.st_size

    except Exception:
        try:
            os.close(fd)
        except Exception:
            pass
        raise


def validate_parser_response(resp_data: dict[str, object]) -> ParserResponse:
    """Validate raw parser response dictionary against strict invariants."""
    try:
        response = ParserResponse.model_validate(resp_data)
    except ValidationError as err:
        raise ParserClientError(
            "PARSER_RESPONSE_INVALID", f"Invalid response schema: {err}"
        ) from err

    if response.protocol_version != PROTOCOL_VERSION:
        raise ParserClientError(
            "PARSER_PROTOCOL_ERROR",
            f"Unsupported parser protocol version '{response.protocol_version}'",
        )

    if response.parser_name != PARSER_NAME:
        raise ParserClientError(
            "PARSER_PROTOCOL_ERROR",
            f"Unexpected parser service name '{response.parser_name}'",
        )

    if response.total_units > MAX_UNITS or len(response.units) > MAX_UNITS:
        msg = (
            f"Parser returned {response.total_units} units "
            f"exceeding limit of {MAX_UNITS}"
        )
        raise ParserClientError("PARSER_OUTPUT_LIMIT", msg)

    if response.total_characters > MAX_TOTAL_CHARS:
        msg = (
            f"Parser returned {response.total_characters} chars "
            f"exceeding limit of {MAX_TOTAL_CHARS}"
        )
        raise ParserClientError("PARSER_OUTPUT_LIMIT", msg)

    # Validate unit sequence numbers and unique locators
    seen_locators: set[str] = set()
    expected_seq = 1

    for unit in response.units:
        if unit.sequence != expected_seq:
            msg = (
                f"Non-sequential unit sequence {unit.sequence}, expected {expected_seq}"
            )
            raise ParserClientError("PARSER_RESPONSE_INVALID", msg)
        expected_seq += 1

        if unit.source_locator in seen_locators:
            raise ParserClientError(
                "PARSER_RESPONSE_INVALID",
                f"Duplicate unit source_locator '{unit.source_locator}'",
            )
        seen_locators.add(unit.source_locator)

    return response


async def send_document_for_parsing(
    clean_identifier: str,
    detected_mime: str,
    expected_sha256: str,
    expected_size: int,
    request_id: str,
) -> ParserResponse:
    """Stream clean document to isolated parser service and return
    validated ParserResponse.
    """
    file_obj, _size = _resolve_and_verify_clean_file(
        clean_identifier, expected_sha256, expected_size
    )

    parser_url = getattr(settings, "PARSER_SERVICE_URL", "http://parser:8000")
    endpoint = f"{parser_url.rstrip('/')}/parse"

    headers = {
        "X-Content-Type": detected_mime,
        "X-Expected-SHA256": expected_sha256,
        "X-Expected-Size-Bytes": str(expected_size),
        "X-Protocol-Version": PROTOCOL_VERSION,
        "X-Request-ID": request_id,
    }

    timeouts = httpx.Timeout(connect=5.0, read=30.0, write=10.0, pool=5.0)

    try:
        async with httpx.AsyncClient(
            trust_env=False, follow_redirects=False, timeout=timeouts
        ) as client:
            files = {"file": (clean_identifier, file_obj, detected_mime)}
            data = {
                "content_type": detected_mime,
                "expected_sha256": expected_sha256,
                "expected_size_bytes": str(expected_size),
                "protocol_version": PROTOCOL_VERSION,
                "request_id": request_id,
            }

            res = await client.post(endpoint, headers=headers, data=data, files=files)

    except httpx.ConnectError as err:
        raise ParserClientError(
            "PARSER_UNAVAILABLE", f"Cannot connect to parser service: {err}"
        ) from err
    except httpx.TimeoutException as err:
        raise ParserClientError(
            "PARSER_TIMEOUT", f"Parser service HTTP request timed out: {err}"
        ) from err
    except Exception as err:
        raise ParserClientError(
            "PARSER_PROTOCOL_ERROR", f"HTTP communication error: {err}"
        ) from err
    finally:
        try:
            file_obj.close()
        except Exception:
            pass

    if res.status_code != 200:
        try:
            err_data = res.json()
            err_resp = ParserErrorResponse.model_validate(err_data)
            code = err_resp.error_code or "PARSER_DOCUMENT_REJECTED"
            msg = err_resp.message or "Parser rejected document"
        except Exception:
            code = "PARSER_DOCUMENT_REJECTED"
            msg = f"Parser returned HTTP status {res.status_code}"
        raise ParserClientError(code, msg)

    # Bound response body size (max 10MB) before JSON parsing
    if len(res.content) > 10 * 1024 * 1024:
        raise ParserClientError(
            "PARSER_RESPONSE_TOO_LARGE",
            "Parser JSON response body exceeds limit of 10MB",
        )

    try:
        resp_dict = res.json()
    except Exception as err:
        raise ParserClientError(
            "PARSER_RESPONSE_INVALID", f"Failed to parse JSON response: {err}"
        ) from err

    return validate_parser_response(resp_dict)
