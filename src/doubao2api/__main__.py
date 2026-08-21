from __future__ import annotations

import argparse
import logging

from .config import RuntimeConfig, configure_logging
from .server import run_server


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="doubao-account-manager",
        description="Start the open-source Doubao multi-account manager.",
    )
    parser.add_argument("--host", default=None, help="API bind host")
    parser.add_argument("--port", default=None, type=int, help="API bind port")
    parser.add_argument(
        "--headless",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Run account browsers without visible windows",
    )
    parser.add_argument("--browser-channel", default=None)
    parser.add_argument("--browser-executable-path", default=None)
    parser.add_argument(
        "--open-admin-browser",
        action=argparse.BooleanOptionalAction,
        default=None,
    )
    return parser


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    runtime = RuntimeConfig.from_env()
    if args.host is not None:
        runtime.host = args.host
    if args.port is not None:
        runtime.port = args.port
    if args.headless is not None:
        runtime.headless = args.headless
    if args.browser_channel is not None:
        runtime.browser_channel = args.browser_channel
    if args.browser_executable_path is not None:
        runtime.browser_executable_path = args.browser_executable_path
    if args.open_admin_browser is not None:
        runtime.open_admin_browser = args.open_admin_browser
    log_path = configure_logging(data_root=runtime.data_root)
    logging.info("Server log file: %s", log_path)
    run_server(runtime)


if __name__ == "__main__":
    main()
