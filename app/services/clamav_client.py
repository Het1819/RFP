"""ClamAV INSTREAM protocol client.

Security-critical module: this is a standalone protocol client only -- no
scanning orchestration is wired up here (that lands in a later A5c task).
It streams a quarantined file's bytes to a `clamd` daemon over TCP using
the INSTREAM protocol and returns a typed, fixed-vocabulary `ScanResult`.

Design invariants:
- Never let a raw exception (or a traceback containing a filesystem path)
  escape this module. Every socket/file operation is wrapped and mapped
  to a typed `ScanOutcome`; only the fixed outcome string is ever logged.
- `clamd` receives no application secrets -- this client takes only a
  host/port, nothing else.
- The client-side `CLAMAV_STREAM_MAX_BYTES` limit is enforced while
  streaming, chunk by chunk (reusing `settings.QUARANTINE_CHUNK_SIZE_BYTES`
  as the read granularity, matching A5b's quarantine-storage discipline).
  The file is never fully buffered in memory to check its size.
- On a client-side size-limit abort, the zero-length INSTREAM terminator
  is still sent so the socket is left in a clean protocol state rather
  than being abandoned mid-stream.

ClamAV INSTREAM/PING/VERSION wire protocol (verified against clamd's
documented command protocol):
- Commands prefixed with `z` are NUL-terminated on both the request and
  the response: `INSTREAM` (`b"zINSTREAM\\0"`) and `PING` (`b"zPING\\0"`,
  replies `b"PONG\\0"`).
- INSTREAM body: repeated `<4-byte big-endian length><that many bytes>`
  chunks, terminated by a zero-length chunk (`b"\\x00\\x00\\x00\\x00"`).
  The final reply is a NUL-terminated line: `stream: OK\\0`,
  `stream: <name> FOUND\\0`, or `stream: <message> ERROR\\0`.
- `VERSION` uses the `n`-prefixed, newline-terminated form instead:
  request `b"nVERSION\\n"`, reply is plain text terminated by `\\n`
  (no NUL, no `z` prefix) -- distinct from the `z`-commands above.
"""

from __future__ import annotations

import logging
import re
import socket
import struct
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path

from app.core.config import settings

logger = logging.getLogger(__name__)

_INSTREAM_COMMAND = b"zINSTREAM\0"
_PING_COMMAND = b"zPING\0"
_VERSION_COMMAND = b"nVERSION\n"
_ZERO_CHUNK = b"\x00\x00\x00\x00"
_RECV_BUFFER_BYTES = 4096
_MAX_RESPONSE_BYTES = 8192  # generous bound for a one-line clamd reply

_PONG_RESPONSE = "PONG"
_STREAM_OK = "stream: OK"
_FOUND_SUFFIX = " FOUND"
_ERROR_SUFFIX = " ERROR"
_STREAM_PREFIX = "stream: "

# "ClamAV 1.4.5/28058/Sun Jul 12 06:25:26 2026"
_VERSION_TIMESTAMP_FORMAT = "%a %b %d %H:%M:%S %Y"


class ScanOutcome(str, Enum):  # noqa: UP042 -- str mixin required for JSON/API contract
    CLEAN = "CLEAN"
    FOUND = "FOUND"
    ERROR = "ERROR"  # protocol-level ERROR response from clamd itself
    UNAVAILABLE = "UNAVAILABLE"  # connect failure / broken connection
    TIMEOUT = "TIMEOUT"
    PROTOCOL_ERROR = "PROTOCOL_ERROR"  # malformed/unexpected response
    SIZE_LIMIT_EXCEEDED = "SIZE_LIMIT_EXCEEDED"


@dataclass(frozen=True)
class ScanResult:
    outcome: ScanOutcome
    signature_name: str | None  # only set on FOUND; never surfaced to ordinary callers
    engine_version: str | None
    signature_version: str | None


@dataclass(frozen=True)
class VersionInfo:
    raw: str
    engine_version: str | None
    signature_version: str | None
    signature_timestamp: datetime | None


def _empty_result(outcome: ScanOutcome) -> ScanResult:
    return ScanResult(
        outcome=outcome,
        signature_name=None,
        engine_version=None,
        signature_version=None,
    )


