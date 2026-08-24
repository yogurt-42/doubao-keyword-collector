from __future__ import annotations

import ctypes
import logging
import multiprocessing
import os

from doubao2api.config import RuntimeConfig, configure_logging

BACKGROUND_BROWSER_FLAGS = (
    "--disable-background-timer-throttling",
    "--disable-renderer-backgrounding",
    "--disable-backgrounding-occluded-windows",
    "--disable-webrtc",
    "--disable-features=WebRtcHideLocalIpsWithMdns,WebRTC",
    "--webrtc-ip-handling-policy=disable_non_proxied_udp",
    "--log-level=3",
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
    log_path = configure_logging()
    _configure_background_browser()
    runtime = RuntimeConfig.from_env()
    try:
        from doubao2api.desktop import run_desktop

        run_desktop(runtime)
    except Exception as exc:
        logging.exception("Desktop launch failed")
        _show_error(f"软件启动失败：\n\n{exc}\n\n日志：{log_path}")


if __name__ == "__main__":
    main()
