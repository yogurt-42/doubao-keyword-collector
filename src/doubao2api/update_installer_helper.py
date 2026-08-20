"""Standalone helper for replacing a running Windows application.

This module is intentionally self-contained so it can be packaged as a small
standalone executable by PyInstaller. The main application spawns it with the
necessary paths and PID, then exits. The helper waits for the original process
to die, performs the replacement, and starts the new version.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path


def _log(message: str) -> None:
    """Append a line to a helper log file in the system temp directory."""
    log_path = Path(tempfile.gettempdir()) / "doubao_update_helper.log"
    try:
        with log_path.open("a", encoding="utf-8") as stream:
            stream.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {message}\n")
    except OSError:
        pass


def _wait_for_process(pid: int, timeout: float = 60.0) -> bool:
    """Wait until the process with ``pid`` no longer exists."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            # ``os.kill(pid, 0)`` raises OSError when the process is gone.
            os.kill(pid, 0)
        except OSError:
            return True
        time.sleep(0.5)
    return False


def _remove_path(path: Path) -> None:
    """Delete a file or directory tree, ignoring errors."""
    if not path.exists():
        return
    if path.is_dir():
        shutil.rmtree(path, ignore_errors=True)
    else:
        path.unlink(missing_ok=True)


def _replace_single(old_exe: Path, new_exe: Path) -> None:
    """Backup the old executable and copy the new one into place."""
    backup = old_exe.with_suffix(old_exe.suffix + ".bak")
    _remove_path(backup)
    _log(f"backing up {old_exe} -> {backup}")
    old_exe.rename(backup)
    _log(f"copying {new_exe} -> {old_exe}")
    shutil.copy2(new_exe, old_exe)


def _replace_portable(app_dir: Path, new_dir: Path) -> None:
    """Backup the old app directory and move the new contents into place."""
    backup = Path(str(app_dir) + ".bak")
    _remove_path(backup)
    _log(f"backing up {app_dir} -> {backup}")
    app_dir.rename(backup)

    _log(f"copying {new_dir} contents -> {app_dir}")
    app_dir.mkdir(parents=True)
    for entry in new_dir.iterdir():
        dest = app_dir / entry.name
        if entry.is_dir():
            shutil.copytree(entry, dest)
        else:
            shutil.copy2(entry, dest)


def _start_new(app_dir: Path, exe_name: str) -> None:
    """Launch the updated executable detached from the helper console."""
    new_exe = app_dir / exe_name
    if not new_exe.exists():
        raise RuntimeError(f"updated executable not found: {new_exe}")

    _log(f"starting {new_exe}")
    creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) | getattr(
        subprocess, "DETACHED_PROCESS", 0
    )
    subprocess.Popen(
        [str(new_exe)],
        creationflags=creationflags,
        close_fds=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        cwd=str(app_dir),
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Replace a running Doubao Keyword Collector installation."
    )
    parser.add_argument("--mode", required=True, choices=["single", "portable"])
    parser.add_argument("--pid", required=True, type=int, help="PID to wait for")
    parser.add_argument("--old-exe", help="Path to the current executable (single mode)")
    parser.add_argument("--new-exe", help="Path to the downloaded executable (single mode)")
    parser.add_argument("--app-dir", help="Path to the current app directory (portable mode)")
    parser.add_argument("--new-dir", help="Path to the extracted new app directory (portable mode)")
    parser.add_argument("--exe-name", default="doubao-keyword-collector.exe", help="Exe name")
    args = parser.parse_args(argv)

    _log(f"started mode={args.mode} pid={args.pid}")

    if not _wait_for_process(args.pid):
        _log(f"timed out waiting for pid {args.pid}")
        return 1

    try:
        if args.mode == "single":
            old_exe = Path(args.old_exe)
            new_exe = Path(args.new_exe)
            _replace_single(old_exe, new_exe)
            _start_new(old_exe.parent, old_exe.name)
        else:
            app_dir = Path(args.app_dir)
            new_dir = Path(args.new_dir)
            _replace_portable(app_dir, new_dir)
            _start_new(app_dir, args.exe_name)
    except Exception as exc:  # noqa: BLE001
        _log(f"replacement failed: {exc}")
        return 1

    _log("replacement completed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
