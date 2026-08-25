from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

from doubao2api.research_scheduler import ResearchScheduler
from doubao2api.research_store import iso_now, local_now


class FakeClient:
    async def inspect_session_state(self) -> dict[str, bool]:
        return {
            "needs_captcha": False,
            "logged_in": True,
            "chat_ready": True,
        }

    async def chat(self, messages: list[dict[str, str]], **_: Any) -> dict[str, Any]:
        await asyncio.sleep(0)
        return {"thinking_references": []}


class FakeAccount:
    client = FakeClient()


class BlockingClient(FakeClient):
    def __init__(self, started: asyncio.Event, release: asyncio.Event) -> None:
        self.started = started
        self.release = release

    async def chat(self, messages: list[dict[str, str]], **_: Any) -> dict[str, Any]:
        self.started.set()
        await self.release.wait()
        return {"thinking_references": []}


class MultiAccountPool:
    def __init__(self) -> None:
        self.store = FakeSettingsStore(auto_start_all_accounts=False)
        self.release = asyncio.Event()
        self.started = {
            "账号1": asyncio.Event(),
            "账号2": asyncio.Event(),
        }
        self.accounts = {
            account_id: type(
                "Account",
                (),
                {"client": BlockingClient(started, self.release)},
            )()
            for account_id, started in self.started.items()
        }

    def discover_account_ids(self) -> list[str]:
        return list(self.accounts)

    def get_if_started(self, account_id: str) -> Any:
        return self.accounts[account_id]


class FakeSettings:
    def __init__(self, auto_start_all_accounts: bool = False) -> None:
        self.auto_start_all_accounts = auto_start_all_accounts


class FakeSettingsStore:
    def __init__(self, auto_start_all_accounts: bool = False) -> None:
        self.settings = FakeSettings(auto_start_all_accounts)


class FakePool:
    def __init__(self, auto_start_all_accounts: bool = False) -> None:
        self.store = FakeSettingsStore(auto_start_all_accounts)

    def discover_account_ids(self) -> list[str]:
        return ["账号1"]

    def get_if_started(self, account_id: str) -> FakeAccount:
        return FakeAccount()


class ClosedAccountPool(FakePool):
    def __init__(self, auto_start_all_accounts: bool = True) -> None:
        super().__init__(auto_start_all_accounts)
        self.start_calls = 0
        self._started: FakeAccount | None = None

    def get_if_started(self, account_id: str) -> FakeAccount | None:
        return self._started

    async def start_account(self, account_id: str) -> FakeAccount:
        self.start_calls += 1
        self._started = FakeAccount()
        return self._started


class FakeStore:
    def __init__(self) -> None:
        self.started: list[str] = []
        self.task_accounts: dict[str, str] = {}
        self.completed: list[str] = []
        self.failed: list[tuple[str, bool]] = []
        self.tasks = [
            {
                "id": "task-1",
                "job_id": "job-1",
                "keyword": "关键词1",
                "prompt_template": "{keyword}",
                "account_ids": [],
                "interval_seconds": 30,
                "max_attempts": 1,
                "attempt_count": 0,
                "scheduled_at": iso_now(),
            },
            {
                "id": "task-2",
                "job_id": "job-1",
                "keyword": "关键词2",
                "prompt_template": "{keyword}",
                "account_ids": [],
                "interval_seconds": 30,
                "max_attempts": 1,
                "attempt_count": 0,
                "scheduled_at": iso_now(),
            },
        ]

    def due_tasks(self, limit: int = 50) -> list[dict[str, Any]]:
        now = iso_now()
        return [
            task
            for task in self.tasks
            if task["id"] not in self.started
            and task["id"] not in self.completed
            and task.get("scheduled_at", "") <= now
        ][:limit]

    def due_schedules(self, limit: int = 50) -> list[dict[str, Any]]:
        return []

    def account_runtime(self, account_id: str) -> dict[str, Any]:
        return {
            "last_used_at": None,
            "paused_until": None,
            "pause_reason": "",
        }

    def resume_account(self, account_id: str) -> None:
        return None

    def mark_task_running(self, task_id: str, account_id: str) -> bool:
        self.started.append(task_id)
        self.task_accounts[task_id] = account_id
        return True

    def update_task_progress(self, task_id: str, message: str) -> None:
        return None

    def mark_account_used(self, account_id: str) -> None:
        return None

    def complete_task(self, task_id: str, **_: Any) -> None:
        self.completed.append(task_id)

    def pause_account(self, account_id: str, seconds: int, reason: str) -> None:
        return None

    def fail_or_retry_task(self, task_id: str, error: str, *, retry: bool) -> None:
        self.failed.append((task_id, retry))


