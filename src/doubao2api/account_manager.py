from __future__ import annotations

import asyncio
import json
import logging
import shutil
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from .browser_client import BrowserClient
from .config import RuntimeConfig, SettingsStore
from .platforms import DEFAULT_PLATFORM_KEY

LOGGER = logging.getLogger(__name__)

DEFAULT_ACCOUNT_ID = "default"
ACCOUNT_ID_MAX_LENGTH = 64
ACCOUNT_ID_EXTRA_CHARS = "._-"
ACCOUNT_BROWSER_CONFIG_FILENAME = ".doubao-browser.json"
ACCOUNT_CONFIG_FILENAME = ".account-config.json"

CACHE_CLEAR_DIRECTORY_NAMES = {
    "cache",
    "code cache",
    "gpucache",
    "dawncache",
    "graphitecache",
    "grshadercache",
    "shadercache",
    "mediacache",
    "crashpad",
    "blob_storage",
    "webstorage",
    "service worker",
}
CACHE_CLEAR_FILE_NAMES = {
    "history",
    "history-journal",
    "history-shm",
    "history-wal",
    "favicons",
    "favicons-journal",
    "favicons-shm",
    "favicons-wal",
}


def _is_cjk(char: str) -> bool:
    return "\u3400" <= char <= "\u9fff"


def is_valid_account_id(value: str) -> bool:
    if not 1 <= len(value) <= ACCOUNT_ID_MAX_LENGTH:
        return False
    if not (value[0].isalnum() or _is_cjk(value[0])):
        return False
    return all(char.isalnum() or _is_cjk(char) or char in ACCOUNT_ID_EXTRA_CHARS for char in value)


def normalize_account_id(value: str | None, default: str = DEFAULT_ACCOUNT_ID) -> str:
    normalized = (value or default).strip()
    if not is_valid_account_id(normalized):
        raise ValueError(
            "Invalid account_id. Use 1-64 letters, digits, Chinese characters, "
            "dot, underscore or hyphen."
        )
    return normalized


@dataclass(slots=True)
class ManagedBrowserAccount:
    account_id: str
    user_data_dir: Path
    client: BrowserClient
    ai_platform: str = DEFAULT_PLATFORM_KEY


