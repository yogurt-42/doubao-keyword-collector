from pathlib import Path
from typing import Any

import pytest

from doubao2api.account_manager import (
    BrowserAccountPool,
    is_valid_account_id,
    normalize_account_id,
)
from doubao2api.config import RuntimeConfig, SettingsStore


def test_account_id_validation() -> None:
    assert is_valid_account_id("default")
    assert is_valid_account_id("工作号-A")
    assert not is_valid_account_id("../escape")
    assert not is_valid_account_id("-starts-with-dash")
    with pytest.raises(ValueError):
        normalize_account_id("../escape")


@pytest.mark.asyncio
async def test_environment_lifecycle(tmp_path: Path) -> None:
    store = SettingsStore(tmp_path)
    pool = BrowserAccountPool(store, RuntimeConfig(open_admin_browser=False))

    path = pool.ensure_account_environment("account-a")
    assert path.exists()
    assert path.parent == (tmp_path / "accounts").resolve()

    pool.set_category("account-a", "主力")
    assert store.settings.account_categories["account-a"] == "主力"

    renamed = await pool.rename_account("account-a", "account-b")
    assert renamed == "account-b"
    assert pool.get_user_data_path("account-b").exists()
    assert not pool.get_user_data_path("account-a").exists()

    await pool.delete_account("account-b")
    assert not pool.get_user_data_path("account-b").exists()


@pytest.mark.asyncio
async def test_default_account_cannot_be_deleted(tmp_path: Path) -> None:
    pool = BrowserAccountPool(
        SettingsStore(tmp_path),
        RuntimeConfig(open_admin_browser=False),
    )
    with pytest.raises(ValueError):
        await pool.delete_account("default")


@pytest.mark.asyncio
async def test_snapshot_failure_backoff(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = SettingsStore(tmp_path)
    pool = BrowserAccountPool(store, RuntimeConfig(open_admin_browser=False))
    pool.ensure_account_environment("account-a")
    monkeypatch.setattr(pool, "discover_account_ids", lambda: ["account-a"])

    call_count = 0

    async def failing_snapshot(account_id: str) -> dict[str, Any]:
        nonlocal call_count
        call_count += 1
        raise TimeoutError("snapshot timeout")

    monkeypatch.setattr(pool, "snapshot", failing_snapshot)

    current_time = [0.0]

    def fake_monotonic() -> float:
        return current_time[0]

    monkeypatch.setattr("doubao2api.account_manager.time.monotonic", fake_monotonic)

    def by_account(results: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
        return {item["account_id"]: item for item in results}

    first = by_account(await pool.snapshots())["account-a"]
    assert "snapshot timeout" in first["snapshot_error"]
    assert call_count == 1

    # 仍在 5 秒退避期内，不应再调用 snapshot
    current_time[0] = 4.0
    second = by_account(await pool.snapshots())["account-a"]
    assert "snapshot timeout" in second["snapshot_error"]
    assert call_count == 1

    # 超过退避期，允许再次尝试并继续累加失败计数
    current_time[0] = 6.0
    third = by_account(await pool.snapshots())["account-a"]
    assert "snapshot timeout" in third["snapshot_error"]
    assert call_count == 2

    async def success_snapshot(account_id: str) -> dict[str, Any]:
        nonlocal call_count
        call_count += 1
        return {"account_id": account_id, "snapshot_error": ""}

    monkeypatch.setattr(pool, "snapshot", success_snapshot)
    current_time[0] = 25.0
    fourth = by_account(await pool.snapshots())["account-a"]
    assert fourth["snapshot_error"] == ""
    assert call_count == 3
