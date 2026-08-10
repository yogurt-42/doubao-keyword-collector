from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import pytest

from doubao2api.research_store import (
    ResearchStore,
    _compute_next_run,
    iso_now,
    local_now,
)


def _create_template(store: ResearchStore, **overrides: object) -> dict:
    defaults = {
        "name": "测试模板",
        "keywords": ["关键词 A", "关键词 B"],
        "prompt_template": "调研 {keyword}",
        "interval_seconds": 10,
        "account_cooldown_seconds": 0,
        "max_attempts": 2,
    }
    defaults.update(overrides)
    return store.create_job_template(**defaults)


def _create_schedule(store: ResearchStore, template_id: str, **overrides: object) -> dict:
    defaults = {
        "name": "测试计划",
        "template_id": template_id,
        "schedule_type": "interval",
        "schedule_value": "60",
    }
    defaults.update(overrides)
    return store.create_schedule(**defaults)


class TestComputeNextRun:
    def test_interval_adds_seconds(self) -> None:
        after = local_now()
        result = _compute_next_run("interval", "60", after)
        expected = (after + timedelta(seconds=60)).isoformat(timespec="seconds")
        assert result == expected

    def test_interval_rejects_non_integer(self) -> None:
        with pytest.raises(ValueError, match="间隔秒数必须是整数"):
            _compute_next_run("interval", "abc")

    def test_interval_rejects_zero(self) -> None:
        with pytest.raises(ValueError, match="间隔秒数必须大于 0"):
            _compute_next_run("interval", "0")

    def test_once_returns_normalized_datetime(self) -> None:
        value = (local_now() + timedelta(hours=1)).replace(second=0, microsecond=0)
        result = _compute_next_run("once", value.isoformat())
        assert result == value.isoformat(timespec="seconds")

    def test_daily_same_day_if_future(self) -> None:
        after = local_now().replace(hour=8, minute=0, second=0, microsecond=0)
        result = _compute_next_run("daily", "09:00", after)
        assert result == after.replace(hour=9, minute=0).isoformat(timespec="seconds")

    def test_daily_next_day_if_past(self) -> None:
        after = local_now().replace(hour=10, minute=0, second=0, microsecond=0)
        result = _compute_next_run("daily", "09:00", after)
        expected = (
            (after + timedelta(days=1))
            .replace(hour=9, minute=0, second=0, microsecond=0)
            .isoformat(timespec="seconds")
        )
        assert result == expected

    def test_daily_rejects_invalid_format(self) -> None:
        with pytest.raises(ValueError, match="每日时间格式必须是 HH:MM"):
            _compute_next_run("daily", "9:00:00")

    def test_daily_rejects_out_of_range(self) -> None:
        with pytest.raises(ValueError, match="每日时间超出有效范围"):
            _compute_next_run("daily", "25:00")

    def test_unknown_type_raises(self) -> None:
        with pytest.raises(ValueError, match="不支持的触发类型"):
            _compute_next_run("weekly", "1")


class TestJobTemplateCrud:
    def test_create_and_get_template(self, tmp_path: Path) -> None:
        store = ResearchStore(tmp_path / "research.sqlite3")
        template = _create_template(store)

        assert template["name"] == "测试模板"
        assert template["keywords"] == ["关键词 A", "关键词 B"]
        assert template["prompt_template"] == "调研 {keyword}"
        assert template["interval_seconds"] == 10
        assert template["max_attempts"] == 2

        fetched = store.get_job_template(template["id"])
        assert fetched["id"] == template["id"]

    def test_create_template_rejects_empty_keywords(self, tmp_path: Path) -> None:
        store = ResearchStore(tmp_path / "research.sqlite3")
        with pytest.raises(ValueError, match="请至少填写一个关键词"):
            _create_template(store, keywords=[])

    def test_create_template_rejects_missing_placeholder(self, tmp_path: Path) -> None:
        store = ResearchStore(tmp_path / "research.sqlite3")
        with pytest.raises(ValueError, match="提问模板必须包含"):
            _create_template(store, prompt_template="调研关键词")

    def test_create_template_rejects_bad_max_attempts(self, tmp_path: Path) -> None:
        store = ResearchStore(tmp_path / "research.sqlite3")
        with pytest.raises(ValueError, match="最多尝试次数"):
            _create_template(store, max_attempts=5)

    def test_list_templates_ordered_by_updated_at(self, tmp_path: Path) -> None:
        import time

        store = ResearchStore(tmp_path / "research.sqlite3")
        first = _create_template(store, name="模板 A")
        time.sleep(1)
        second = _create_template(store, name="模板 B")

        templates = store.list_job_templates()
        assert [t["id"] for t in templates] == [second["id"], first["id"]]

    def test_update_template(self, tmp_path: Path) -> None:
        store = ResearchStore(tmp_path / "research.sqlite3")
        template = _create_template(store)
        updated = store.update_job_template(
            template["id"],
            name="更新后模板",
            keywords=["新关键词"],
            prompt_template="分析 {keyword}",
            interval_seconds=20,
            account_cooldown_seconds=5,
            max_attempts=3,
        )

        assert updated["name"] == "更新后模板"
        assert updated["keywords"] == ["新关键词"]
        assert updated["prompt_template"] == "分析 {keyword}"
        assert updated["interval_seconds"] == 20
        assert updated["max_attempts"] == 3

    def test_delete_template(self, tmp_path: Path) -> None:
        store = ResearchStore(tmp_path / "research.sqlite3")
        template = _create_template(store)
        store.delete_job_template(template["id"])

        with pytest.raises(KeyError):
            store.get_job_template(template["id"])


