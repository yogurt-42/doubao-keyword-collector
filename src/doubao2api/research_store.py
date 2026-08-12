from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from .research_links import platform_for_reference
from .research_platforms import category_for_url, platform_category

LONG_TAIL_TOP_N = 20


def local_now() -> datetime:
    return datetime.now().astimezone()


def iso_now() -> str:
    return local_now().isoformat(timespec="seconds")


def normalize_datetime(value: str | None) -> str:
    if not value:
        return iso_now()
    try:
        normalized = value.strip()
        if normalized.endswith("Z"):
            normalized = f"{normalized[:-1]}+00:00"
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise ValueError("计划时间格式无效") from exc
    parsed = parsed.astimezone()
    return parsed.isoformat(timespec="seconds")


def _compute_next_run(
    schedule_type: str,
    schedule_value: str,
    after: datetime | None = None,
) -> str:
    """Compute the next run time for a schedule.

    - interval: schedule_value is seconds as integer string.
    - once: schedule_value is an ISO datetime string.
    - daily: schedule_value is "HH:MM" in local time.
    """
    after = after or local_now()
    if schedule_type == "interval":
        try:
            seconds = int(schedule_value)
        except ValueError as exc:
            raise ValueError("间隔秒数必须是整数") from exc
        if seconds <= 0:
            raise ValueError("间隔秒数必须大于 0")
        return (after + timedelta(seconds=seconds)).isoformat(timespec="seconds")
    if schedule_type == "once":
        return normalize_datetime(schedule_value)
    if schedule_type == "daily":
        try:
            hour_str, minute_str = schedule_value.split(":")
            hour = int(hour_str)
            minute = int(minute_str)
        except ValueError as exc:
            raise ValueError("每日时间格式必须是 HH:MM") from exc
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            raise ValueError("每日时间超出有效范围")
        candidate = after.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if candidate <= after:
            candidate += timedelta(days=1)
        return candidate.isoformat(timespec="seconds")
    raise ValueError(f"不支持的触发类型: {schedule_type}")