def _safe_close(sock: socket.socket | None) -> None:
    if sock is None:
        return
    try:
        sock.close()
    except OSError:
        pass


def _connect() -> socket.socket:
    """Open a TCP connection to clamd with the configured connect timeout.
    Raises OSError/socket.timeout on failure -- callers must catch."""
    return socket.create_connection(
        (settings.CLAMAV_HOST, settings.CLAMAV_PORT),
        timeout=settings.CLAMAV_CONNECT_TIMEOUT_SECONDS,
    )


def _read_until(sock: socket.socket, terminator: bytes) -> bytes:
    """Read from `sock` until `terminator` is seen or the connection
    closes, bounded by `_MAX_RESPONSE_BYTES` so a misbehaving/malicious
    daemon response can never be buffered without limit."""
    data = b""
    while terminator not in data:
        chunk = sock.recv(_RECV_BUFFER_BYTES)
        if not chunk:
            break
        data += chunk
        if len(data) > _MAX_RESPONSE_BYTES:
            break
    return data


def scan_stream(file_path: Path, *, max_bytes: int | None = None) -> ScanResult:
    """Stream `file_path` to clamd over the INSTREAM protocol and return
    the scan verdict. Enforces `max_bytes` (default
    `settings.CLAMAV_STREAM_MAX_BYTES`) incrementally while reading the
    file in `settings.QUARANTINE_CHUNK_SIZE_BYTES`-sized chunks -- the
    file is never fully buffered in memory."""
    limit = max_bytes if max_bytes is not None else settings.CLAMAV_STREAM_MAX_BYTES
    chunk_size = settings.QUARANTINE_CHUNK_SIZE_BYTES

    try:
        sock = _connect()
    except TimeoutError:
        logger.warning(
            "clamav scan_stream connect timed out: %s", ScanOutcome.TIMEOUT.value
        )
        return _empty_result(ScanOutcome.TIMEOUT)
    except OSError:
        logger.warning(
            "clamav scan_stream connect failed: %s", ScanOutcome.UNAVAILABLE.value
        )
        return _empty_result(ScanOutcome.UNAVAILABLE)

    try:
        sock.settimeout(settings.CLAMAV_IO_TIMEOUT_SECONDS)
        try:
            sock.sendall(_INSTREAM_COMMAND)

            total = 0
            exceeded = False
            try:
                with open(file_path, "rb") as handle:
                    while True:
                        chunk = handle.read(chunk_size)
                        if not chunk:
                            break
                        total += len(chunk)
                        if total > limit:
                            exceeded = True
                            break
                        sock.sendall(struct.pack(">I", len(chunk)) + chunk)
            except OSError:
                logger.warning(
                    "clamav scan_stream local file read failed: %s",
                    ScanOutcome.UNAVAILABLE.value,
                )
                return _empty_result(ScanOutcome.UNAVAILABLE)

            # Always send the terminator, whether the stream completed
            # normally or was aborted for exceeding the size limit -- this
            # leaves the clamd session in a clean protocol state rather
            # than abandoning the socket mid-stream.
            sock.sendall(_ZERO_CHUNK)

            if exceeded:
                logger.warning(
                    "clamav scan_stream aborted: %s",
                    ScanOutcome.SIZE_LIMIT_EXCEEDED.value,
                )
                return _empty_result(ScanOutcome.SIZE_LIMIT_EXCEEDED)

            raw = _read_until(sock, b"\0")
        except TimeoutError:
            logger.warning(
                "clamav scan_stream I/O timed out: %s", ScanOutcome.TIMEOUT.value
            )
            return _empty_result(ScanOutcome.TIMEOUT)
        except OSError:
            logger.warning(
                "clamav scan_stream connection failed: %s",
                ScanOutcome.UNAVAILABLE.value,
            )
            return _empty_result(ScanOutcome.UNAVAILABLE)
    finally:
        _safe_close(sock)

    response = raw.split(b"\0", 1)[0].decode("utf-8", errors="replace").strip()

    if response == _STREAM_OK:
        logger.info("clamav scan_stream result: %s", ScanOutcome.CLEAN.value)
        return _empty_result(ScanOutcome.CLEAN)

    if response.startswith(_STREAM_PREFIX) and response.endswith(_FOUND_SUFFIX):
        name = response[len(_STREAM_PREFIX) : -len(_FOUND_SUFFIX)].strip()
        logger.warning("clamav scan_stream result: %s", ScanOutcome.FOUND.value)
        return ScanResult(
            outcome=ScanOutcome.FOUND,
            signature_name=name or None,
            engine_version=None,
            signature_version=None,
        )

    if response.startswith(_STREAM_PREFIX) and response.endswith(_ERROR_SUFFIX):
        logger.warning("clamav scan_stream result: %s", ScanOutcome.ERROR.value)
        return _empty_result(ScanOutcome.ERROR)

    logger.warning("clamav scan_stream result: %s", ScanOutcome.PROTOCOL_ERROR.value)
    return _empty_result(ScanOutcome.PROTOCOL_ERROR)