class TestScheduleCrud:
    def test_create_and_get_schedule(self, tmp_path: Path) -> None:
        store = ResearchStore(tmp_path / "research.sqlite3")
        template = _create_template(store)
        schedule = _create_schedule(store, template["id"])

        assert schedule["template_id"] == template["id"]
        assert schedule["template_name"] == template["name"]
        assert schedule["schedule_type"] == "interval"
        assert schedule["schedule_value"] == "60"
        assert schedule["enabled"] == 1
        assert "keywords" in schedule

    def test_create_schedule_rejects_missing_template(self, tmp_path: Path) -> None:
        store = ResearchStore(tmp_path / "research.sqlite3")
        with pytest.raises(KeyError):
            _create_schedule(store, "no-such-id")

    def test_create_schedule_rejects_invalid_type(self, tmp_path: Path) -> None:
        store = ResearchStore(tmp_path / "research.sqlite3")
        template = _create_template(store)
        with pytest.raises(ValueError, match="触发类型必须是"):
            _create_schedule(store, template["id"], schedule_type="weekly")

    def test_due_schedules_only_returns_enabled_and_overdue(self, tmp_path: Path) -> None:
        import time

        store = ResearchStore(tmp_path / "research.sqlite3")
        template = _create_template(store)
        past = _create_schedule(store, template["id"], schedule_value="1")
        future = _create_schedule(store, template["id"], schedule_value="86400")
        disabled = _create_schedule(store, template["id"], schedule_value="1")
        store.toggle_schedule(disabled["id"], False)

        # Wait for the past schedule to become overdue.
        time.sleep(2)

        due = store.due_schedules(limit=10)
        due_ids = {s["id"] for s in due}
        assert past["id"] in due_ids
        assert future["id"] not in due_ids
        assert disabled["id"] not in due_ids

    def test_toggle_schedule_recomputes_when_re_enabling_after_expiry(self, tmp_path: Path) -> None:
        import time

        store = ResearchStore(tmp_path / "research.sqlite3")
        template = _create_template(store)
        schedule = _create_schedule(store, template["id"], schedule_value="1")
        store.toggle_schedule(schedule["id"], False)

        # Wait for the original next_run_at to expire.
        time.sleep(2)

        re_enabled = store.toggle_schedule(schedule["id"], True)
        assert re_enabled["enabled"] == 1
        assert re_enabled["next_run_at"] > iso_now()

    def test_delete_schedule(self, tmp_path: Path) -> None:
        store = ResearchStore(tmp_path / "research.sqlite3")
        template = _create_template(store)
        schedule = _create_schedule(store, template["id"])
        store.delete_schedule(schedule["id"])

        with pytest.raises(KeyError):
            store.get_schedule(schedule["id"])


class TestScheduleExecution:
    def test_create_job_from_schedule_uses_template_latest_config(self, tmp_path: Path) -> None:
        store = ResearchStore(tmp_path / "research.sqlite3")
        template = _create_template(store, keywords=["关键词 A"])
        schedule = _create_schedule(store, template["id"])

        # Update template after schedule creation.
        store.update_job_template(
            template["id"],
            name="更新后模板",
            keywords=["关键词 X", "关键词 Y"],
            prompt_template="分析 {keyword}",
            interval_seconds=20,
            account_cooldown_seconds=0,
            max_attempts=3,
        )

        job = store.create_job_from_schedule(schedule["id"])
        task_keywords = [t["keyword"] for t in job["tasks"]]
        assert task_keywords == ["关键词 X", "关键词 Y"]
        assert job["prompt_template"] == "分析 {keyword}"
        assert job["interval_seconds"] == 20
        assert job["max_attempts"] == 3
        assert job["account_ids"] == []

    def test_advance_schedule_updates_metadata(self, tmp_path: Path) -> None:
        store = ResearchStore(tmp_path / "research.sqlite3")
        template = _create_template(store)
        schedule = _create_schedule(store, template["id"], schedule_value="60")

        job = store.create_job_from_schedule(schedule["id"])
        new_next = _compute_next_run("interval", "60")
        store.advance_schedule(schedule["id"], job["id"], new_next)

        updated = store.get_schedule(schedule["id"])
        assert updated["run_count"] == 1
        assert updated["last_job_id"] == job["id"]
        assert updated["next_run_at"] == new_next

    def test_delete_template_cascades_to_schedules(self, tmp_path: Path) -> None:
        store = ResearchStore(tmp_path / "research.sqlite3")
        template = _create_template(store)
        schedule = _create_schedule(store, template["id"])

        store.delete_job_template(template["id"])

        with pytest.raises(KeyError):
            store.get_schedule(schedule["id"])


class TestSchedulerIntegration:
    def test_check_schedules_triggers_due_schedule(self, tmp_path: Path) -> None:
        import asyncio
        import time

        from doubao2api.research_scheduler import ResearchScheduler

        store = ResearchStore(tmp_path / "research.sqlite3")
        template = _create_template(store)
        schedule = _create_schedule(store, template["id"], schedule_value="1")

        # Wait for the schedule to become overdue.
        time.sleep(2)

        class FakePool:
            def discover_account_ids(self) -> list[str]:
                return []

        scheduler = ResearchScheduler(store, FakePool())  # type: ignore[arg-type]
        asyncio.run(scheduler._check_schedules())

        updated = store.get_schedule(schedule["id"])
        assert updated["run_count"] == 1
        assert updated["last_job_id"]
        assert updated["next_run_at"] > iso_now()
