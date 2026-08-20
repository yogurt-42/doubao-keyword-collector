from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from doubao2api.update_checker import AssetInfo, DownloadResult
from doubao2api.update_installer import UpdateInstaller, UpdateInstallerError


def _result(path: Path, *, verified: bool = True) -> DownloadResult:
    return DownloadResult(
        asset=AssetInfo(name="test.exe", url="http://example.com/test.exe", size=1),
        path=path,
        size=path.stat().st_size if path.exists() else 0,
        sha256_expected=None,
        sha256_actual=None,
        verified=verified,
        verification_method="fallback",
    )


def test_installer_refuses_unknown_runtime(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("doubao2api.update_installer.sys.frozen", False, raising=False)
    monkeypatch.setattr(
        "doubao2api.update_installer.sys.executable", str(tmp_path / "python.exe"), raising=False
    )
    installer = UpdateInstaller(_result(tmp_path / "test.exe"))
    assert installer.can_install is False
    with pytest.raises(UpdateInstallerError):
        installer.install()


def test_installer_accepts_frozen_single(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    exe = tmp_path / "doubao-keyword-collector.exe"
    exe.write_text("fake exe")
    monkeypatch.setattr("doubao2api.update_installer.sys.frozen", True, raising=False)
    monkeypatch.setattr("doubao2api.update_installer.sys.executable", str(exe), raising=False)
    monkeypatch.setattr(
        "doubao2api.update_installer.sys._MEIPASS", str(tmp_path / "_MEI"), raising=False
    )

    installer = UpdateInstaller(_result(exe))
    assert installer.variant == "single"
    assert installer.can_install is True


def test_installer_accepts_frozen_portable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    exe = tmp_path / "doubao-keyword-collector.exe"
    exe.write_text("fake exe")
    (tmp_path / "_internal").mkdir()
    monkeypatch.setattr("doubao2api.update_installer.sys.frozen", True, raising=False)
    monkeypatch.setattr("doubao2api.update_installer.sys.executable", str(exe), raising=False)

    installer = UpdateInstaller(_result(exe))
    assert installer.variant == "portable"
    assert installer.can_install is True


def test_installer_refuses_unverified_download(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    exe = tmp_path / "doubao-keyword-collector.exe"
    exe.write_text("fake exe")
    monkeypatch.setattr("doubao2api.update_installer.sys.frozen", True, raising=False)
    monkeypatch.setattr("doubao2api.update_installer.sys.executable", str(exe), raising=False)
    monkeypatch.setattr(
        "doubao2api.update_installer.sys._MEIPASS", str(tmp_path / "_MEI"), raising=False
    )

    installer = UpdateInstaller(_result(exe, verified=False))
    assert installer.can_install is False


def test_cleanup_old_backups_removes_existing_backups(tmp_path: Path) -> None:
    target = tmp_path / "app"
    target.mkdir()
    old_backup = tmp_path / "app.bak"
    old_backup.mkdir()
    older_backup = tmp_path / "app.bak.old"
    older_backup.mkdir()

    UpdateInstaller.cleanup_old_backups(target)

    assert not old_backup.exists()
    assert not older_backup.exists()
    assert target.exists()


def test_extract_portable_unpacks_zip(tmp_path: Path) -> None:
    zip_path = tmp_path / "v1.0.3-portable.zip"
    with zipfile.ZipFile(zip_path, "w") as archive:
        archive.writestr("doubao-keyword-collector-v1.0.3-portable/_internal/test.dll", "dll")
        archive.writestr(
            "doubao-keyword-collector-v1.0.3-portable/doubao-keyword-collector.exe", "exe"
        )

    exe = tmp_path / "doubao-keyword-collector.exe"
    exe.write_text("fake exe")
    installer = UpdateInstaller(_result(zip_path))
    installer.current_exe = exe

    extracted = installer._extract_portable()
    assert (extracted / "doubao-keyword-collector.exe").exists()
    assert (extracted / "_internal" / "test.dll").exists()


def test_extract_portable_unpacks_flat_zip(tmp_path: Path) -> None:
    zip_path = tmp_path / "v1.0.3-portable.zip"
    with zipfile.ZipFile(zip_path, "w") as archive:
        archive.writestr("_internal/test.dll", "dll")
        archive.writestr("doubao-keyword-collector.exe", "exe")

    exe = tmp_path / "doubao-keyword-collector.exe"
    exe.write_text("fake exe")
    installer = UpdateInstaller(_result(zip_path))
    installer.current_exe = exe

    extracted = installer._extract_portable()
    assert (extracted / "doubao-keyword-collector.exe").exists()


def test_installer_finds_helper_in_internal_for_onedir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app_dir = tmp_path / "doubao-keyword-collector"
    app_dir.mkdir()
    internal = app_dir / "_internal"
    internal.mkdir()
    exe = app_dir / "doubao-keyword-collector.exe"
    exe.write_text("fake exe")
    helper = internal / "update_installer_helper.exe"
    helper.write_text("fake helper")
    monkeypatch.setattr("doubao2api.update_installer.sys.frozen", True, raising=False)
    monkeypatch.setattr("doubao2api.update_installer.sys.executable", str(exe), raising=False)

    installer = UpdateInstaller(_result(exe))
    found = installer._find_helper()
    assert found == helper


def test_installer_finds_helper_in_meipass_for_single_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    exe = tmp_path / "doubao-keyword-collector.exe"
    exe.write_text("fake exe")
    meipass = tmp_path / "_MEI12345"
    meipass.mkdir()
    helper = meipass / "update_installer_helper.exe"
    helper.write_text("fake helper")
    monkeypatch.setattr("doubao2api.update_installer.sys.frozen", True, raising=False)
    monkeypatch.setattr("doubao2api.update_installer.sys.executable", str(exe), raising=False)
    monkeypatch.setattr("doubao2api.update_installer.sys._MEIPASS", str(meipass), raising=False)

    installer = UpdateInstaller(_result(exe))
    found = installer._find_helper()
    assert found == helper


def test_installer_finds_helper_next_to_exe(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    exe = tmp_path / "doubao-keyword-collector.exe"
    exe.write_text("fake exe")
    helper = tmp_path / "update_installer_helper.exe"
    helper.write_text("fake helper")
    monkeypatch.setattr("doubao2api.update_installer.sys.frozen", True, raising=False)
    monkeypatch.setattr("doubao2api.update_installer.sys.executable", str(exe), raising=False)
    monkeypatch.setattr(
        "doubao2api.update_installer.sys._MEIPASS", str(tmp_path / "_MEI"), raising=False
    )

    installer = UpdateInstaller(_result(exe))
    found = installer._find_helper()
    assert found == helper


def test_installer_raises_when_helper_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    exe = tmp_path / "doubao-keyword-collector.exe"
    exe.write_text("fake exe")
    monkeypatch.setattr("doubao2api.update_installer.sys.frozen", True, raising=False)
    monkeypatch.setattr("doubao2api.update_installer.sys.executable", str(exe), raising=False)
    monkeypatch.setattr(
        "doubao2api.update_installer.sys._MEIPASS", str(tmp_path / "_MEI"), raising=False
    )

    installer = UpdateInstaller(_result(exe))
    with pytest.raises(UpdateInstallerError):
        installer._find_helper()
