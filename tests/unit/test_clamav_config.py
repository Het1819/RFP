"""ClamAV settings (Phase A5c): defaults and the CLAMAV_STREAM_MAX_BYTES
<= MAX_UPLOAD_SIZE structural invariant, enforced in every environment
(not gated behind production hardening)."""

import pytest
from pydantic import ValidationError

from app.core.config import Settings


class TestClamavSettingsDefaults:
    def test_defaults_present(self) -> None:
        s = Settings(APP_ENV="development")
        assert s.CLAMAV_HOST == "clamd"
        assert s.CLAMAV_PORT == 3310
        assert s.CLAMAV_CONNECT_TIMEOUT_SECONDS == 5.0
        assert s.CLAMAV_IO_TIMEOUT_SECONDS == 30.0
        assert s.CLAMAV_STREAM_MAX_BYTES == 10 * 1024 * 1024
        assert s.CLAMAV_MAX_SIGNATURE_AGE_HOURS == 48


class TestClamavStreamMaxBytesInvariant:
    def test_stream_max_exceeding_upload_size_rejected_in_development(self) -> None:
        with pytest.raises(ValidationError, match="CLAMAV_STREAM_MAX_BYTES"):
            Settings(
                APP_ENV="development",
                MAX_UPLOAD_SIZE=1000,
                CLAMAV_STREAM_MAX_BYTES=1001,
            )

    def test_stream_max_exceeding_upload_size_rejected_in_test_env(self) -> None:
        # Not gated behind production-hardening: must also fail in test.
        with pytest.raises(ValidationError, match="CLAMAV_STREAM_MAX_BYTES"):
            Settings(
                APP_ENV="test",
                MAX_UPLOAD_SIZE=1000,
                CLAMAV_STREAM_MAX_BYTES=1001,
            )

    def test_stream_max_equal_to_upload_size_accepted(self) -> None:
        s = Settings(
            APP_ENV="development",
            MAX_UPLOAD_SIZE=1000,
            CLAMAV_STREAM_MAX_BYTES=1000,
        )
        assert s.CLAMAV_STREAM_MAX_BYTES == 1000

    def test_stream_max_below_upload_size_accepted(self) -> None:
        s = Settings(
            APP_ENV="development",
            MAX_UPLOAD_SIZE=1000,
            CLAMAV_STREAM_MAX_BYTES=500,
        )
        assert s.CLAMAV_STREAM_MAX_BYTES == 500
