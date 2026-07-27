import os
import stat

import pytest

from app.core.config import settings


class TestQuarantineReadiness:
    def test_missing_quarantine_root_fails_readiness(
        self, tmp_path, monkeypatch
    ) -> None:
        missing = tmp_path / "does-not-exist"
        monkeypatch.setattr(settings, "QUARANTINE_STORAGE_PATH", str(missing))
        from app.core.readiness import check_quarantine_storage

        result = check_quarantine_storage()
        assert result.healthy is False

    def test_symlink_quarantine_root_fails_readiness(
        self, tmp_path, monkeypatch
    ) -> None:
        if os.name != "posix":
            pytest.skip("POSIX-only symlink check")
        real_dir = tmp_path / "real"
        real_dir.mkdir()
        link = tmp_path / "link"
        link.symlink_to(real_dir, target_is_directory=True)
        monkeypatch.setattr(settings, "QUARANTINE_STORAGE_PATH", str(link))
        from app.core.readiness import check_quarantine_storage

        result = check_quarantine_storage()
        assert result.healthy is False

    def test_unwritable_quarantine_root_fails_readiness(
        self, tmp_path, monkeypatch
    ) -> None:
        if os.name != "posix":
            pytest.skip("POSIX-only permission check")
        ro_dir = tmp_path / "readonly"
        ro_dir.mkdir()
        ro_dir.chmod(stat.S_IREAD | stat.S_IEXEC)
        monkeypatch.setattr(settings, "QUARANTINE_STORAGE_PATH", str(ro_dir))
        from app.core.readiness import check_quarantine_storage

        try:
            result = check_quarantine_storage()
            assert result.healthy is False
        finally:
            ro_dir.chmod(stat.S_IRWXU)

    def test_healthy_quarantine_root_passes_readiness(
        self, tmp_path, monkeypatch
    ) -> None:
        healthy_dir = tmp_path / "quarantine"
        healthy_dir.mkdir()
        monkeypatch.setattr(settings, "QUARANTINE_STORAGE_PATH", str(healthy_dir))
        from app.core.readiness import check_quarantine_storage

        result = check_quarantine_storage()
        assert result.healthy is True

    def test_failure_detail_never_leaks_host_path(self, tmp_path, monkeypatch) -> None:
        missing = tmp_path / "does-not-exist"
        monkeypatch.setattr(settings, "QUARANTINE_STORAGE_PATH", str(missing))
        from app.core.readiness import check_quarantine_storage

        result = check_quarantine_storage()
        assert str(missing) not in result.detail
        assert str(tmp_path) not in result.detail