class BrowserAccountPool:
    def __init__(
        self,
        store: SettingsStore,
        runtime: RuntimeConfig,
        client_factory: Callable[[Path, str, RuntimeConfig, str], Any] | None = None,
        runtime_store: Any | None = None,
    ) -> None:
        self.store = store
        self.runtime = runtime
        self.runtime_store = runtime_store
        self.default_account_id = store.settings.default_account_id
        self.accounts_root = (store.data_root / "accounts").resolve()
        self.accounts_root.mkdir(parents=True, exist_ok=True)
        self.client_factory = client_factory
        self._managed: dict[str, ManagedBrowserAccount] = {}
        self._starting: dict[str, asyncio.Task[ManagedBrowserAccount]] = {}
        self._stopping: set[str] = set()
        self._snapshot_failures: dict[str, int] = {}
        self._snapshot_last_attempt: dict[str, float] = {}
        self._snapshot_last_error: dict[str, str] = {}
        self._config_cache: dict[str, tuple[dict[str, Any], float, float]] = {}
        self._platform_cache: dict[str, tuple[str, float]] = {}
        self._shutdown = False
        self._lock = asyncio.Lock()

    def get_user_data_path(self, account_id: str | None) -> Path:
        normalized = normalize_account_id(account_id, self.default_account_id)
        path = (self.accounts_root / normalized).resolve()
        if path.parent != self.accounts_root:
            raise ValueError("Account path escapes the managed accounts directory")
        return path

    def ensure_account_environment(self, account_id: str | None) -> Path:
        path = self.get_user_data_path(account_id)
        path.mkdir(parents=True, exist_ok=True)
        return path

    def discover_account_ids(self, platform: str | None = None) -> list[str]:
        discovered = {self.default_account_id}
        for child in self.accounts_root.iterdir():
            if (
                child.is_dir()
                and not child.is_symlink()
                and is_valid_account_id(child.name)
                and (platform is None or self.get_account_platform(child.name) == platform)
            ):
                discovered.add(child.name)
        return sorted(discovered, key=lambda value: (value != self.default_account_id, value))

    def _build_client(self, account_id: str) -> Any:
        platform = self.get_account_platform(account_id)
        if self.client_factory is not None:
            return self.client_factory(
                self.get_user_data_path(account_id),
                account_id,
                self.runtime,
                platform,
            )
        return BrowserClient(
            self.get_user_data_path(account_id),
            account_id=account_id,
            headless=self.runtime.headless,
            browser_channel=self.runtime.browser_channel,
            browser_executable_path=self.runtime.browser_executable_path,
            platform=platform,
        )

    def _account_config_path(self, account_id: str) -> Path:
        path = self.get_user_data_path(account_id)
        new_path = path / ACCOUNT_CONFIG_FILENAME
        if new_path.exists():
            return new_path
        old_path = path / ACCOUNT_BROWSER_CONFIG_FILENAME
        if old_path.exists():
            return old_path
        return new_path

    def get_account_platform(self, account_id: str | None) -> str:
        normalized = normalize_account_id(account_id, self.default_account_id)
        now = time.monotonic()
        cached = self._platform_cache.get(normalized)
        if cached is not None and now - cached[1] < 5.0:
            return cached[0]
        platform = self._resolve_account_platform(normalized)
        self._platform_cache[normalized] = (platform, now)
        return platform

    def _resolve_account_platform(self, account_id: str) -> str:
        config = self.account_browser_config(account_id)
        platform = config.get("ai_platform", "")
        if platform:
            return platform
        platform = self.store.settings.account_platforms.get(account_id, "")
        if platform:
            return platform
        return self.store.settings.default_ai_platform or DEFAULT_PLATFORM_KEY

    def set_account_platform(self, account_id: str, platform: str) -> None:
        normalized = normalize_account_id(account_id, self.default_account_id)
        path = self.ensure_account_environment(normalized) / ACCOUNT_CONFIG_FILENAME
        config = self.account_browser_config(normalized)
        old_platform = config.get("ai_platform", "")
        config["ai_platform"] = platform
        path.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
        self.store.settings.account_platforms[normalized] = platform
        self.store.save()
        self._clear_account_caches(normalized)
        LOGGER.info(
            "Set platform for account %s: %s -> %s (config=%s)",
            normalized,
            old_platform or "(unset)",
            platform,
            path,
        )

    async def start_account(self, account_id: str | None) -> ManagedBrowserAccount:
        normalized = normalize_account_id(account_id, self.default_account_id)
        async with self._lock:
            existing = self._managed.get(normalized)
            if existing:
                return existing
            task = self._starting.get(normalized)
            if task is None:
                task = asyncio.create_task(self._start_account_inner(normalized))
                self._starting[normalized] = task
        try:
            return await task
        finally:
            async with self._lock:
                self._starting.pop(normalized, None)

    async def _start_account_inner(self, account_id: str) -> ManagedBrowserAccount:
        path = self.ensure_account_environment(account_id)
        platform = self.get_account_platform(account_id)
        LOGGER.info("Starting account %s with platform %s", account_id, platform)
        client = self._build_client(account_id)
        await client.start()
        platform = self.get_account_platform(account_id)
        managed = ManagedBrowserAccount(account_id, path, client, ai_platform=platform)
        async with self._lock:
            self._managed[account_id] = managed
        self._update_startup_state(account_id, started=True)
        return managed

    async def stop_account(self, account_id: str | None) -> bool:
        normalized = normalize_account_id(account_id, self.default_account_id)
        async with self._lock:
            self._stopping.add(normalized)
            managed = self._managed.pop(normalized, None)
            starting = self._starting.get(normalized)
        try:
            if starting:
                starting.cancel()
            if managed:
                await managed.client.stop()
                return True
            return False
        finally:
            async with self._lock:
                self._stopping.discard(normalized)
            if not self._shutdown:
                self._update_startup_state(normalized, started=False)

    async def stop_all(self) -> None:
        for account_id in list(self._managed):
            await self.stop_account(account_id)

    def get_if_started(self, account_id: str | None) -> ManagedBrowserAccount | None:
        return self._managed.get(normalize_account_id(account_id, self.default_account_id))

    async def get_or_start(self, account_id: str | None) -> ManagedBrowserAccount:
        return await self.start_account(account_id)

    async def reset_account_environment(self, account_id: str) -> None:
        normalized = normalize_account_id(account_id, self.default_account_id)
        if normalized == self.default_account_id:
            raise ValueError("The default account environment cannot be reset")
        await self.stop_account(normalized)
        path = self.get_user_data_path(normalized)
        if path.is_symlink():
            raise ValueError("Refusing to reset a symlinked account environment")
        if path.exists():
            shutil.rmtree(path)
        path.mkdir(parents=True)
        self._clear_account_caches(normalized)

    async def delete_account(self, account_id: str) -> None:
        normalized = normalize_account_id(account_id, self.default_account_id)
        if normalized == self.default_account_id:
            raise ValueError("The default account cannot be deleted")
        await self.stop_account(normalized)
        path = self.get_user_data_path(normalized)
        if path.is_symlink():
            raise ValueError("Refusing to delete a symlinked account environment")
        if path.exists():
            shutil.rmtree(path)
        self.store.settings.account_categories.pop(normalized, None)
        self.store.settings.account_tab_hidden.pop(normalized, None)
        self.store.settings.account_platforms.pop(normalized, None)
        self.store.save()
        self._clear_account_caches(normalized)

    async def rename_account(self, account_id: str, new_account_id: str) -> str:
        old = normalize_account_id(account_id, self.default_account_id)
        new = normalize_account_id(new_account_id, self.default_account_id)
        if old == self.default_account_id or new == self.default_account_id:
            raise ValueError("The default account cannot be renamed or replaced")
        if old == new:
            return new
        await self.stop_account(old)
        source, target = self.get_user_data_path(old), self.get_user_data_path(new)
        if target.exists():
            raise ValueError(f"Target account '{new}' already exists")
        if source.exists():
            source.rename(target)
        category = self.store.settings.account_categories.pop(old, "")
        if category:
            self.store.settings.account_categories[new] = category
        tab_hidden = self.store.settings.account_tab_hidden.pop(old, False)
        if tab_hidden:
            self.store.settings.account_tab_hidden[new] = True
        platform = self.store.settings.account_platforms.pop(old, "")
        if platform:
            self.store.settings.account_platforms[new] = platform
        self.store.save()
        self._clear_account_caches(old)
        self._clear_account_caches(new)
        return new

    async def clear_account_cache(self, account_id: str) -> dict[str, Any]:
        normalized = normalize_account_id(account_id, self.default_account_id)
        self._clear_account_caches(normalized)
        await self.stop_account(normalized)
        root = self.get_user_data_path(normalized)
        deleted: list[str] = []
        failed: list[str] = []
        if not root.exists():
            return {"deleted_path_count": 0, "deleted_paths": [], "failed_paths": []}
        for path in sorted(root.rglob("*"), key=lambda item: len(item.parts), reverse=True):
            relative = path.relative_to(root).as_posix()
            should_remove = (
                path.is_dir() and path.name.casefold() in CACHE_CLEAR_DIRECTORY_NAMES
            ) or (path.is_file() and path.name.casefold() in CACHE_CLEAR_FILE_NAMES)
            if not should_remove:
                continue
            try:
                if path.is_dir():
                    shutil.rmtree(path)
                else:
                    path.unlink()
                deleted.append(relative)
            except OSError:
                failed.append(relative)
        return {
            "deleted_path_count": len(deleted),
            "deleted_paths": deleted,
            "failed_paths": failed,
        }

    def set_category(self, account_id: str, category: str | None) -> None:
        normalized = normalize_account_id(account_id, self.default_account_id)
        cleaned = (category or "").strip()
        if cleaned:
            self.store.settings.account_categories[normalized] = cleaned
        else:
            self.store.settings.account_categories.pop(normalized, None)
        self.store.save()

    def is_tab_hidden(self, account_id: str) -> bool:
        normalized = normalize_account_id(account_id, self.default_account_id)
        return bool(self.store.settings.account_tab_hidden.get(normalized, False))

    def set_tab_hidden(self, account_id: str, hidden: bool) -> None:
        normalized = normalize_account_id(account_id, self.default_account_id)
        if hidden:
            self.store.settings.account_tab_hidden[normalized] = True
        else:
            self.store.settings.account_tab_hidden.pop(normalized, None)
        self.store.save()
        self._update_startup_state(normalized, hidden=hidden)

    def account_browser_config(self, account_id: str) -> dict[str, Any]:
        normalized = normalize_account_id(account_id, self.default_account_id)
        path = self._account_config_path(normalized)
        if not path.exists():
            self._config_cache.pop(normalized, None)
            return {}
        try:
            stat = path.stat()
            mtime = stat.st_mtime
        except OSError:
            self._config_cache.pop(normalized, None)
            return {}
        now = time.monotonic()
        cached = self._config_cache.get(normalized)
        if cached is not None and cached[1] == mtime and now - cached[2] < 5.0:
            return cached[0]
        try:
            config = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            config = {}
        self._config_cache[normalized] = (config, mtime, now)
        return config

    def _clear_account_caches(self, account_id: str) -> None:
        normalized = normalize_account_id(account_id, self.default_account_id)
        self._config_cache.pop(normalized, None)
        self._platform_cache.pop(normalized, None)

    def _update_startup_state(
        self,
        account_id: str,
        *,
        started: bool | None = None,
        hidden: bool | None = None,
    ) -> None:
        normalized = normalize_account_id(account_id, self.default_account_id)
        state = self.store.settings.account_startup_states.get(normalized, {})
        if started is not None:
            state["started"] = started
        if hidden is not None:
            state["hidden"] = hidden
        if state:
            self.store.settings.account_startup_states[normalized] = state
        else:
            self.store.settings.account_startup_states.pop(normalized, None)
        self.store.save()

    @staticmethod
    def _snapshot_backoff_seconds(failures: int) -> int:
        tiers = [0, 5, 15, 30, 60, 120, 300]
        return tiers[min(failures, len(tiers) - 1)]

    def _snapshot_error_state(self, account_id: str, error: str) -> dict[str, Any]:
        managed = self._managed.get(account_id)
        runtime = (
            self.runtime_store.account_runtime(account_id) if self.runtime_store is not None else {}
        )
        paused_until = runtime.get("paused_until") or ""
        pause_reason = runtime.get("pause_reason") or ""
        is_paused = False
        if paused_until:
            try:
                is_paused = datetime.now().astimezone() < datetime.fromisoformat(paused_until)
            except ValueError:
                is_paused = False
        return {
            "account_id": account_id,
            "is_default": account_id == self.default_account_id,
            "user_data_dir": str(self.get_user_data_path(account_id)),
            "category": self.store.settings.account_categories.get(account_id, ""),
            "tab_hidden": bool(self.store.settings.account_tab_hidden.get(account_id, False)),
            "environment_exists": self.get_user_data_path(account_id).exists(),
            "can_delete": account_id != self.default_account_id,
            "can_reset_environment": account_id != self.default_account_id,
            "started": managed is not None,
            "starting": account_id in self._starting,
            "stopping": account_id in self._stopping,
            "logged_in": False,
            "chat_ready": False,
            "has_ms_token": False,
            "needs_captcha": False,
            "ai_platform": self.get_account_platform(account_id),
            "paused_until": paused_until,
            "pause_reason": pause_reason,
            "is_paused": is_paused,
            "last_error_code": 0,
            "consecutive_failures": 0,
            "manually_stopped": False,
            "video_quota_limit": self.store.settings.video_daily_credits,
            "video_quota_used": 0,
            "video_quota_committed_used": 0,
            "video_quota_reserved": 0,
            "video_quota_remaining": self.store.settings.video_daily_credits,
            "video_quota_exhausted": False,
            "video_quota_external_exhausted": False,
            "video_generation_calls_today": 0,
            "video_quota_day": "",
            "video_quota_updated_at": 0,
            "image_generation_inflight": 0,
            "video_generation_inflight": 0,
            "route_call_count": 0,
            "start_error": "",
            "snapshot_error": error,
        }

    async def snapshot(self, account_id: str) -> dict[str, Any]:
        normalized = normalize_account_id(account_id, self.default_account_id)
        path = self.get_user_data_path(normalized)
        managed = self._managed.get(normalized)
        state = (
            await managed.client.inspect_session_state()
            if managed
            else {
                "started": False,
                "logged_in": False,
                "chat_ready": False,
                "has_ms_token": False,
                "needs_captcha": False,
                "last_error_code": 0,
                "consecutive_failures": 0,
            }
        )
        runtime = (
            self.runtime_store.account_runtime(normalized) if self.runtime_store is not None else {}
        )
        paused_until = runtime.get("paused_until") or ""
        pause_reason = runtime.get("pause_reason") or ""
        is_paused = False
        if paused_until:
            try:
                is_paused = datetime.now().astimezone() < datetime.fromisoformat(paused_until)
            except ValueError:
                is_paused = False
        daily_limit = self.store.settings.video_daily_credits
        return {
            "account_id": normalized,
            "is_default": normalized == self.default_account_id,
            "user_data_dir": str(path),
            "category": self.store.settings.account_categories.get(normalized, ""),
            "tab_hidden": bool(self.store.settings.account_tab_hidden.get(normalized, False)),
            "environment_exists": path.exists(),
            "can_delete": normalized != self.default_account_id,
            "can_reset_environment": normalized != self.default_account_id,
            "started": state["started"],
            "starting": normalized in self._starting,
            "stopping": normalized in self._stopping,
            "logged_in": state["logged_in"],
            "chat_ready": state["chat_ready"],
            "has_ms_token": state["has_ms_token"],
            "needs_captcha": state["needs_captcha"],
            "ai_platform": self.get_account_platform(normalized),
            "paused_until": paused_until,
            "pause_reason": pause_reason,
            "is_paused": is_paused,
            "last_error_code": state["last_error_code"],
            "consecutive_failures": state["consecutive_failures"],
            "manually_stopped": False,
            "video_quota_limit": daily_limit,
            "video_quota_used": 0,
            "video_quota_committed_used": 0,
            "video_quota_reserved": 0,
            "video_quota_remaining": daily_limit,
            "video_quota_exhausted": False,
            "video_quota_external_exhausted": False,
            "video_generation_calls_today": 0,
            "video_quota_day": "",
            "video_quota_updated_at": 0,
            "image_generation_inflight": 0,
            "video_generation_inflight": 0,
            "route_call_count": 0,
            "start_error": "",
            "snapshot_error": "",
        }

    async def snapshots(self) -> list[dict[str, Any]]:
        account_ids = self.discover_account_ids()
        semaphore = asyncio.Semaphore(3)

        async def safe_snapshot(account_id: str) -> dict[str, Any]:
            async with semaphore:
                now = time.monotonic()
                failures = self._snapshot_failures.get(account_id, 0)
                last_attempt = self._snapshot_last_attempt.get(account_id, 0)
                if failures > 0:
                    backoff = self._snapshot_backoff_seconds(failures)
                    if now - last_attempt < backoff:
                        cached_error = self._snapshot_last_error.get(
                            account_id, "账号状态检测退避中"
                        )
                        return self._snapshot_error_state(account_id, cached_error)
                self._snapshot_last_attempt[account_id] = now
                try:
                    result = await asyncio.wait_for(self.snapshot(account_id), timeout=10)
                except Exception as exc:
                    error = str(exc) or "账号状态检测超时"
                    self._snapshot_failures[account_id] = failures + 1
                    self._snapshot_last_error[account_id] = error
                    return self._snapshot_error_state(account_id, error)
                self._snapshot_failures[account_id] = 0
                self._snapshot_last_error.pop(account_id, None)
                return result

        return list(
            await asyncio.gather(*(safe_snapshot(account_id) for account_id in account_ids))
        )
