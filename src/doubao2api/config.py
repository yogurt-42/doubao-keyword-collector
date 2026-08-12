from __future__ import annotations

import json
import os
import tempfile
from contextlib import suppress
from dataclasses import asdict, dataclass, fields
from pathlib import Path
from typing import Any

DEFAULT_BROWSER_CHANNEL = "msedge" if os.name == "nt" else ""


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def default_data_root() -> Path:
    configured = os.getenv("DOUBAO_DATA_ROOT")
    if configured:
        return Path(configured).expanduser().resolve()
    if os.name == "nt":
        base = Path(os.getenv("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
        return (base / "DoubaoAccountManager").resolve()
    data_home = Path(os.getenv("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    return (data_home / "doubao-account-manager").resolve()


@dataclass(slots=True)
class Settings:
    return_no_watermark_video: bool = True
    image_upload_force_upload: bool = True
    default_account_id: str = "default"
    auto_start_all_accounts: bool = False
    auto_start_account_categories: list[str] | None = None
    auto_replenish_accounts: bool = False
    auto_replenish_account_categories: list[str] | None = None
    account_categories: dict[str, str] | None = None
    account_tab_hidden: dict[str, bool] | None = None
    video_daily_credits: int = 10
    video_15s_credit_cost: int = 4
    video_10s_credit_cost: int = 3
    video_5s_credit_cost: int = 2

    def __post_init__(self) -> None:
        self.auto_start_account_categories = list(self.auto_start_account_categories or [])
        self.auto_replenish_account_categories = list(self.auto_replenish_account_categories or [])
        self.account_categories = dict(self.account_categories or {})
        self.account_tab_hidden = dict(self.account_tab_hidden or {})
        self.video_daily_credits = max(0, int(self.video_daily_credits))
        self.video_15s_credit_cost = max(0, int(self.video_15s_credit_cost))
        self.video_10s_credit_cost = max(0, int(self.video_10s_credit_cost))
        self.video_5s_credit_cost = max(0, int(self.video_5s_credit_cost))

    def public_dict(self) -> dict[str, Any]:
        return asdict(self)


class SettingsStore:
    def __init__(self, data_root: Path | None = None) -> None:
        self.data_root = (data_root or default_data_root()).resolve()
        self.path = self.data_root / "settings.json"
        self.data_root.mkdir(parents=True, exist_ok=True)
        self.settings = self.load()

    def load(self) -> Settings:
        if not self.path.exists():
            return Settings()
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return Settings()
        allowed = {field.name for field in fields(Settings)}
        return Settings(**{key: value for key, value in raw.items() if key in allowed})

    def update(self, values: dict[str, Any]) -> Settings:
        allowed = {field.name for field in fields(Settings)}
        current = self.settings.public_dict()
        for key, value in values.items():
            if key in allowed and value is not None:
                current[key] = value
        self.settings = Settings(**current)
        self.save()
        return self.settings

    def save(self) -> None:
        self.data_root.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(
            self.settings.public_dict(),
            ensure_ascii=False,
            indent=2,
        )
        handle, temp_name = tempfile.mkstemp(
            prefix="settings-",
            suffix=".tmp",
            dir=self.data_root,
            text=True,
        )
        try:
            with os.fdopen(handle, "w", encoding="utf-8") as stream:
                stream.write(payload)
            os.replace(temp_name, self.path)
        finally:
            with suppress(FileNotFoundError):
                os.unlink(temp_name)


@dataclass(slots=True)
class RuntimeConfig:
    host: str = "127.0.0.1"
    port: int = 9090
    headless: bool = False
    browser_channel: str = DEFAULT_BROWSER_CHANNEL
    browser_executable_path: str = ""
    open_admin_browser: bool = True
    api_key: str = ""

    @classmethod
    def from_env(cls) -> RuntimeConfig:
        return cls(
            host=os.getenv("DOUBAO_HOST", "127.0.0.1"),
            port=int(os.getenv("DOUBAO_PORT", "9090")),
            headless=_env_bool("DOUBAO_HEADLESS", False),
            browser_channel=os.getenv("DOUBAO_BROWSER_CHANNEL", DEFAULT_BROWSER_CHANNEL).strip(),
            browser_executable_path=os.getenv("DOUBAO_BROWSER_EXECUTABLE_PATH", "").strip(),
            open_admin_browser=_env_bool("DOUBAO_OPEN_ADMIN_BROWSER", True),
            api_key=os.getenv("DOUBAO_API_KEY", "").strip(),
        )