class ResearchStore:
    def __init__(self, path: Path) -> None:
        self.path = path.resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS research_jobs (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    prompt_template TEXT NOT NULL,
                    status TEXT NOT NULL,
                    scheduled_at TEXT NOT NULL,
                    interval_seconds INTEGER NOT NULL,
                    account_cooldown_seconds INTEGER NOT NULL,
                    max_attempts INTEGER NOT NULL,
                    account_ids_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    started_at TEXT,
                    finished_at TEXT,
                    last_error TEXT NOT NULL DEFAULT ''
                );
                CREATE TABLE IF NOT EXISTS research_tasks (
                    id TEXT PRIMARY KEY,
                    job_id TEXT NOT NULL REFERENCES research_jobs(id) ON DELETE CASCADE,
                    position INTEGER NOT NULL,
                    keyword TEXT NOT NULL,
                    status TEXT NOT NULL,
                    scheduled_at TEXT NOT NULL,
                    account_id TEXT NOT NULL DEFAULT '',
                    attempt_count INTEGER NOT NULL DEFAULT 0,
                    answer TEXT NOT NULL DEFAULT '',
                    error TEXT NOT NULL DEFAULT '',
                    result_count INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    started_at TEXT,
                    finished_at TEXT,
                    UNIQUE(job_id, position)
                );
                CREATE INDEX IF NOT EXISTS idx_research_tasks_due
                ON research_tasks(status, scheduled_at);
                CREATE TABLE IF NOT EXISTS research_results (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    job_id TEXT NOT NULL REFERENCES research_jobs(id) ON DELETE CASCADE,
                    task_id TEXT NOT NULL REFERENCES research_tasks(id) ON DELETE CASCADE,
                    collected_at TEXT NOT NULL,
                    collected_date TEXT NOT NULL,
                    keyword TEXT NOT NULL,
                    link TEXT NOT NULL,
                    platform TEXT NOT NULL,
                    platform_type TEXT NOT NULL DEFAULT '',
                    account_id TEXT NOT NULL,
                    title TEXT NOT NULL DEFAULT '',
                    UNIQUE(task_id, link)
                );
                CREATE INDEX IF NOT EXISTS idx_research_results_keyword
                ON research_results(keyword, platform, collected_at);
                CREATE INDEX IF NOT EXISTS idx_research_results_job
                ON research_results(job_id);
                CREATE INDEX IF NOT EXISTS idx_research_results_account
                ON research_results(account_id);
                CREATE INDEX IF NOT EXISTS idx_research_results_date
                ON research_results(collected_date);
                CREATE TABLE IF NOT EXISTS account_runtime (
                    account_id TEXT PRIMARY KEY,
                    last_used_at TEXT,
                    paused_until TEXT,
                    pause_reason TEXT NOT NULL DEFAULT ''
                );
                CREATE INDEX IF NOT EXISTS idx_account_runtime_paused
                ON account_runtime(paused_until);
                CREATE TABLE IF NOT EXISTS research_job_templates (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    keywords_json TEXT NOT NULL,
                    prompt_template TEXT NOT NULL,
                    interval_seconds INTEGER NOT NULL,
                    account_cooldown_seconds INTEGER NOT NULL DEFAULT 0,
                    max_attempts INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS research_schedules (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    template_id TEXT NOT NULL
                        REFERENCES research_job_templates(id) ON DELETE CASCADE,
                    enabled INTEGER NOT NULL DEFAULT 1,
                    schedule_type TEXT NOT NULL,
                    schedule_value TEXT NOT NULL,
                    next_run_at TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    last_run_at TEXT,
                    last_job_id TEXT,
                    run_count INTEGER NOT NULL DEFAULT 0,
                    last_error TEXT NOT NULL DEFAULT ''
                );
                CREATE INDEX IF NOT EXISTS idx_research_schedules_due
                ON research_schedules(enabled, next_run_at);
                """
            )
            connection.execute(
                """
                UPDATE research_jobs SET status = 'failed'
                WHERE status = 'completed' AND EXISTS (
                    SELECT 1 FROM research_tasks
                    WHERE research_tasks.job_id = research_jobs.id
                        AND research_tasks.status = 'failed'
                )
                """
            )
            platform_updates = []
            for result in connection.execute(
                "SELECT id, link, platform FROM research_results"
            ).fetchall():
                canonical = platform_for_reference(result["link"], result["platform"])
                if canonical != result["platform"]:
                    platform_updates.append((canonical, result["id"]))
            if platform_updates:
                connection.executemany(
                    "UPDATE research_results SET platform = ? WHERE id = ?",
                    platform_updates,
                )
            self._ensure_platform_type_column(connection)

    def _ensure_platform_type_column(self, connection: sqlite3.Connection) -> None:
        columns = {
            row["name"]
            for row in connection.execute("PRAGMA table_info(research_results)").fetchall()
        }
        if "platform_type" not in columns:
            connection.execute(
                "ALTER TABLE research_results ADD COLUMN platform_type TEXT NOT NULL DEFAULT ''"
            )
            updates = []
            for result in connection.execute(
                "SELECT id, link, platform FROM research_results"
            ).fetchall():
                platform_type = category_for_url(result["link"]) or platform_category(
                    result["platform"]
                )
                updates.append((platform_type, result["id"]))
            if updates:
                connection.executemany(
                    "UPDATE research_results SET platform_type = ? WHERE id = ?",
                    updates,
                )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_research_results_type "
            "ON research_results(platform_type)"
        )

    def create_job(
        self,
        *,
        name: str,
        keywords: list[str],
        account_ids: list[str],
        prompt_template: str,
        scheduled_at: str | None,
        interval_seconds: int,
        account_cooldown_seconds: int,
        max_attempts: int,
    ) -> dict[str, Any]:
        job_id = uuid.uuid4().hex
        created_at = iso_now()
        first_due = datetime.fromisoformat(normalize_datetime(scheduled_at))
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO research_jobs (
                    id, name, prompt_template, status, scheduled_at, interval_seconds,
                    account_cooldown_seconds, max_attempts, account_ids_json, created_at
                ) VALUES (?, ?, ?, 'running', ?, ?, ?, ?, ?, ?)
                """,
                (
                    job_id,
                    name.strip() or f"关键词采集 {created_at[:10]}",
                    prompt_template,
                    first_due.isoformat(timespec="seconds"),
                    interval_seconds,
                    account_cooldown_seconds,
                    max_attempts,
                    json.dumps(account_ids, ensure_ascii=False),
                    created_at,
                ),
            )
            for position, keyword in enumerate(keywords):
                due = first_due + timedelta(seconds=position * interval_seconds)
                connection.execute(
                    """
                    INSERT INTO research_tasks (
                        id, job_id, position, keyword, status, scheduled_at, created_at
                    ) VALUES (?, ?, ?, ?, 'pending', ?, ?)
                    """,
                    (
                        uuid.uuid4().hex,
                        job_id,
                        position,
                        keyword,
                        due.isoformat(timespec="seconds"),
                        created_at,
                    ),
                )
        return self.get_job(job_id)

    def _job_row(self, row: sqlite3.Row) -> dict[str, Any]:
        item = dict(row)
        item["account_ids"] = json.loads(item.pop("account_ids_json"))
        for key in ("total", "completed", "failed", "pending", "running_tasks"):
            if key in item:
                item[key] = int(item[key] or 0)
        item["active_details"] = str(item.get("active_details") or "")
        item["progress_percent"] = (
            round((item.get("completed", 0) + item.get("failed", 0)) * 100 / item["total"])
            if item.get("total")
            else 0
        )
        return item

    def get_job(self, job_id: str, *, include_tasks: bool = True) -> dict[str, Any]:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT j.*,
                    COUNT(t.id) AS total,
                    SUM(CASE WHEN t.status = 'completed' THEN 1 ELSE 0 END) AS completed,
                    SUM(CASE WHEN t.status = 'failed' THEN 1 ELSE 0 END) AS failed,
                    SUM(CASE WHEN t.status = 'pending' THEN 1 ELSE 0 END) AS pending,
                    SUM(CASE WHEN t.status = 'running' THEN 1 ELSE 0 END) AS running_tasks,
                    GROUP_CONCAT(
                        CASE WHEN t.status = 'running'
                            THEN t.keyword || ' · ' || t.account_id END,
                        '；'
                    ) AS active_details
                FROM research_jobs j
                LEFT JOIN research_tasks t ON t.job_id = j.id
                WHERE j.id = ?
                GROUP BY j.id
                """,
                (job_id,),
            ).fetchone()
            if row is None:
                raise KeyError(job_id)
            job = self._job_row(row)
            if include_tasks:
                job["tasks"] = [
                    dict(item)
                    for item in connection.execute(
                        """
                        SELECT * FROM research_tasks
                        WHERE job_id = ? ORDER BY position
                        """,
                        (job_id,),
                    ).fetchall()
                ]
            return job

    def list_jobs(self, limit: int = 100) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT j.*,
                    COUNT(t.id) AS total,
                    SUM(CASE WHEN t.status = 'completed' THEN 1 ELSE 0 END) AS completed,
                    SUM(CASE WHEN t.status = 'failed' THEN 1 ELSE 0 END) AS failed,
                    SUM(CASE WHEN t.status = 'pending' THEN 1 ELSE 0 END) AS pending,
                    SUM(CASE WHEN t.status = 'running' THEN 1 ELSE 0 END) AS running_tasks,
                    GROUP_CONCAT(
                        CASE WHEN t.status = 'running'
                            THEN t.keyword || ' · ' || t.account_id END,
                        '；'
                    ) AS active_details
                FROM research_jobs j
                LEFT JOIN research_tasks t ON t.job_id = j.id
                GROUP BY j.id ORDER BY j.created_at DESC LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [self._job_row(row) for row in rows]

    def set_job_status(self, job_id: str, status: str) -> dict[str, Any]:
        if status not in {"running", "paused", "cancelled"}:
            raise ValueError("不支持的任务状态")
        with self._connect() as connection:
            result = connection.execute(
                """
                UPDATE research_jobs SET status = ?,
                    finished_at = CASE WHEN ? = 'cancelled' THEN ? ELSE finished_at END
                WHERE id = ?
                """,
                (status, status, iso_now(), job_id),
            )
            if result.rowcount == 0:
                raise KeyError(job_id)
            if status == "cancelled":
                connection.execute(
                    """
                    UPDATE research_tasks SET status = 'cancelled', finished_at = ?
                    WHERE job_id = ? AND status IN ('pending', 'running')
                    """,
                    (iso_now(), job_id),
                )
            return self.get_job(job_id)

    def delete_job(self, job_id: str) -> None:
        with self._connect() as connection:
            result = connection.execute(
                "DELETE FROM research_jobs WHERE id = ?",
                (job_id,),
            )
            if result.rowcount == 0:
                raise KeyError(job_id)

    def rename_job(self, job_id: str, name: str) -> dict[str, Any]:
        name = (name or "").strip()
        if not name:
            raise ValueError("任务名称不能为空")
        with self._connect() as connection:
            result = connection.execute(
                "UPDATE research_jobs SET name = ? WHERE id = ?",
                (name, job_id),
            )
            if result.rowcount == 0:
                raise KeyError(job_id)
            connection.commit()
            return self.get_job(job_id)

    def sync_platform_info(self, batch_size: int = 10000) -> int:
        """Re-resolve platform and platform_type for rows that look stale.

        Only updates rows with an empty platform_type or an unknown platform,
        which is the common case after importing new platform rules.
        """

        limit = max(batch_size, 1)
        updated = 0
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT id, link, platform, platform_type
                FROM research_results
                WHERE platform_type = '' OR platform = '未知平台'
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
            updates: list[tuple[str, str, int]] = []
            for row in rows:
                new_platform = platform_for_reference(row["link"], row["platform"])
                new_type = category_for_url(row["link"]) or platform_category(new_platform)
                if new_platform != row["platform"] or new_type != row["platform_type"]:
                    updates.append((new_platform, new_type, row["id"]))
            if updates:
                connection.executemany(
                    """
                    UPDATE research_results
                    SET platform = ?, platform_type = ?
                    WHERE id = ?
                    """,
                    updates,
                )
                updated = len(updates)
        return updated

    def due_tasks(self, limit: int = 20) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT t.*, j.prompt_template, j.account_ids_json,
                    j.interval_seconds, j.account_cooldown_seconds, j.max_attempts
                FROM research_tasks t
                JOIN research_jobs j ON j.id = t.job_id
                WHERE t.status = 'pending' AND j.status = 'running'
                    AND t.scheduled_at <= ?
                ORDER BY t.scheduled_at, t.position LIMIT ?
                """,
                (iso_now(), limit),
            ).fetchall()
        output = []
        for row in rows:
            item = dict(row)
            item["account_ids"] = json.loads(item.pop("account_ids_json"))
            output.append(item)
        return output

    def mark_task_running(self, task_id: str, account_id: str) -> bool:
        with self._connect() as connection:
            result = connection.execute(
                """
                UPDATE research_tasks SET status = 'running', account_id = ?,
                    attempt_count = attempt_count + 1, started_at = ?, error = ''
                WHERE id = ? AND status = 'pending'
                """,
                (account_id, iso_now(), task_id),
            )
            if result.rowcount:
                connection.execute(
                    """
                    UPDATE research_jobs SET started_at = COALESCE(started_at, ?)
                    WHERE id = ?
                    """,
                    (
                        iso_now(),
                        connection.execute(
                            "SELECT job_id FROM research_tasks WHERE id = ?", (task_id,)
                        ).fetchone()[0],
                    ),
                )
            return bool(result.rowcount)

    def update_task_progress(self, task_id: str, message: str) -> None:
        text = message.strip()[:500]
        with self._connect() as connection:
            task = connection.execute(
                "SELECT job_id, status FROM research_tasks WHERE id = ?",
                (task_id,),
            ).fetchone()
            if task is None:
                return
            connection.execute(
                "UPDATE research_tasks SET error = ? WHERE id = ?",
                (text, task_id),
            )
            running_tasks = connection.execute(
                """
                SELECT COUNT(*) FROM research_tasks
                WHERE job_id = ? AND status = 'running'
                """,
                (task["job_id"],),
            ).fetchone()[0]
            if task["status"] == "running" or not running_tasks:
                connection.execute(
                    "UPDATE research_jobs SET last_error = ? WHERE id = ?",
                    (text, task["job_id"]),
                )

    def complete_task(
        self,
        task_id: str,
        *,
        answer: str,
        links: list[dict[str, str]],
        account_id: str,
    ) -> None:
        now = iso_now()
        with self._connect() as connection:
            task = connection.execute(
                "SELECT job_id, keyword FROM research_tasks WHERE id = ?",
                (task_id,),
            ).fetchone()
            if task is None:
                return
            for item in links:
                platform_type = item.get("platform_type", "") or category_for_url(item["link"])
                connection.execute(
                    """
                    INSERT OR IGNORE INTO research_results (
                        job_id, task_id, collected_at, collected_date, keyword,
                        link, platform, platform_type, account_id, title
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        task["job_id"],
                        task_id,
                        now,
                        now[:10],
                        task["keyword"],
                        item["link"],
                        item["platform"],
                        platform_type,
                        account_id,
                        item.get("title", ""),
                    ),
                )
            connection.execute(
                """
                UPDATE research_tasks SET status = 'completed', answer = ?,
                    result_count = ?, finished_at = ?, error = ''
                WHERE id = ?
                """,
                (answer, len(links), now, task_id),
            )
            connection.execute(
                "UPDATE research_jobs SET last_error = ? WHERE id = ?",
                (
                    f"“{task['keyword']}”已完成，采集 {len(links)} 条参考资料",
                    task["job_id"],
                ),
            )
            self._finish_job_if_done(connection, task["job_id"], now)

    def add_result(
        self,
        task_id: str,
        *,
        item: dict[str, str],
        account_id: str,
    ) -> bool:
        now = iso_now()
        with self._connect() as connection:
            task = connection.execute(
                "SELECT job_id, keyword FROM research_tasks WHERE id = ?",
                (task_id,),
            ).fetchone()
            if task is None:
                return False
            result = connection.execute(
                """
                INSERT OR IGNORE INTO research_results (
                    job_id, task_id, collected_at, collected_date, keyword,
                    link, platform, platform_type, account_id, title
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    task["job_id"],
                    task_id,
                    now,
                    now[:10],
                    task["keyword"],
                    item["link"],
                    item["platform"],
                    item.get("platform_type", "") or category_for_url(item["link"]),
                    account_id,
                    item.get("title", ""),
                ),
            )
            if result.rowcount:
                connection.execute(
                    """
                    UPDATE research_tasks SET result_count = (
                        SELECT COUNT(*) FROM research_results WHERE task_id = ?
                    ) WHERE id = ?
                    """,
                    (task_id, task_id),
                )
            return bool(result.rowcount)

    def fail_or_retry_task(self, task_id: str, error: str, *, retry: bool) -> None:
        now = iso_now()
        with self._connect() as connection:
            task = connection.execute(
                "SELECT job_id FROM research_tasks WHERE id = ?", (task_id,)
            ).fetchone()
            if task is None:
                return
            connection.execute(
                """
                UPDATE research_tasks SET status = ?, error = ?,
                    finished_at = CASE WHEN ? THEN NULL ELSE ? END
                WHERE id = ?
                """,
                ("pending" if retry else "failed", error[:2000], retry, now, task_id),
            )
            connection.execute(
                "UPDATE research_jobs SET last_error = ? WHERE id = ?",
                (error[:2000], task["job_id"]),
            )
            if not retry:
                self._finish_job_if_done(connection, task["job_id"], now)

    def _finish_job_if_done(self, connection: sqlite3.Connection, job_id: str, now: str) -> None:
        unfinished = connection.execute(
            """
            SELECT COUNT(*) FROM research_tasks
            WHERE job_id = ? AND status IN ('pending', 'running')
            """,
            (job_id,),
        ).fetchone()[0]
        if unfinished == 0:
            failed = connection.execute(
                """
                SELECT COUNT(*) FROM research_tasks
                WHERE job_id = ? AND status = 'failed'
                """,
                (job_id,),
            ).fetchone()[0]
            connection.execute(
                """
                UPDATE research_jobs SET status = ?, finished_at = ?
                WHERE id = ? AND status = 'running'
                """,
                ("failed" if failed else "completed", now, job_id),
            )

    def recover_running_tasks(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE research_tasks SET status = 'pending',
                    error = '程序上次退出时任务仍在运行，已恢复排队'
                WHERE status = 'running'
                """
            )

    # ------------------------------------------------------------------
    # Job templates for scheduled research
    # ------------------------------------------------------------------

    def _validate_job_template_inputs(
        self,
        keywords: list[str],
        prompt_template: str,
        max_attempts: int,
    ) -> list[str]:
        normalized = [keyword.strip() for keyword in keywords if keyword.strip()]
        if not normalized:
            raise ValueError("请至少填写一个关键词")
        if "{keyword}" not in prompt_template:
            raise ValueError("提问模板必须包含 {keyword} 占位符")
        if not 1 <= max_attempts <= 3:
            raise ValueError("最多尝试次数必须在 1 到 3 之间")
        return normalized

    def _job_template_row(self, row: sqlite3.Row) -> dict[str, Any]:
        item = dict(row)
        item["keywords"] = json.loads(item.pop("keywords_json"))
        return item

    def create_job_template(
        self,
        *,
        name: str,
        keywords: list[str],
        prompt_template: str,
        interval_seconds: int,
        account_cooldown_seconds: int,
        max_attempts: int,
    ) -> dict[str, Any]:
        normalized = self._validate_job_template_inputs(keywords, prompt_template, max_attempts)
        template_id = uuid.uuid4().hex
        created_at = iso_now()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO research_job_templates (
                    id, name, keywords_json, prompt_template, interval_seconds,
                    account_cooldown_seconds, max_attempts, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    template_id,
                    name.strip() or f"任务模板 {created_at[:10]}",
                    json.dumps(normalized, ensure_ascii=False),
                    prompt_template,
                    interval_seconds,
                    account_cooldown_seconds,
                    max_attempts,
                    created_at,
                    created_at,
                ),
            )
        return self.get_job_template(template_id)

    def list_job_templates(self) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM research_job_templates ORDER BY updated_at DESC"
            ).fetchall()
        return [self._job_template_row(row) for row in rows]

    def get_job_template(self, template_id: str) -> dict[str, Any]:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM research_job_templates WHERE id = ?", (template_id,)
            ).fetchone()
        if row is None:
            raise KeyError(template_id)
        return self._job_template_row(row)

    def update_job_template(
        self,
        template_id: str,
        *,
        name: str,
        keywords: list[str],
        prompt_template: str,
        interval_seconds: int,
        account_cooldown_seconds: int,
        max_attempts: int,
    ) -> dict[str, Any]:
        normalized = self._validate_job_template_inputs(keywords, prompt_template, max_attempts)
        updated_at = iso_now()
        with self._connect() as connection:
            result = connection.execute(
                """
                UPDATE research_job_templates SET
                    name = ?,
                    keywords_json = ?,
                    prompt_template = ?,
                    interval_seconds = ?,
                    account_cooldown_seconds = ?,
                    max_attempts = ?,
                    updated_at = ?
                WHERE id = ?
                """,
                (
                    name.strip() or f"任务模板 {updated_at[:10]}",
                    json.dumps(normalized, ensure_ascii=False),
                    prompt_template,
                    interval_seconds,
                    account_cooldown_seconds,
                    max_attempts,
                    updated_at,
                    template_id,
                ),
            )
            if result.rowcount == 0:
                raise KeyError(template_id)
        return self.get_job_template(template_id)

    def delete_job_template(self, template_id: str) -> None:
        with self._connect() as connection:
            result = connection.execute(
                "DELETE FROM research_job_templates WHERE id = ?", (template_id,)
            )
            if result.rowcount == 0:
                raise KeyError(template_id)

    # ------------------------------------------------------------------
    # Research schedules
    # ------------------------------------------------------------------

    def _schedule_row(self, row: sqlite3.Row) -> dict[str, Any]:
        item = dict(row)
        item["keywords"] = json.loads(item.pop("keywords_json"))
        return item

    def create_schedule(
        self,
        *,
        name: str,
        template_id: str,
        schedule_type: str,
        schedule_value: str,
    ) -> dict[str, Any]:
        if schedule_type not in ("interval", "once", "daily"):
            raise ValueError("触发类型必须是 interval、once 或 daily")
        next_run_at = _compute_next_run(schedule_type, schedule_value)
        # Verify template exists before creating the schedule.
        self.get_job_template(template_id)
        schedule_id = uuid.uuid4().hex
        created_at = iso_now()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO research_schedules (
                    id, name, template_id, enabled, schedule_type, schedule_value,
                    next_run_at, created_at, updated_at
                ) VALUES (?, ?, ?, 1, ?, ?, ?, ?, ?)
                """,
                (
                    schedule_id,
                    name.strip() or f"定时计划 {created_at[:10]}",
                    template_id,
                    schedule_type,
                    schedule_value,
                    next_run_at,
                    created_at,
                    created_at,
                ),
            )
        return self.get_schedule(schedule_id)

    def list_schedules(self) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT s.*, t.name AS template_name, t.keywords_json
                FROM research_schedules s
                JOIN research_job_templates t ON t.id = s.template_id
                ORDER BY s.enabled DESC, s.next_run_at ASC
                """
            ).fetchall()
        return [self._schedule_row(row) for row in rows]

    def get_schedule(self, schedule_id: str) -> dict[str, Any]:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT s.*, t.name AS template_name, t.keywords_json
                FROM research_schedules s
                JOIN research_job_templates t ON t.id = s.template_id
                WHERE s.id = ?
                """,
                (schedule_id,),
            ).fetchone()
        if row is None:
            raise KeyError(schedule_id)
        return self._schedule_row(row)

    def update_schedule(
        self,
        schedule_id: str,
        *,
        name: str,
        template_id: str,
        schedule_type: str,
        schedule_value: str,
    ) -> dict[str, Any]:
        if schedule_type not in ("interval", "once", "daily"):
            raise ValueError("触发类型必须是 interval、once 或 daily")
        next_run_at = _compute_next_run(schedule_type, schedule_value)
        self.get_job_template(template_id)
        updated_at = iso_now()
        with self._connect() as connection:
            result = connection.execute(
                """
                UPDATE research_schedules SET
                    name = ?,
                    template_id = ?,
                    schedule_type = ?,
                    schedule_value = ?,
                    next_run_at = ?,
                    updated_at = ?
                WHERE id = ?
                """,
                (
                    name.strip() or f"定时计划 {updated_at[:10]}",
                    template_id,
                    schedule_type,
                    schedule_value,
                    next_run_at,
                    updated_at,
                    schedule_id,
                ),
            )
            if result.rowcount == 0:
                raise KeyError(schedule_id)
        return self.get_schedule(schedule_id)

    def toggle_schedule(self, schedule_id: str, enabled: bool) -> dict[str, Any]:
        updated_at = iso_now()
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT schedule_type, schedule_value, next_run_at, enabled
                FROM research_schedules WHERE id = ?
                """,
                (schedule_id,),
            ).fetchone()
            if row is None:
                raise KeyError(schedule_id)
            next_run_at = row["next_run_at"]
            # If re-enabling and the old next_run_at has passed, recompute.
            if enabled and not row["enabled"] and next_run_at <= updated_at:
                next_run_at = _compute_next_run(row["schedule_type"], row["schedule_value"])
            connection.execute(
                """
                UPDATE research_schedules
                SET enabled = ?, next_run_at = ?, updated_at = ?
                WHERE id = ?
                """,
                (1 if enabled else 0, next_run_at, updated_at, schedule_id),
            )
        return self.get_schedule(schedule_id)

    def delete_schedule(self, schedule_id: str) -> None:
        with self._connect() as connection:
            result = connection.execute(
                "DELETE FROM research_schedules WHERE id = ?", (schedule_id,)
            )
            if result.rowcount == 0:
                raise KeyError(schedule_id)

    def due_schedules(self, limit: int = 20) -> list[dict[str, Any]]:
        now = iso_now()
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT s.*, t.name AS template_name, t.keywords_json
                FROM research_schedules s
                JOIN research_job_templates t ON t.id = s.template_id
                WHERE s.enabled = 1 AND s.next_run_at <= ?
                ORDER BY s.next_run_at ASC LIMIT ?
                """,
                (now, limit),
            ).fetchall()
        return [self._schedule_row(row) for row in rows]

    def create_job_from_schedule(self, schedule_id: str) -> dict[str, Any]:
        schedule = self.get_schedule(schedule_id)
        template = self.get_job_template(schedule["template_id"])
        return self.create_job(
            name=f"{schedule['name']} - {iso_now()}",
            keywords=template["keywords"],
            account_ids=[],  # 由调度器按现有 LRU 逻辑动态选择账号
            prompt_template=template["prompt_template"],
            scheduled_at=iso_now(),
            interval_seconds=template["interval_seconds"],
            account_cooldown_seconds=template["account_cooldown_seconds"],
            max_attempts=template["max_attempts"],
        )

    def advance_schedule(self, schedule_id: str, job_id: str, next_run_at: str) -> None:
        updated_at = iso_now()
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE research_schedules SET
                    last_run_at = ?,
                    last_job_id = ?,
                    run_count = run_count + 1,
                    next_run_at = ?,
                    updated_at = ?,
                    last_error = ''
                WHERE id = ?
                """,
                (updated_at, job_id, next_run_at, updated_at, schedule_id),
            )

    def update_schedule_error(self, schedule_id: str, error: str) -> None:
        updated_at = iso_now()
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE research_schedules SET last_error = ?, updated_at = ?
                WHERE id = ?
                """,
                (error[:500], updated_at, schedule_id),
            )

    def account_runtime(self, account_id: str) -> dict[str, Any]:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM account_runtime WHERE account_id = ?", (account_id,)
            ).fetchone()
        return (
            dict(row)
            if row
            else {
                "account_id": account_id,
                "last_used_at": None,
                "paused_until": None,
                "pause_reason": "",
            }
        )

    def mark_account_used(self, account_id: str) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO account_runtime(account_id, last_used_at)
                VALUES (?, ?)
                ON CONFLICT(account_id) DO UPDATE SET last_used_at = excluded.last_used_at
                """,
                (account_id, iso_now()),
            )

    def resume_account(self, account_id: str) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO account_runtime (account_id, paused_until, pause_reason)
                VALUES (?, NULL, '')
                ON CONFLICT(account_id) DO UPDATE SET
                    paused_until = NULL,
                    pause_reason = ''
                """,
                (account_id,),
            )

    def rename_account_references(self, old_account_id: str, new_account_id: str) -> None:
        with self._connect() as connection:
            connection.execute(
                "DELETE FROM account_runtime WHERE account_id = ?",
                (new_account_id,),
            )
            connection.execute(
                "UPDATE account_runtime SET account_id = ? WHERE account_id = ?",
                (new_account_id, old_account_id),
            )
            connection.execute(
                "UPDATE research_results SET account_id = ? WHERE account_id = ?",
                (new_account_id, old_account_id),
            )
            jobs = connection.execute("SELECT id, account_ids_json FROM research_jobs").fetchall()
            for job in jobs:
                account_ids = json.loads(job["account_ids_json"])
                updated = [
                    new_account_id if value == old_account_id else value for value in account_ids
                ]
                if updated != account_ids:
                    connection.execute(
                        "UPDATE research_jobs SET account_ids_json = ? WHERE id = ?",
                        (json.dumps(updated, ensure_ascii=False), job["id"]),
                    )

    def remove_account_references(self, account_id: str) -> None:
        with self._connect() as connection:
            connection.execute(
                "DELETE FROM account_runtime WHERE account_id = ?",
                (account_id,),
            )
            jobs = connection.execute("SELECT id, account_ids_json FROM research_jobs").fetchall()
            for job in jobs:
                account_ids = json.loads(job["account_ids_json"])
                updated = [value for value in account_ids if value != account_id]
                if updated != account_ids:
                    connection.execute(
                        "UPDATE research_jobs SET account_ids_json = ? WHERE id = ?",
                        (json.dumps(updated, ensure_ascii=False), job["id"]),
                    )

    def pause_account(self, account_id: str, seconds: int, reason: str) -> None:
        paused_until = (local_now() + timedelta(seconds=seconds)).isoformat(timespec="seconds")
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO account_runtime(account_id, paused_until, pause_reason)
                VALUES (?, ?, ?)
                ON CONFLICT(account_id) DO UPDATE SET
                    paused_until = excluded.paused_until,
                    pause_reason = excluded.pause_reason
                """,
                (account_id, paused_until, reason[:500]),
            )

    def _result_filter(
        self,
        *,
        job_id: str = "",
        keyword: str | list[str] = "",
        platform: str = "",
        account_id: str = "",
        date_from: str = "",
        date_to: str = "",
    ) -> tuple[str, list[Any]]:
        conditions: list[str] = []
        params: list[Any] = []
        if job_id:
            conditions.append("r.job_id = ?")
            params.append(job_id)
        if isinstance(keyword, list):
            keywords = list(dict.fromkeys(value.strip() for value in keyword if value.strip()))
            if keywords:
                placeholders = ", ".join("?" for _ in keywords)
                conditions.append(f"r.keyword IN ({placeholders})")
                params.extend(keywords)
        elif keyword:
            conditions.append("r.keyword LIKE ?")
            params.append(f"%{keyword}%")
        if platform:
            conditions.append("r.platform = ?")
            params.append(platform)
        if account_id:
            conditions.append("r.account_id = ?")
            params.append(account_id)
        if date_from:
            conditions.append("r.collected_date >= ?")
            params.append(date_from)
        if date_to:
            conditions.append("r.collected_date <= ?")
            params.append(date_to)
        where = "WHERE 1=1" if not conditions else f"WHERE {' AND '.join(conditions)}"
        return where, params

    def list_results(
        self,
        *,
        job_id: str = "",
        keyword: str | list[str] = "",
        platform: str = "",
        account_id: str = "",
        date_from: str = "",
        date_to: str = "",
        limit: int = 500,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        where, params = self._result_filter(
            job_id=job_id,
            keyword=keyword,
            platform=platform,
            account_id=account_id,
            date_from=date_from,
            date_to=date_to,
        )
        params.extend([min(max(limit, 1), 100000), max(offset, 0)])
        with self._connect() as connection:
            rows = connection.execute(
                f"""
                SELECT r.*, j.name AS job_name
                FROM research_results r
                JOIN research_jobs j ON j.id = r.job_id
                {where}
                ORDER BY r.collected_at DESC, r.id DESC LIMIT ? OFFSET ?
                """,
                params,
            ).fetchall()
        return [dict(row) for row in rows]

    def result_dashboard(
        self,
        *,
        job_id: str = "",
        keyword: str | list[str] = "",
        platform: str = "",
        account_id: str = "",
        date_from: str = "",
        date_to: str = "",
    ) -> dict[str, Any]:
        where, params = self._result_filter(
            job_id=job_id,
            keyword=keyword,
            platform=platform,
            account_id=account_id,
            date_from=date_from,
            date_to=date_to,
        )
        with self._connect() as connection:
            summary = connection.execute(
                f"""
                SELECT COUNT(*) AS total,
                    COUNT(DISTINCT r.job_id) AS jobs,
                    COUNT(DISTINCT r.keyword) AS keywords,
                    COUNT(DISTINCT NULLIF(r.platform, '')) AS platforms
                FROM research_results r
                {where}
                """,
                params,
            ).fetchone()
            platform_rows = connection.execute(
                f"""
                SELECT COALESCE(NULLIF(r.platform, ''), '未知平台') AS platform,
                    COALESCE(MAX(NULLIF(r.platform_type, '')), '') AS type,
                    COUNT(*) AS count
                FROM research_results r
                {where}
                GROUP BY COALESCE(NULLIF(r.platform, ''), '未知平台')
                ORDER BY count DESC, platform
                """,
                params,
            ).fetchall()
            type_rows = connection.execute(
                f"""
                SELECT COALESCE(NULLIF(r.platform_type, ''), '未分类') AS type,
                    COUNT(*) AS count
                FROM research_results r
                {where}
                GROUP BY COALESCE(NULLIF(r.platform_type, ''), '未分类')
                ORDER BY count DESC
                """,
                params,
            ).fetchall()
        total = int(summary["total"])
        platform_rows = [dict(row) for row in platform_rows]
        long_tail_rows = platform_rows[LONG_TAIL_TOP_N:]
        long_tail_total = sum(int(row["count"]) for row in long_tail_rows)
        long_tail = {
            "total": long_tail_total,
            "share": round(long_tail_total / total * 100, 1) if total else 0.0,
            "by_platform": long_tail_rows,
            "by_type": [dict(row) for row in type_rows],
        }
        return {
            "summary": dict(summary),
            "platforms": platform_rows,
            "long_tail": long_tail,
        }

    def long_tail_analysis(
        self,
        *,
        job_id: str = "",
        keyword: str | list[str] = "",
        platform: str = "",
        account_id: str = "",
        date_from: str = "",
        date_to: str = "",
        split_mode: str = "threshold",
        breadth_threshold: int = 3,
        freq_threshold: int = 20,
        density_threshold: float = 5.0,
        noise_density_threshold: float = 20.0,
        keywords_sample_limit: int = 10,
    ) -> dict[str, Any]:
        """Aggregate platforms and classify them into long-tail quadrants.

        Returns per-platform frequency, keyword breadth, density, a representative
        link/domain, a keyword sample, and the quadrant classification.
        """
        where, params = self._result_filter(
            job_id=job_id,
            keyword=keyword,
            platform=platform,
            account_id=account_id,
            date_from=date_from,
            date_to=date_to,
        )
        with self._connect() as connection:
            rows = connection.execute(
                f"""
                SELECT
                    platform,
                    COUNT(*) AS freq,
                    COUNT(DISTINCT keyword) AS breadth,
                    COALESCE(MAX(NULLIF(platform_type, '')), '') AS type
                FROM research_results r
                {where}
                    AND platform <> ''
                GROUP BY platform
                ORDER BY freq DESC, platform
                """,
                params,
            ).fetchall()
            link_rows = connection.execute(
                f"""
                SELECT platform, link, COUNT(*) AS c
                FROM research_results r
                {where}
                    AND platform <> ''
                GROUP BY platform, link
                ORDER BY platform, c DESC, link ASC
                """,
                params,
            ).fetchall()
            keyword_rows = connection.execute(
                f"""
                SELECT DISTINCT platform, keyword
                FROM research_results r
                {where}
                    AND platform <> ''
                    AND keyword <> ''
                ORDER BY platform, keyword
                """,
                params,
            ).fetchall()
            total_row = connection.execute(
                f"SELECT COUNT(*) FROM research_results r {where}", params
            ).fetchone()
        total_records = int(total_row[0]) if total_row else 0

        representative_links: dict[str, str] = {}
        for row in link_rows:
            platform = row["platform"]
            if platform not in representative_links:
                representative_links[platform] = row["link"]

        keywords_by_platform: dict[str, list[str]] = {}
        for row in keyword_rows:
            platform = row["platform"]
            sample = keywords_by_platform.setdefault(platform, [])
            if len(sample) < keywords_sample_limit:
                sample.append(row["keyword"])

        platforms: list[dict[str, Any]] = []
        for row in rows:
            platform = row["platform"]
            keywords_sample = keywords_by_platform.get(platform, [])
            link = representative_links.get(platform, "")
            domain = urlparse(link).netloc or str(platform)
            platforms.append(
                {
                    "platform": platform,
                    "domain": domain,
                    "representative_link": link,
                    "freq": int(row["freq"]),
                    "breadth": int(row["breadth"]),
                    "density": round(int(row["freq"]) / int(row["breadth"]), 2),
                    "type": row["type"],
                    "keywords_sample": keywords_sample,
                }
            )

        medians: dict[str, float] = {}
        if split_mode == "median" and platforms:
            breadth_values = sorted(p["breadth"] for p in platforms)
            freq_values = sorted(p["freq"] for p in platforms)
            n = len(breadth_values)
            medians["breadth"] = (
                breadth_values[n // 2]
                if n % 2
                else (breadth_values[n // 2 - 1] + breadth_values[n // 2]) / 2
            )
            medians["freq"] = (
                freq_values[n // 2]
                if n % 2
                else (freq_values[n // 2 - 1] + freq_values[n // 2]) / 2
            )

        def classify(row: dict[str, Any]) -> str:
            if split_mode == "median":
                high_breadth = row["breadth"] >= medians["breadth"]
                high_freq = row["freq"] > medians["freq"]
            else:
                high_breadth = row["breadth"] >= breadth_threshold
                high_freq = row["freq"] > freq_threshold
            density = row["density"]
            if high_breadth and high_freq:
                return "头部主流媒体"
            if high_breadth and not high_freq:
                if density >= noise_density_threshold:
                    return "虚假长尾(噪声)"
                if density <= density_threshold:
                    return "垂直长尾宝藏"
                return "普通垂直信源"
            if not high_breadth and high_freq:
                return "特定品类垂直站"
            return "一次性/僵尸信源"

        for row in platforms:
            row["quadrant"] = classify(row)

        quadrant_order = [
            "垂直长尾宝藏",
            "虚假长尾(噪声)",
            "头部主流媒体",
            "特定品类垂直站",
            "普通垂直信源",
            "一次性/僵尸信源",
        ]
        quadrants: dict[str, list[dict[str, Any]]] = {name: [] for name in quadrant_order}
        for row in platforms:
            quadrants[row["quadrant"]].append(row)

        target_long_tail = [dict(row) for row in platforms if row["quadrant"] == "垂直长尾宝藏"]
        target_long_tail.sort(key=lambda row: (-row["breadth"], row["density"], row["platform"]))

        return {
            "params": {
                "split_mode": split_mode,
                "breadth_threshold": breadth_threshold,
                "freq_threshold": freq_threshold,
                "density_threshold": density_threshold,
                "noise_density_threshold": noise_density_threshold,
                "medians": medians,
            },
            "summary": {
                "total_records": total_records,
                "platform_count": len(platforms),
                "target_count": len(target_long_tail),
                "noise_count": len(quadrants["虚假长尾(噪声)"]),
                "quadrant_counts": {name: len(items) for name, items in quadrants.items() if items},
            },
            "platforms": platforms,
            "target_long_tail": target_long_tail,
            "quadrants": quadrants,
        }

    def result_jobs(self) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT j.id, j.name, COUNT(r.id) AS result_count
                FROM research_jobs j
                JOIN research_results r ON r.job_id = j.id
                GROUP BY j.id, j.name
                ORDER BY MAX(r.collected_at) DESC
                """
            ).fetchall()
        return [dict(row) for row in rows]

    def platforms(self) -> list[str]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT DISTINCT platform FROM research_results
                WHERE platform <> '' ORDER BY platform
                """
            ).fetchall()
        return [row[0] for row in rows]

    def result_accounts(self) -> list[str]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT DISTINCT account_id FROM research_results
                WHERE account_id <> '' ORDER BY account_id
                """
            ).fetchall()
        return [row[0] for row in rows]

    def result_keywords(self) -> list[str]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT DISTINCT keyword FROM research_results
                WHERE keyword <> '' ORDER BY keyword COLLATE NOCASE
                """
            ).fetchall()
        return [row[0] for row in rows]

    def source_comparison(
        self,
        *,
        date_a_from: str,
        date_a_to: str,
        date_b_from: str,
        date_b_to: str,
        job_id: str = "",
        keyword: str | list[str] = "",
        platform: str = "",
        account_id: str = "",
    ) -> dict[str, Any]:
        where, params = self._result_filter(
            job_id=job_id,
            keyword=keyword,
            platform=platform,
            account_id=account_id,
        )
        joiner = " AND " if where else " WHERE "

        def counts_for(
            connection: sqlite3.Connection, date_from: str, date_to: str
        ) -> dict[str, int]:
            rows = connection.execute(
                f"""
                SELECT COALESCE(NULLIF(r.platform, ''), '未知平台') AS platform,
                    COUNT(*) AS count
                FROM research_results r
                {where}{joiner}r.collected_date >= ? AND r.collected_date <= ?
                GROUP BY COALESCE(NULLIF(r.platform, ''), '未知平台')
                """,
                [*params, date_from, date_to],
            ).fetchall()
            return {str(row["platform"]): int(row["count"]) for row in rows}

        with self._connect() as connection:
            counts_a = counts_for(connection, date_a_from, date_a_to)
            counts_b = counts_for(connection, date_b_from, date_b_to)

        rows: list[dict[str, Any]] = []
        total_a = sum(counts_a.values())
        total_b = sum(counts_b.values())
        for source in sorted(set(counts_a) | set(counts_b)):
            a_count = counts_a.get(source, 0)
            b_count = counts_b.get(source, 0)
            delta = b_count - a_count
            if a_count == 0 and b_count > 0:
                status = "added"
                change_rate: float | None = None
                movement = "added"
            elif a_count > 0 and b_count == 0:
                status = "removed"
                change_rate = -100.0
                movement = "removed"
            elif delta > 0:
                status = "continued"
                change_rate = round(delta / a_count * 100, 1)
                movement = "increased"
            elif delta < 0:
                status = "continued"
                change_rate = round(delta / a_count * 100, 1)
                movement = "decreased"
            else:
                status = "continued"
                change_rate = 0.0
                movement = "unchanged"
            rows.append(
                {
                    "platform": source,
                    "type": platform_category(source),
                    "a_count": a_count,
                    "b_count": b_count,
                    "delta": delta,
                    "change_rate": change_rate,
                    "status": status,
                    "movement": movement,
                    "a_share": round(a_count / total_a * 100, 1) if total_a else 0.0,
                    "b_share": round(b_count / total_b * 100, 1) if total_b else 0.0,
                }
            )
        status_order = {"added": 0, "removed": 1, "continued": 2}
        rows.sort(
            key=lambda row: (
                status_order[str(row["status"])],
                -abs(int(row["delta"])),
                str(row["platform"]).casefold(),
            )
        )
        return {
            "summary": {
                "a_total": total_a,
                "b_total": total_b,
                "a_sources": len(counts_a),
                "b_sources": len(counts_b),
                "delta": total_b - total_a,
                "added_platforms": sum(row["status"] == "added" for row in rows),
                "removed_platforms": sum(row["status"] == "removed" for row in rows),
                "continued_platforms": sum(row["status"] == "continued" for row in rows),
                "increased_platforms": sum(row["movement"] == "increased" for row in rows),
                "decreased_platforms": sum(row["movement"] == "decreased" for row in rows),
            },
            "rows": rows,
        }

    def result_count(self) -> int:
        with self._connect() as connection:
            return int(connection.execute("SELECT COUNT(*) FROM research_results").fetchone()[0])
