import os

import pytest

from app.core.secrets import MAX_SECRET_FILE_BYTES, SecretFileError, read_secret_file


def test_reads_secret_and_trims_final_newline(tmp_path):
    p = tmp_path / "secret"
    p.write_bytes(b"hunter2\n")
    assert read_secret_file(p) == "hunter2"


def test_reads_secret_and_trims_final_crlf_newline(tmp_path):
    p = tmp_path / "secret"
    p.write_bytes(b"hunter2\r\n")
    assert read_secret_file(p) == "hunter2"


def test_does_not_trim_internal_newlines(tmp_path):
    p = tmp_path / "secret"
    p.write_bytes(b"line1\nline2\n")
    assert read_secret_file(p) == "line1\nline2"


def test_missing_file_fails_closed(tmp_path):
    with pytest.raises(SecretFileError, match="not found"):
        read_secret_file(tmp_path / "does-not-exist")


def test_empty_file_fails_closed(tmp_path):
    p = tmp_path / "secret"
    p.write_text("", encoding="utf-8")
    with pytest.raises(SecretFileError, match="empty"):
        read_secret_file(p)


def test_file_that_is_only_a_newline_fails_closed(tmp_path):
    p = tmp_path / "secret"
    p.write_text("\n", encoding="utf-8")
    with pytest.raises(SecretFileError, match="empty"):
        read_secret_file(p)


def test_oversized_file_fails_closed(tmp_path):
    p = tmp_path / "secret"
    p.write_text("a" * (MAX_SECRET_FILE_BYTES + 1), encoding="utf-8")
    with pytest.raises(SecretFileError, match="exceeds"):
        read_secret_file(p)


def test_directory_path_fails_closed(tmp_path):
    d = tmp_path / "a_directory"
    d.mkdir()
    with pytest.raises(SecretFileError, match="directory"):
        read_secret_file(d)


@pytest.mark.skipif(os.name == "nt", reason="POSIX-only device file test")
def test_non_regular_file_fails_closed():
    with pytest.raises(SecretFileError, match="not a regular file"):
        read_secret_file("/dev/null")


def test_error_message_never_contains_secret_contents(tmp_path):
    p = tmp_path / "secret"
    p.write_text("a" * (MAX_SECRET_FILE_BYTES + 1), encoding="utf-8")
    try:
        read_secret_file(p)
    except SecretFileError as exc:
        assert "a" * 50 not in str(exc)


def test_secret_file_error_is_a_value_error():
    # SecretFileError must subclass ValueError so it composes cleanly with
    # pydantic's `before` model validators (which expect ValueError/
    # TypeError/AssertionError to signal validation failure).
    assert issubclass(SecretFileError, ValueError)