def check_connectivity() -> bool:
    """PING/PONG readiness handshake. Does not scan anything. Returns
    False for any connect/timeout/protocol failure."""
    try:
        sock = _connect()
    except (TimeoutError, OSError):
        logger.warning(
            "clamav check_connectivity failed: %s", ScanOutcome.UNAVAILABLE.value
        )
        return False

    try:
        sock.settimeout(settings.CLAMAV_IO_TIMEOUT_SECONDS)
        sock.sendall(_PING_COMMAND)
        raw = _read_until(sock, b"\0")
    except (TimeoutError, OSError):
        logger.warning(
            "clamav check_connectivity failed: %s", ScanOutcome.UNAVAILABLE.value
        )
        return False
    finally:
        _safe_close(sock)

    response = raw.split(b"\0", 1)[0].decode("ascii", errors="replace").strip()
    return response == _PONG_RESPONSE


_VERSION_LINE_RE = re.compile(r"^(?P<engine>.+?)/(?P<sig>[^/]+)/(?P<timestamp>.+)$")


def _parse_version_line(raw: str) -> VersionInfo | None:
    match = _VERSION_LINE_RE.match(raw)
    if not match:
        return None

    engine_part = match.group("engine").strip()
    signature_version = match.group("sig").strip() or None
    timestamp_str = match.group("timestamp").strip()

    engine_tokens = engine_part.split(" ", 1)
    engine_version = (
        engine_tokens[1].strip() if len(engine_tokens) == 2 else engine_part or None
    )

    signature_timestamp: datetime | None
    try:
        parsed = datetime.strptime(timestamp_str, _VERSION_TIMESTAMP_FORMAT)
        signature_timestamp = parsed.replace(tzinfo=UTC)
    except ValueError:
        signature_timestamp = None

    return VersionInfo(
        raw=raw,
        engine_version=engine_version,
        signature_version=signature_version,
        signature_timestamp=signature_timestamp,
    )


def get_version_info() -> VersionInfo | None:
    """VERSION handshake, for signature-age comparisons against
    `settings.CLAMAV_MAX_SIGNATURE_AGE_HOURS`. Returns None on any
    connect/timeout/protocol failure, or if the response cannot be
    parsed into the expected `engine/signature/timestamp` shape."""
    try:
        sock = _connect()
    except (TimeoutError, OSError):
        logger.warning(
            "clamav get_version_info failed: %s", ScanOutcome.UNAVAILABLE.value
        )
        return None

    try:
        sock.settimeout(settings.CLAMAV_IO_TIMEOUT_SECONDS)
        sock.sendall(_VERSION_COMMAND)
        raw = _read_until(sock, b"\n")
    except (TimeoutError, OSError):
        logger.warning(
            "clamav get_version_info failed: %s", ScanOutcome.UNAVAILABLE.value
        )
        return None
    finally:
        _safe_close(sock)

    text = raw.decode("utf-8", errors="replace").strip()
    if not text:
        logger.warning(
            "clamav get_version_info result: %s", ScanOutcome.PROTOCOL_ERROR.value
        )
        return None

    parsed = _parse_version_line(text)
    if parsed is None:
        logger.warning(
            "clamav get_version_info result: %s", ScanOutcome.PROTOCOL_ERROR.value
        )
    return parsed