class EmptyPollingStore(FakeStore):
    def __init__(self) -> None:
        super().__init__()
        self.due_calls = 0

    def recover_running_tasks(self) -> None:
        return None

    def due_tasks(self, limit: int = 50) -> list[dict[str, Any]]:
        self.due_calls += 1
        return []


@pytest.mark.asyncio
async def test_scheduler_keeps_polling_after_asyncio_timeout() -> None:
    store = EmptyPollingStore()
    scheduler = ResearchScheduler(store, FakePool())  # type: ignore[arg-type]

    await scheduler.start()
    await asyncio.sleep(2.2)
    await scheduler.stop()

    assert store.due_calls >= 2


@pytest.mark.asyncio
async def test_scheduler_runs_one_keyword_and_respects_second_interval() -> None:
    store = FakeStore()
    # task-2 安排在 30 秒后，模拟真实任务的时间间隔
    store.tasks[1]["scheduled_at"] = (local_now() + timedelta(seconds=30)).isoformat(
        timespec="seconds"
    )
    scheduler = ResearchScheduler(store, FakePool())  # type: ignore[arg-type]

    await scheduler._dispatch_due_tasks()
    assert store.started == ["task-1"]
    await asyncio.gather(*list(scheduler._workers))
    await asyncio.sleep(0)

    # task-2 仍未到时间，不会被派发
    await scheduler._dispatch_due_tasks()
    assert store.started == ["task-1"]

    # 到达 task-2 的计划时间后继续派发
    store.tasks[1]["scheduled_at"] = iso_now()
    await scheduler._dispatch_due_tasks()
    assert store.started == ["task-1", "task-2"]
    await asyncio.gather(*list(scheduler._workers))


@pytest.mark.asyncio
async def test_scheduler_starts_closed_account_automatically() -> None:
    store = FakeStore()
    # 只让 task-1 到期，避免 worker 立即完成后 task-2 也被派发
    store.tasks[1]["scheduled_at"] = (local_now() + timedelta(seconds=30)).isoformat(
        timespec="seconds"
    )
    pool = ClosedAccountPool()
    scheduler = ResearchScheduler(store, pool)  # type: ignore[arg-type]

    await scheduler._dispatch_due_tasks()

    assert pool.start_calls == 1
    assert store.started == ["task-1"]
    await asyncio.gather(*list(scheduler._workers))


@pytest.mark.asyncio
async def test_scheduler_can_overlap_work_on_different_accounts() -> None:
    store = FakeStore()
    # 先只让第一个任务到期，第二个任务随后手动放行
    store.tasks[1]["scheduled_at"] = (local_now() + timedelta(seconds=30)).isoformat(
        timespec="seconds"
    )
    pool = MultiAccountPool()
    scheduler = ResearchScheduler(store, pool)  # type: ignore[arg-type]

    await scheduler._dispatch_due_tasks()
    await asyncio.wait_for(pool.started["账号1"].wait(), timeout=1)

    # 第二个任务到期后，应能立即派发到另一个账号
    store.tasks[1]["scheduled_at"] = iso_now()
    await scheduler._dispatch_due_tasks()
    await asyncio.wait_for(pool.started["账号2"].wait(), timeout=1)

    assert store.started == ["task-1", "task-2"]
    assert scheduler.snapshot()["busy_accounts"] == ["账号1", "账号2"]

    pool.release.set()
    await asyncio.gather(*list(scheduler._workers))


