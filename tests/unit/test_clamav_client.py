"""Tests for app.services.clamav_client: the ClamAV INSTREAM protocol
client. Uses a real loopback TCP server that speaks a scripted subset of
the clamd wire protocol -- a mock of `socket.socket` would not catch
protocol-framing bugs (wrong length-prefix byte order, missing
terminator, etc.), which is exactly the class of bug this module must
get right."""

from __future__ import annotations

import socket
import struct
import threading
import time
from collections.abc import Callable
from pathlib import Path

import pytest

from app.core.config import settings
from app.services.clamav_client import (
    ScanOutcome,
    _parse_version_line,
    check_connectivity,
    get_version_info,
    scan_stream,
)

Handler = Callable[[socket.socket], None]


class _ScriptedServer:
    """A loopback TCP server that accepts exactly one connection and runs
    `handler(conn)` against it on a background thread."""

    def __init__(self, handler: Handler) -> None:
        self._listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._listener.bind(("127.0.0.1", 0))
        self._listener.listen(1)
        self.port = self._listener.getsockname()[1]
        self._handler = handler
        self._thread = threading.Thread(target=self._run, daemon=True)

    def start(self) -> _ScriptedServer:
        self._thread.start()
        return self

    def _run(self) -> None:
        try:
            conn, _ = self._listener.accept()
        except OSError:
            return
        try:
            self._handler(conn)
        except OSError:
            pass
        finally:
            conn.close()

    def stop(self) -> None:
        self._listener.close()
        self._thread.join(timeout=2)


def _recv_exact(conn: socket.socket, n: int) -> bytes | None:
    buf = b""
    while len(buf) < n:
        chunk = conn.recv(n - len(buf))
        if not chunk:
            return None
        buf += chunk
    return buf


def _read_instream_command(conn: socket.socket) -> bytes | None:
    return _recv_exact(conn, len(b"zINSTREAM\0"))


def _drain_instream_chunks(conn: socket.socket) -> bytes:
    """Read length-prefixed chunks until the zero-length terminator.
    Returns the concatenated content bytes actually received."""
    received = b""
    while True:
        header = _recv_exact(conn, 4)
        if header is None:
            break
        (length,) = struct.unpack(">I", header)
        if length == 0:
            break
        payload = _recv_exact(conn, length)
        if payload is None:
            break
        received += payload
    return received


@pytest.fixture(autouse=True)
def _point_settings_at_loopback(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "CLAMAV_HOST", "127.0.0.1")
    monkeypatch.setattr(settings, "CLAMAV_CONNECT_TIMEOUT_SECONDS", 2.0)
    monkeypatch.setattr(settings, "CLAMAV_IO_TIMEOUT_SECONDS", 1.0)


def _use_server(monkeypatch: pytest.MonkeyPatch, server: _ScriptedServer) -> None:
    monkeypatch.setattr(settings, "CLAMAV_PORT", server.port)


