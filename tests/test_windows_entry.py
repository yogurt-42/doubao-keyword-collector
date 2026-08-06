import os

from doubao2api.windows_entry import (
    BACKGROUND_BROWSER_FLAGS,
    _configure_background_browser,
)


def test_background_browser_flags_are_added_once(monkeypatch) -> None:
    monkeypatch.setenv("QTWEBENGINE_CHROMIUM_FLAGS", "--existing-flag")

    _configure_background_browser()
    _configure_background_browser()

    flags = os.environ["QTWEBENGINE_CHROMIUM_FLAGS"].split()
    assert "--existing-flag" in flags
    for flag in BACKGROUND_BROWSER_FLAGS:
        assert flags.count(flag) == 1