class PausedAccountStore(FakeStore):
    def __init__(self) -> None:
        super().__init__()
        self.paused_until = (datetime.now(timezone.utc) + timedelta(seconds=60)).isoformat()

    def account_runtime(self, account_id: str) -> dict[str, Any]:
        return {
            "last_used_at": None,
            "paused_until": self.paused_until,
            "pause_reason": "账号尚未登录",
        }


@pytest.mark.asyncio
async def test_scheduler_skips_paused_account() -> None:
    store = PausedAccountStore()
    pool = FakePool()
    scheduler = ResearchScheduler(store, pool)  # type: ignore[arg-type]

    selected = await scheduler._select_account(store.tasks[0])

    assert selected is None


@pytest.mark.asyncio
async def test_scheduler_cancels_running_worker() -> None:
    store = FakeStore()
    pool = MultiAccountPool()
    scheduler = ResearchScheduler(store, pool)  # type: ignore[arg-type]

    await scheduler._dispatch_due_tasks()
    await asyncio.wait_for(pool.started["账号1"].wait(), timeout=1)

    scheduler.cancel_job("job-1")
    pool.release.set()
    await asyncio.gather(*list(scheduler._workers))

    assert ("task-1", False) in store.failed
    assert "task-1" not in store.completed


class ThreeAccountPool:
    def __init__(self) -> None:
        self.store = FakeSettingsStore(auto_start_all_accounts=False)
        self.release = asyncio.Event()
        self.started = {account_id: asyncio.Event() for account_id in ["账号1", "账号2", "账号3"]}
        self.accounts = {
            account_id: type(
                "Account",
                (),
                {"client": BlockingClient(started, self.release)},
            )()
            for account_id, started in self.started.items()
        }

    def discover_account_ids(self) -> list[str]:
        return list(self.accounts)

    def get_if_started(self, account_id: str) -> Any:
        return self.accounts[account_id]


@pytest.mark.asyncio
async def test_scheduler_dispatches_multiple_accounts_in_one_loop() -> None:
    store = FakeStore()
    pool = ThreeAccountPool()
    scheduler = ResearchScheduler(store, pool)  # type: ignore[arg-type]

    # 让三个任务同时到期
    store.tasks = []
    for index in range(3):
        store.tasks.append(
            {
                "id": f"task-{index + 1}",
                "job_id": "job-1",
                "keyword": f"关键词{index + 1}",
                "prompt_template": "{keyword}",
                "account_ids": [],
                "interval_seconds": 30,
                "max_attempts": 1,
                "attempt_count": 0,
                "scheduled_at": iso_now(),
            }
        )

    await scheduler._dispatch_due_tasks()

    for account_id in pool.started:
        await asyncio.wait_for(pool.started[account_id].wait(), timeout=2)

    assert sorted(store.started) == ["task-1", "task-2", "task-3"]
    assert scheduler.snapshot()["active_workers"] == 3

    pool.release.set()
    await asyncio.gather(*list(scheduler._workers))


class CaptchaClient(FakeClient):
    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self._needs_captcha = False

    async def chat(self, messages: list[dict[str, str]], **_: Any) -> dict[str, Any]:
        self.started.set()
        await self.release.wait()
        return {"thinking_references": []}


class CaptchaStore(FakeStore):
    def __init__(self) -> None:
        super().__init__()
        self.pause_calls: list[tuple[str, int, str]] = []

    def pause_account(self, account_id: str, seconds: int, reason: str) -> None:
        self.pause_calls.append((account_id, seconds, reason))


class CaptchaAccountPool:
    def __init__(self) -> None:
        self.store = FakeSettingsStore(auto_start_all_accounts=False)
        self.client = CaptchaClient()
        self.account = type("Account", (), {"client": self.client})()

    def discover_account_ids(self) -> list[str]:
        return ["账号1"]

    def get_if_started(self, account_id: str) -> Any:
        return self.account