class TestScanStreamClean:
    def test_clean_file_returns_ok(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def handler(conn: socket.socket) -> None:
            assert _read_instream_command(conn) == b"zINSTREAM\0"
            content = _drain_instream_chunks(conn)
            assert content == b"clean file content"
            conn.sendall(b"stream: OK\0")

        server = _ScriptedServer(handler).start()
        _use_server(monkeypatch, server)
        try:
            target = tmp_path / "clean.bin"
            target.write_bytes(b"clean file content")
            result = scan_stream(target)
        finally:
            server.stop()

        assert result.outcome == ScanOutcome.CLEAN
        assert result.signature_name is None


class TestScanStreamFound:
    def test_malware_signature_name_parsed(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def handler(conn: socket.socket) -> None:
            _read_instream_command(conn)
            _drain_instream_chunks(conn)
            conn.sendall(b"stream: Eicar-Test-Signature FOUND\0")

        server = _ScriptedServer(handler).start()
        _use_server(monkeypatch, server)
        try:
            target = tmp_path / "eicar.bin"
            target.write_bytes(b"fake eicar payload")
            result = scan_stream(target)
        finally:
            server.stop()

        assert result.outcome == ScanOutcome.FOUND
        assert result.signature_name == "Eicar-Test-Signature"


class TestScanStreamClamdError:
    def test_clamd_side_error_response(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def handler(conn: socket.socket) -> None:
            _read_instream_command(conn)
            _drain_instream_chunks(conn)
            conn.sendall(b"stream: Size limit reached ERROR\0")

        server = _ScriptedServer(handler).start()
        _use_server(monkeypatch, server)
        try:
            target = tmp_path / "err.bin"
            target.write_bytes(b"some content")
            result = scan_stream(target)
        finally:
            server.stop()

        assert result.outcome == ScanOutcome.ERROR


class TestScanStreamMalformedResponse:
    def test_unrecognized_response_is_protocol_error(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def handler(conn: socket.socket) -> None:
            _read_instream_command(conn)
            _drain_instream_chunks(conn)
            conn.sendall(b"totally unexpected garbage\0")

        server = _ScriptedServer(handler).start()
        _use_server(monkeypatch, server)
        try:
            target = tmp_path / "weird.bin"
            target.write_bytes(b"content")
            result = scan_stream(target)
        finally:
            server.stop()

        assert result.outcome == ScanOutcome.PROTOCOL_ERROR


class TestScanStreamConnectionRefused:
    def test_closed_port_returns_unavailable(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Bind to get a free ephemeral port, then close it immediately so
        # nothing is listening -- connecting to it must fail fast with
        # ECONNREFUSED on loopback.
        probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        probe.bind(("127.0.0.1", 0))
        probe.listen(1)
        free_port = probe.getsockname()[1]
        probe.close()

        monkeypatch.setattr(settings, "CLAMAV_PORT", free_port)
        # Some platforms (observed on Windows) take a couple of seconds to
        # deliver the RST for a just-closed loopback port; give connect()
        # enough headroom that this resolves as a real refusal rather than
        # racing the shorter default connect timeout into a false TIMEOUT.
        monkeypatch.setattr(settings, "CLAMAV_CONNECT_TIMEOUT_SECONDS", 10.0)

        target = tmp_path / "irrelevant.bin"
        target.write_bytes(b"content")
        result = scan_stream(target)

        assert result.outcome == ScanOutcome.UNAVAILABLE


class TestScanStreamTimeout:
    def test_hanging_server_returns_timeout(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(settings, "CLAMAV_IO_TIMEOUT_SECONDS", 0.3)

        def handler(conn: socket.socket) -> None:
            _read_instream_command(conn)
            _drain_instream_chunks(conn)
            # Never respond -- forces the client's recv() to time out.
            time.sleep(2)

        server = _ScriptedServer(handler).start()
        _use_server(monkeypatch, server)
        try:
            target = tmp_path / "slow.bin"
            target.write_bytes(b"content")
            result = scan_stream(target)
        finally:
            server.stop()

        assert result.outcome == ScanOutcome.TIMEOUT


class TestScanStreamWriteTimeout:
    def test_write_timeout_during_chunk_send_returns_timeout(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Regression test: a stall while *sending* a chunk (clamd's
        # receive buffer stops draining) must be categorized as TIMEOUT,
        # not misread as a local file-read failure (UNAVAILABLE) just
        # because TimeoutError is a subclass of OSError.
        monkeypatch.setattr(settings, "CLAMAV_IO_TIMEOUT_SECONDS", 1.0)

        def handler(conn: socket.socket) -> None:
            assert _read_instream_command(conn) == b"zINSTREAM\0"
            # Never read the chunk data that follows -- once the kernel
            # send/receive buffers fill (empirically well under 1 MiB on
            # loopback), the client's blocking sendall() for a later
            # chunk must time out.
            time.sleep(5)

        server = _ScriptedServer(handler).start()
        _use_server(monkeypatch, server)
        try:
            target = tmp_path / "large.bin"
            target.write_bytes(b"x" * (2 * 1024 * 1024))  # 2 MiB
            result = scan_stream(target)
        finally:
            server.stop()

        assert result.outcome == ScanOutcome.TIMEOUT


class TestScanStreamSizeLimit:
    def test_oversized_file_rejected_without_exceeding_max_bytes_on_wire(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(settings, "QUARANTINE_CHUNK_SIZE_BYTES", 1000)
        max_bytes = 2000
        received_content: list[bytes] = []

        def handler(conn: socket.socket) -> None:
            _read_instream_command(conn)
            content = _drain_instream_chunks(conn)
            received_content.append(content)
            # Client should have already returned before reading a reply,
            # but respond anyway so the handler doesn't block forever.
            conn.sendall(b"stream: OK\0")

        server = _ScriptedServer(handler).start()
        _use_server(monkeypatch, server)
        try:
            target = tmp_path / "big.bin"
            target.write_bytes(b"x" * 5000)
            result = scan_stream(target, max_bytes=max_bytes)
        finally:
            server.stop()

        assert result.outcome == ScanOutcome.SIZE_LIMIT_EXCEEDED
        assert len(received_content) == 1
        assert len(received_content[0]) <= max_bytes


class TestCheckConnectivity:
    def test_pong_response_is_true(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def handler(conn: socket.socket) -> None:
            assert _recv_exact(conn, len(b"zPING\0")) == b"zPING\0"
            conn.sendall(b"PONG\0")

        server = _ScriptedServer(handler).start()
        _use_server(monkeypatch, server)
        try:
            assert check_connectivity() is True
        finally:
            server.stop()

    def test_connection_refused_is_false(self, monkeypatch: pytest.MonkeyPatch) -> None:
        probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        probe.bind(("127.0.0.1", 0))
        probe.listen(1)
        free_port = probe.getsockname()[1]
        probe.close()

        monkeypatch.setattr(settings, "CLAMAV_PORT", free_port)
        monkeypatch.setattr(settings, "CLAMAV_CONNECT_TIMEOUT_SECONDS", 10.0)
        assert check_connectivity() is False

    def test_wedged_daemon_times_out_by_connect_timeout_not_io_timeout(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Regression test for review finding 4: the PING read in
        check_connectivity() must be bounded by
        CLAMAV_CONNECT_TIMEOUT_SECONDS (documented in readiness.py's
        docstring, RUNBOOK.md 5.3, and DEPLOYMENT.md as the readiness
        probe's bound), not the much longer CLAMAV_IO_TIMEOUT_SECONDS
        used for real scan streaming. A daemon that accepts the TCP
        connection but never responds to PING -- exactly the wedged/
        overloaded scenario a readiness probe exists to catch -- must
        fail fast on the short timeout, not hang for the long one."""
        monkeypatch.setattr(settings, "CLAMAV_CONNECT_TIMEOUT_SECONDS", 0.3)
        monkeypatch.setattr(settings, "CLAMAV_IO_TIMEOUT_SECONDS", 30.0)

        def handler(conn: socket.socket) -> None:
            assert _recv_exact(conn, len(b"zPING\0")) == b"zPING\0"
            # Never respond -- if the PING read were bounded by the long
            # CLAMAV_IO_TIMEOUT_SECONDS instead of the short
            # CLAMAV_CONNECT_TIMEOUT_SECONDS, this test would hang for
            # ~30s instead of returning quickly.
            time.sleep(5)

        server = _ScriptedServer(handler).start()
        _use_server(monkeypatch, server)
        started = time.monotonic()
        try:
            result = check_connectivity()
        finally:
            server.stop()
        elapsed = time.monotonic() - started

        assert result is False
        assert elapsed < 5.0, (
            f"check_connectivity took {elapsed:.1f}s -- PING read is not "
            "bounded by CLAMAV_CONNECT_TIMEOUT_SECONDS"
        )


class TestGetVersionInfo:
    def test_well_formed_version_string_parsed(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def handler(conn: socket.socket) -> None:
            assert _recv_exact(conn, len(b"nVERSION\n")) == b"nVERSION\n"
            conn.sendall(b"ClamAV 1.4.5/28058/Sun Jul 12 06:25:26 2026\n")

        server = _ScriptedServer(handler).start()
        _use_server(monkeypatch, server)
        try:
            info = get_version_info()
        finally:
            server.stop()

        assert info is not None
        assert info.engine_version == "1.4.5"
        assert info.signature_version == "28058"
        assert info.signature_timestamp is not None
        assert info.signature_timestamp.year == 2026
        assert info.signature_timestamp.month == 7
        assert info.signature_timestamp.day == 12

    def test_malformed_version_string_returns_none(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def handler(conn: socket.socket) -> None:
            _recv_exact(conn, len(b"nVERSION\n"))
            conn.sendall(b"not a version string at all\n")

        server = _ScriptedServer(handler).start()
        _use_server(monkeypatch, server)
        try:
            info = get_version_info()
        finally:
            server.stop()

        assert info is None

    def test_parse_version_line_unit(self) -> None:
        parsed = _parse_version_line("ClamAV 1.4.5/28058/Sun Jul 12 06:25:26 2026")
        assert parsed is not None
        assert parsed.engine_version == "1.4.5"
        assert parsed.signature_version == "28058"
        assert parsed.signature_timestamp is not None

    def test_parse_version_line_malformed_timestamp_keeps_other_fields(self) -> None:
        parsed = _parse_version_line("ClamAV 1.4.5/28058/not-a-real-timestamp")
        assert parsed is not None
        assert parsed.engine_version == "1.4.5"
        assert parsed.signature_version == "28058"
        assert parsed.signature_timestamp is None

    def test_parse_version_line_no_separators_returns_none(self) -> None:
        assert _parse_version_line("garbage") is None

    def test_parse_version_line_all_months_parse_without_locale_dependence(
        self,
    ) -> None:
        """Regression test for review finding 7: month parsing must not
        rely on strptime's locale-bound "%b" -- verify every month
        abbreviation clamd could emit parses correctly regardless of the
        process's locale (this test itself never touches locale.setlocale,
        which is exactly the point: the explicit month table means there
        is nothing locale-sensitive to set up)."""
        months = [
            ("Jan", 1),
            ("Feb", 2),
            ("Mar", 3),
            ("Apr", 4),
            ("May", 5),
            ("Jun", 6),
            ("Jul", 7),
            ("Aug", 8),
            ("Sep", 9),
            ("Oct", 10),
            ("Nov", 11),
            ("Dec", 12),
        ]
        for abbrev, month_number in months:
            parsed = _parse_version_line(
                f"ClamAV 1.4.5/28058/Sun {abbrev} 12 06:25:26 2026"
            )
            assert parsed is not None
            assert parsed.signature_timestamp is not None
            assert parsed.signature_timestamp.month == month_number

    def test_parse_version_line_unrecognized_month_fails_closed(self) -> None:
        parsed = _parse_version_line("ClamAV 1.4.5/28058/Sun Xyz 12 06:25:26 2026")
        assert parsed is not None
        assert parsed.signature_timestamp is None
