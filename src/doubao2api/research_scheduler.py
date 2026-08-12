from __future__ import annotations

import asyncio
import contextlib
from datetime import datetime
from typing import Any

from .account_manager import BrowserAccountPool
from .browser_client import ReferenceExpansionError
from .research_links import normalize_thinking_references
from .research_store import ResearchStore, _compute_next_run, local_now

RISK_MARKERS = (
    "captcha",
    "risk",
    "verify",
    "verification",
    "风控",
    "验证码",
    "验证",
    "访问频繁",
    "操作频繁",
    "429",
    "403",
)
ACCOUNT_PROBE_TIMEOUT_SECONDS = 8
ACCOUNT_START_TIMEOUT_SECONDS = 15
ACCOUNT_STARTUP_GRACE_SECONDS = 15
TASK_TIMEOUT_SECONDS = 300
DISPATCH_STAGGER_SECONDS = 0.3


def _datetime_or_none(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed.astimezone() if parsed.tzinfo else parsed.astimezone()


def _is_risk_error(error: BaseException) -> bool:
    text = str(error).casefold()
    return any(marker in text for marker in RISK_MARKERS)


class ResearchScheduler:
    """Dispatch due keywords to isolated, logged-in browser profiles."""

    def __init__(self, store: ResearchStore, account_pool: BrowserAccountPool) -> None:
        self.store = store
        self.account_pool = account_pool
        self._loop_task: asyncio.Task[None] | None = None
        self._workers: set[asyncio.Task[None]] = set()
        self._busy_accounts: set[str] = set()
        self._cancelled_jobs: set[str] = set()
        self._stopping = False
        self._wake_event = asyncio.Event()
        self._selection_wait_reason = ""
        self.last_error = ""

    def cancel_job(self, job_id: str) -> None:
        """Signal active workers for this job to stop early."""

        self._cancelled_jobs.add(job_id)
        self.wake()

    def _is_job_cancelled(self, job_id: str) -> bool:
        return job_id in self._cancelled_jobs

    async def start(self) -> None:
        if self._loop_task:
            return
        self.store.recover_running_tasks()
        self._stopping = False
        self._wake_event.clear()
        self._loop_task = asyncio.create_task(self._run_loop(), name="research-scheduler")

    def wake(self) -> None:
        """Wake the dispatcher so newly created immediate jobs do not wait for polling."""

        self._wake_event.set()

    async def stop(self) -> None:
        self._stopping = True
        if self._loop_task:
            self._loop_task.cancel()
            with contextlib.suppress(asyncio.TimeoutError, asyncio.CancelledError):
                await asyncio.wait_for(self._loop_task, timeout=5)
            self._loop_task = None
        for worker in list(self._workers):
            worker.cancel()
        if self._workers:
            with contextlib.suppress(asyncio.TimeoutError, asyncio.CancelledError):
                await asyncio.wait_for(
                    asyncio.gather(*self._workers, return_exceptions=True),
                    timeout=5,
                )
        self._workers.clear()
        self._busy_accounts.clear()
        self.store.recover_running_tasks()

    def snapshot(self) -> dict[str, Any]:
        return {
            "running": bool(self._loop_task and not self._loop_task.done()),
            "active_workers": len(self._workers),
            "busy_accounts": sorted(self._busy_accounts),
            "last_error": self.last_error,
        }

    async def _run_loop(self) -> None:
        while not self._stopping:
            try:
                await self._check_schedules()
                await self._dispatch_due_tasks()
                self.last_error = ""
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.last_error = str(exc)
                for task in self.store.due_tasks(limit=50):
                    self.store.update_task_progress(
                        task["id"],
                        f"采集调度异常：{exc}",
                    )
            with contextlib.suppress(TimeoutError, asyncio.TimeoutError):
                await asyncio.wait_for(self._wake_event.wait(), timeout=2)
            self._wake_event.clear()

    async def _check_schedules(self) -> None:
        """Trigger any overdue research schedules and advance their next run."""
        triggered = False
        for schedule in self.store.due_schedules(limit=20):
            if self._stopping:
                break
            try:
                job = self.store.create_job_from_schedule(schedule["id"])
                next_run_at = _compute_next_run(
                    schedule["schedule_type"], schedule["schedule_value"]
                )
                self.store.advance_schedule(schedule["id"], job["id"], next_run_at)
                if schedule["schedule_type"] == "once":
                    self.store.toggle_schedule(schedule["id"], False)
                triggered = True
            except Exception as exc:
                self.store.update_schedule_error(schedule["id"], str(exc))
                self.last_error = str(exc)
        if triggered:
            self.wake()

    async def _dispatch_due_tasks(self) -> None:
        dispatched_any = False
        for task in self.store.due_tasks(limit=50):
            if self._stopping:
                break
            self._selection_wait_reason = ""
            account_id = await self._select_account(task)
            if not account_id:
                waiting_message = (
                    "已有账号正在采集其他关键词；每个账号同一时间只执行一条任务"
                    if self._busy_accounts
                    else (
                        self._selection_wait_reason
                        or "正在自动唤醒账号；若持续等待，请到“账号环境”检查登录状态"
                    )
                )
                self.store.update_task_progress(
                    task["id"],
                    waiting_message,
                )
                continue
            if not self.store.mark_task_running(task["id"], account_id):
                continue
            self.store.update_task_progress(
                task["id"],
                f"已选择账号 {account_id}，正在发送关键词",
            )
            self._busy_accounts.add(account_id)
            self.store.mark_account_used(account_id)
            worker = asyncio.create_task(
                self._run_task(task, account_id),
                name=f"research-{task['id']}",
            )
            self._workers.add(worker)
            worker.add_done_callback(self._workers.discard)
            await asyncio.sleep(DISPATCH_STAGGER_SECONDS)
            dispatched_any = True
        if dispatched_any:
            self.wake()

    async def _select_account(self, task: dict[str, Any]) -> str | None:
        candidates = task["account_ids"] or self.account_pool.discover_account_ids()
        now = local_now()
        available: list[tuple[datetime, str]] = []
        for account_id in candidates:
            if account_id in self._busy_accounts:
                continue
            runtime = self.store.account_runtime(account_id)
            paused_until = _datetime_or_none(runtime.get("paused_until"))
            if paused_until and paused_until > now:
                continue
            last_used = _datetime_or_none(runtime.get("last_used_at"))
            available.append((last_used or datetime.min.replace(tzinfo=now.tzinfo), account_id))
        # 优先分配给最长时间没被分配任务且处于闲置状态的账号（LRU）
        available.sort(key=lambda item: (item[0], item[1]))

        for _, account_id in available:
            try:
                account = self.account_pool.get_if_started(account_id)
                if account is None:
                    self.store.update_task_progress(
                        task["id"],
                        f"正在后台启动账号 {account_id}",
                    )
                    account = await asyncio.wait_for(
                        self.account_pool.start_account(account_id),
                        timeout=ACCOUNT_START_TIMEOUT_SECONDS,
                    )
                startup_age = getattr(account.client, "startup_age_seconds", None)
                if (
                    isinstance(startup_age, int | float)
                    and startup_age < ACCOUNT_STARTUP_GRACE_SECONDS
                ):
                    self._selection_wait_reason = (
                        f"账号 {account_id} 正在后台加载采集页面（{startup_age:.0f} 秒）"
                    )
                    continue
                self.store.update_task_progress(
                    task["id"],
                    f"正在检测账号 {account_id} 的采集页面",
                )
                state = await asyncio.wait_for(
                    account.client.inspect_session_state(),
                    timeout=ACCOUNT_PROBE_TIMEOUT_SECONDS,
                )
                if state.get("needs_captcha"):
                    self.store.pause_account(account_id, 1800, "检测到验证码，请人工处理")
                    self._selection_wait_reason = (
                        f"账号 {account_id} 检测到验证码，请在账号标签页处理"
                    )
                    continue
                if state.get("browser") == "loading":
                    self._selection_wait_reason = f"账号 {account_id} 正在后台加载采集页面"
                    continue
                if not state.get("logged_in"):
                    self.store.pause_account(account_id, 60, "账号尚未登录")
                    self._selection_wait_reason = (
                        f"账号 {account_id} 尚未登录，请在账号标签页完成登录"
                    )
                    continue
                if not state.get("chat_ready"):
                    self._selection_wait_reason = (
                        f"账号 {account_id} 已登录，正在等待聊天输入框就绪"
                    )
                    continue
                self.store.resume_account(account_id)
                return account_id
            except (TimeoutError, asyncio.TimeoutError):
                self.store.pause_account(
                    account_id,
                    60,
                    "账号启动或状态检测超时，请检查账号页面",
                )
                self._selection_wait_reason = (
                    f"账号 {account_id} 启动或状态检测超时，请检查账号页面"
                )
            except Exception as exc:
                self.store.pause_account(account_id, 60, f"账号暂不可用：{exc}")
                self._selection_wait_reason = f"账号 {account_id} 暂不可用：{exc}"
        return None

    async def _run_task(self, task: dict[str, Any], account_id: str) -> None:
        if self._is_job_cancelled(task["job_id"]):
            self.store.fail_or_retry_task(task["id"], "任务已取消", retry=False)
            return
        try:
            account = self.account_pool.get_if_started(account_id)
            if account is None:
                raise RuntimeError("账号浏览器意外关闭")
            prompt = task["prompt_template"].replace("{keyword}", task["keyword"])
            saved_count = 0

            def save_reference(reference: dict[str, str]) -> None:
                nonlocal saved_count
                for item in normalize_thinking_references([reference]):
                    inserted = self.store.add_result(
                        task["id"],
                        item=item,
                        account_id=account_id,
                    )
                    if inserted:
                        saved_count += 1
                        self.store.update_task_progress(
                            task["id"],
                            f"已采集并保存 {saved_count} 条参考资料",
                        )

            self.store.update_task_progress(
                task["id"],
                "关键词已开始发送，随后会等待豆包回答并展开参考资料",
            )
            result = await asyncio.wait_for(
                account.client.chat(
                    [{"role": "user", "content": prompt}],
                    fresh_conversation=True,
                    collect_thinking_references=True,
                    reference_callback=save_reference,
                ),
                timeout=TASK_TIMEOUT_SECONDS,
            )
            if self._is_job_cancelled(task["job_id"]):
                self.store.fail_or_retry_task(task["id"], "任务已取消", retry=False)
                return
            self.store.update_task_progress(
                task["id"],
                "豆包回答与参考资料已读取，正在保存链接",
            )
            links = normalize_thinking_references(result.get("thinking_references", []))
            self.store.complete_task(
                task["id"],
                answer="",
                links=links,
                account_id=account_id,
            )
        except asyncio.CancelledError:
            self.store.fail_or_retry_task(task["id"], "程序停止，任务已恢复排队", retry=True)
            raise
        except (TimeoutError, asyncio.TimeoutError):
            message = f"采集超过 {TASK_TIMEOUT_SECONDS // 60} 分钟未完成，已自动结束本次尝试"
            self.store.pause_account(account_id, 60, message)
            retry = task["attempt_count"] + 1 < task["max_attempts"]
            self.store.fail_or_retry_task(task["id"], message, retry=retry)
        except Exception as exc:
            risk = _is_risk_error(exc)
            if risk:
                self.store.pause_account(account_id, 1800, f"疑似验证码或风控：{exc}")
            elif isinstance(exc, ReferenceExpansionError):
                self.store.pause_account(account_id, 60, f"参考资料展开不完整：{exc}")
            elif "超时" in str(exc):
                self.store.pause_account(account_id, 60, f"页面响应超时：{exc}")
            retry = task["attempt_count"] + 1 < task["max_attempts"]
            self.store.fail_or_retry_task(task["id"], str(exc), retry=retry)
        finally:
            self._busy_accounts.discard(account_id)
            self.wake()
