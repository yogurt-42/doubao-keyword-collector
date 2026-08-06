from __future__ import annotations

import ctypes
import multiprocessing
import os

from doubao2api.config import RuntimeConfig

BACKGROUND_BROWSER_FLAGS = (
    "--disable-background-timer-throttling",
    "--disable-renderer-backgrounding",
    "--disable-backgrounding-occluded-windows",
)


def _configure_background_browser() -> None:
    existing = os.environ.get("QTWEBENGINE_CHROMIUM_FLAGS", "").split()
    for flag in BACKGROUND_BROWSER_FLAGS:
        if flag not in existing:
            existing.append(flag)
    os.environ["QTWEBENGINE_CHROMIUM_FLAGS"] = " ".join(existing)


def _show_error(message: str) -> None:
    ctypes.windll.user32.MessageBoxW(
        0,
        message,
        "豆包关键词资料采集器",
        0x10,
    )


def main() -> None:
    multiprocessing.freeze_support()
    _configure_background_browser()
    runtime = RuntimeConfig.from_env()
    try:
        from doubao2api.desktop import run_desktop

        run_desktop(runtime)
    except Exception as exc:
        _show_error(f"软件启动失败：\n\n{exc}")


if __name__ == "__main__":
    main()