@pytest.mark.asyncio
async def test_scheduler_pauses_account_when_captcha_detected() -> None:
    store = CaptchaStore()
    pool = CaptchaAccountPool()
    scheduler = ResearchScheduler(store, pool)  # type: ignore[arg-type]
    callback_calls: list[str] = []
    scheduler.on_captcha_callback = callback_calls.append

    await scheduler._dispatch_due_tasks()
    await asyncio.wait_for(pool.client.started.wait(), timeout=1)

    # Simulate the browser client detecting a captcha challenge.
    pool.client._needs_captcha = True
    await asyncio.sleep(3.5)

    assert any(call == ("账号1", 1800, "检测到人机验证，请人工处理") for call in store.pause_calls)
    assert "账号1" in callback_calls

    # After the user solves the challenge, the original chat resumes.
    pool.client._needs_captcha = False
    pool.client.release.set()
    await asyncio.gather(*list(scheduler._workers))

    assert "task-1" in store.completed


@pytest.mark.asyncio
async def test_scheduler_releases_account_after_task_timeout(monkeypatch) -> None:
    monkeypatch.setattr("doubao2api.research_scheduler.TASK_TIMEOUT_SECONDS", 2)
    store = CaptchaStore()
    pool = CaptchaAccountPool()
    scheduler = ResearchScheduler(store, pool)  # type: ignore[arg-type]

    await scheduler._dispatch_due_tasks()
    await asyncio.wait_for(pool.client.started.wait(), timeout=1)

    # Allow the task to hit the overall timeout without solving the captcha.
    await asyncio.gather(*list(scheduler._workers))

    assert ("task-1", False) in store.failed
    assert "账号1" not in scheduler._busy_accounts


class LRUAccountPool:
    def __init__(self) -> None:
        self.store = FakeSettingsStore(auto_start_all_accounts=False)
        self.accounts = {account_id: FakeAccount() for account_id in ["账号1", "账号2", "账号3"]}

    def discover_account_ids(self) -> list[str]:
        return list(self.accounts)

    def get_if_started(self, account_id: str) -> Any:
        return self.accounts[account_id]


class LRUStore(FakeStore):
    def __init__(self) -> None:
        super().__init__()
        self.runtimes = {
            "账号1": {
                "last_used_at": (local_now() - timedelta(minutes=10)).isoformat(),
                "paused_until": None,
                "pause_reason": "",
            },
            "账号2": {
                "last_used_at": (local_now() - timedelta(minutes=5)).isoformat(),
                "paused_until": None,
                "pause_reason": "",
            },
            "账号3": {
                "last_used_at": iso_now(),
                "paused_until": None,
                "pause_reason": "",
            },
        }

    def account_runtime(self, account_id: str) -> dict[str, Any]:
        return self.runtimes.get(
            account_id,
            {"last_used_at": None, "paused_until": None, "pause_reason": ""},
        )

    def mark_account_used(self, account_id: str) -> None:
        self.runtimes[account_id]["last_used_at"] = iso_now()


@pytest.mark.asyncio
async def test_scheduler_selects_longest_idle_account_first() -> None:
    store = LRUStore()
    pool = LRUAccountPool()
    scheduler = ResearchScheduler(store, pool)  # type: ignore[arg-type]

    # 让两个任务同时到期
    store.tasks = [
        {
            "id": "task-1",
            "job_id": "job-1",
            "keyword": "关键词1",
            "prompt_template": "{keyword}",
            "account_ids": [],
            "interval_seconds": 30,
            "max_attempts": 1,
            "attempt_count": 0,
            "scheduled_at": iso_now(),
        },
        {
            "id": "task-2",
            "job_id": "job-1",
            "keyword": "关键词2",
            "prompt_template": "{keyword}",
            "account_ids": [],
            "interval_seconds": 30,
            "max_attempts": 1,
            "attempt_count": 0,
            "scheduled_at": iso_now(),
        },
    ]

    await scheduler._dispatch_due_tasks()
    await asyncio.gather(*list(scheduler._workers))
    await asyncio.sleep(0)

    assert store.task_accounts.get("task-1") == "账号1"
    assert store.task_accounts.get("task-2") == "账号2"
