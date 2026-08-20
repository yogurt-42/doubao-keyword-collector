from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path
from typing import Any

from .update_checker import DownloadResult, _detect_variant

"""Windows self-replacement updater for the desktop application.

The main process calls `UpdateInstaller.install()` which:
1. Verifies the downloaded asset matches the current runtime variant.
2. Extracts / locates the replacement files.
3. Cleans up previous `.bak` backups.
4. Spawns `update_installer_helper.exe` and exits.

The helper runs independently, waits for the original process to die,
performs the file replacement, and starts the new version.
"""


class UpdateInstallerError(RuntimeError):
    """Raised when the update cannot be prepared or started."""


class UpdateInstaller:
    """Prepare and launch a self-replacement update."""

    HELPER_NAME = "update_installer_helper.exe"

    def __init__(self, download_result: DownloadResult) -> None:
        self.download_result = download_result
        self.variant = _detect_variant()
        self.current_exe = Path(sys.executable)

    @property
    def can_install(self) -> bool:
        """Return True only when running a frozen Windows build with a verified asset."""
        return self.variant in ("single", "portable") and self.download_result.verified

    def install(self) -> bool:
        """Launch the replacement helper and return immediately.

        The caller is expected to exit the current process as soon as possible
        after this method returns True.
        """
        if not self.can_install:
            raise UpdateInstallerError("当前环境不支持自动安装，请手动下载覆盖")

        if self.variant == "portable":
            return self._install_portable()
        return self._install_single()

    def _install_portable(self) -> bool:
        app_dir = self.current_exe.parent
        new_dir = self._extract_portable()
        helper_exe = self._find_helper()
        return self._spawn_helper(
            helper_exe,
            mode="portable",
            app_dir=str(app_dir),
            new_dir=str(new_dir),
            pid=str(os.getpid()),
        )

    def _install_single(self) -> bool:
        old_exe = self.current_exe
        new_exe = self.download_result.path
        helper_exe = self._find_helper()
        return self._spawn_helper(
            helper_exe,
            mode="single",
            old_exe=str(old_exe),
            new_exe=str(new_exe),
            pid=str(os.getpid()),
        )

    def _extract_portable(self) -> Path:
        """Extract the downloaded portable zip and return the application root inside."""
        dest = Path(tempfile.mkdtemp(prefix="doubao-portable-"))
        try:
            with zipfile.ZipFile(self.download_result.path, "r") as archive:
                archive.extractall(dest)
        except zipfile.BadZipFile as exc:
            raise UpdateInstallerError(f"便携版压缩包损坏：{exc}") from exc

        # The zip may contain a single top-level folder; if that folder contains
        # the executable, return it. Otherwise the archive is flat and the root
        # is the application directory.
        entries = [entry for entry in dest.iterdir() if entry.is_dir()]
        if len(entries) == 1:
            candidate = entries[0]
            if any(candidate.glob("*.exe")):
                return candidate
        return dest

    def _find_helper(self) -> Path:
        """Locate the helper executable next to the current runtime.

        In a packaged build the helper should be shipped alongside the main exe
        (onedir), inside the onedir `_internal` folder, or inside the one-file
        temporary extraction directory. During development fall back to running
        the helper module with the same Python interpreter.
        """
        # Packaged layout: helper sits next to the current executable (onedir).
        candidate = self.current_exe.parent / self.HELPER_NAME
        if candidate.exists():
            return candidate

        # Onedir layout: helper is collected into the _internal folder.
        candidate = self.current_exe.parent / "_internal" / self.HELPER_NAME
        if candidate.exists():
            return candidate

        # One-file layout: helper is extracted into sys._MEIPASS.
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            candidate = Path(meipass) / self.HELPER_NAME
            if candidate.exists():
                return candidate

        # Development fallback: run the helper module directly.
        if not getattr(sys, "frozen", False):
            module_file = Path(__file__).with_name("update_installer_helper.py")
            if module_file.exists():
                return module_file

        raise UpdateInstallerError(f"找不到更新助手 {self.HELPER_NAME}，请重新下载完整安装包")

    def _spawn_helper(self, helper: Path, **kwargs: Any) -> bool:
        """Start the helper process detached from the current console."""
        args = [str(helper)]
        for key, value in kwargs.items():
            args.append(f"--{key.replace('_', '-')}")
            args.append(str(value))

        creationflags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS
        try:
            if helper.suffix.lower() == ".py":
                # Development mode: run via the same interpreter.
                args = [sys.executable, str(helper), *args[1:]]

            subprocess.Popen(
                args,
                creationflags=creationflags,
                close_fds=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except OSError as exc:
            raise UpdateInstallerError(f"无法启动更新助手：{exc}") from exc
        return True

    @staticmethod
    def cleanup_old_backups(target: Path) -> None:
        """Remove any existing `.bak` backups for `target` before creating a new one.

        This keeps exactly one backup (the one we are about to create) and removes
        all older backups matching the target name.
        """
        if not target.parent.exists():
            return
        stem = target.name
        for entry in target.parent.iterdir():
            if entry == target:
                continue
            if entry.name.startswith(stem) and ".bak" in entry.name:
                if entry.is_dir():
                    shutil.rmtree(entry, ignore_errors=True)
                else:
                    entry.unlink(missing_ok=True)
