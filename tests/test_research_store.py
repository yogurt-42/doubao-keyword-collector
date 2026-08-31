from datetime import datetime
from pathlib import Path

import pytest

from doubao2api.research_store import ResearchStore


def _today_str() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def test_job_name_generation(tmp_path: Path) -> None:
    store = ResearchStore(tmp_path / "names.sqlite3")

    auto_named = store.create_job(
        name="",
        keywords=["附近美食", "北京景点"],
        account_ids=[],
        prompt_template="{keyword}",
        scheduled_at=None,
        interval_seconds=5,
        account_cooldown_seconds=0,
        max_attempts=1,
    )
    assert auto_named["name"] == f"附近美食-豆包-{_today_str()}"

    custom_named = store.create_job(
        name="品牌调研",
        keywords=["新能源汽车"],
        account_ids=[],
        prompt_template="{keyword}",
        scheduled_at=None,
        interval_seconds=5,
        account_cooldown_seconds=0,
        max_attempts=1,
        ai_platform="deepseek",
    )
    assert custom_named["name"] == f"品牌调研-DeepSeek-{_today_str()}"


def test_result_jobs_returns_keyword_count(tmp_path: Path) -> None:
    store = ResearchStore(tmp_path / "result_jobs.sqlite3")
    store.create_job(
        name="计数测试",
        keywords=["A", "B", "C"],
        account_ids=[],
        prompt_template="{keyword}",
        scheduled_at=None,
        interval_seconds=5,
        account_cooldown_seconds=0,
        max_attempts=1,
    )
    result_jobs = store.result_jobs()
    assert len(result_jobs) == 1
    assert result_jobs[0]["keyword_count"] == 3


def test_job_and_results_lifecycle(tmp_path: Path) -> None:
    store = ResearchStore(tmp_path / "research.sqlite3")
    job = store.create_job(
        name="测试",
        keywords=["关键词 A", "关键词 B"],
        account_ids=["account-1"],
        prompt_template="调研 {keyword}",
        scheduled_at=None,
        interval_seconds=60,
        account_cooldown_seconds=120,
        max_attempts=2,
    )
    assert job["total"] == 2
    due = store.due_tasks()
    assert len(due) == 1
    assert due[0]["interval_seconds"] == 60
    assert store.mark_task_running(due[0]["id"], "account-1")
    store.update_task_progress(due[0]["id"], "正在等待豆包回答")
    assert store.get_job(job["id"])["last_error"] == "正在等待豆包回答"
    assert store.get_job(job["id"])["active_details"] == "关键词 A · account-1"
    assert store.get_job(job["id"])["running_tasks"] == 1
    assert store.get_job(job["id"])["progress_percent"] == 0
    second_task_id = store.get_job(job["id"])["tasks"][1]["id"]
    store.update_task_progress(second_task_id, "后续关键词等待账号")
    assert store.get_job(job["id"])["last_error"] == "正在等待豆包回答"
    live_link = {
        "link": "https://example.com/source",
        "platform": "example.com",
        "title": "来源",
    }
    assert store.add_result(
        due[0]["id"],
        item=live_link,
        account_id="account-1",
    )
    assert store.list_results(job_id=job["id"])[0]["job_name"] == job["name"]
    store.complete_task(
        due[0]["id"],
        answer="",
        account_id="account-1",
        links=[live_link],
    )
    result = store.list_results()
    assert result[0]["keyword"] == "关键词 A"
    assert result[0]["platform"] == "example.com"
    assert result[0]["platform_type"] == ""
    assert store.list_results(account_id="account-1") == result
    dashboard = store.result_dashboard(job_id=job["id"])
    assert dashboard["summary"] == {
        "total": 1,
        "jobs": 1,
        "keywords": 1,
        "platforms": 1,
    }
    assert dashboard["platforms"] == [{"platform": "example.com", "type": "", "count": 1}]
    assert dashboard["long_tail"]["total"] == 0
    assert dashboard["long_tail"]["share"] == 0.0
    assert dashboard["long_tail"]["by_platform"] == []
    assert dashboard["long_tail"]["by_type"] == [{"type": "未分类", "count": 1}]
    assert store.result_accounts() == ["account-1"]
    assert store.result_keywords() == ["关键词 A"]
    assert store.list_results(keyword=["关键词 A"]) == result
    assert store.list_results(keyword=["不存在"]) == []
    comparison = store.source_comparison(
        job_ids_a=[job["id"]],
        job_ids_b=["nonexistent-job"],
    )
    assert comparison["summary"]["a_total"] == 1
    assert comparison["summary"]["b_total"] == 0
    assert comparison["summary"]["a_sources"] == 1
    assert comparison["summary"]["b_sources"] == 0
    assert comparison["summary"]["delta"] == -1
    assert comparison["summary"]["removed_platforms"] == 1
    assert comparison["rows"][0]["status"] == "removed"
    assert comparison["rows"][0]["a_share"] == 100.0
    assert comparison["rows"][0]["type"] == ""
    current = store.get_job(job["id"])
    assert current["completed"] == 1
    assert current["running_tasks"] == 0
    assert current["progress_percent"] == 50
    assert current["last_error"] == "“关键词 A”已完成，采集 1 条参考资料"

    store.pause_account("account-1", 60, "账号尚未登录")
    assert store.account_runtime("account-1")["paused_until"]
    store.resume_account("account-1")
    runtime = store.account_runtime("account-1")
    assert runtime["paused_until"] is None
    assert runtime["pause_reason"] == ""
    store.rename_account_references("account-1", "account-renamed")
    assert store.get_job(job["id"])["account_ids"] == ["account-renamed"]
    assert store.list_results()[0]["account_id"] == "account-renamed"
    store.remove_account_references("account-renamed")
    assert store.get_job(job["id"])["account_ids"] == []
    assert store.result_jobs() == [{"id": job["id"], "name": job["name"], "keyword_count": 2}]
    store.delete_job(job["id"])
    assert store.list_jobs() == []
    assert store.list_results() == []


def test_job_with_failed_keyword_is_failed_history(tmp_path: Path) -> None:
    store = ResearchStore(tmp_path / "failed.sqlite3")
    job = store.create_job(
        name="失败任务",
        keywords=["关键词"],
        account_ids=[],
        prompt_template="{keyword}",
        scheduled_at=None,
        interval_seconds=5,
        account_cooldown_seconds=0,
        max_attempts=1,
    )
    task = store.due_tasks()[0]
    assert store.mark_task_running(task["id"], "账号1")
    store.fail_or_retry_task(task["id"], "回答超时", retry=False)
    assert store.get_job(job["id"])["status"] == "failed"


def test_existing_platforms_are_reclassified_on_startup(tmp_path: Path) -> None:
    database = tmp_path / "migration.sqlite3"
    store = ResearchStore(database)
    job = store.create_job(
        name="平台修正",
        keywords=["关键词"],
        account_ids=[],
        prompt_template="{keyword}",
        scheduled_at=None,
        interval_seconds=5,
        account_cooldown_seconds=0,
        max_attempts=1,
    )
    task = store.due_tasks()[0]
    assert store.add_result(
        task["id"],
        item={
            "link": "https://m.to8to.com/yezhu/z1.html",
            "platform": "m.to8to.com",
            "title": "来源",
        },
        account_id="账号1",
    )

    reopened = ResearchStore(database)

    assert reopened.list_results(job_id=job["id"])[0]["platform"] == "土巴兔"
    assert reopened.list_results(job_id=job["id"])[0]["platform_type"] == "生活/房产/汽车门户"


def test_cancel_job_also_cancels_running_task(tmp_path: Path) -> None:
    store = ResearchStore(tmp_path / "cancel.sqlite3")
    job = store.create_job(
        name="取消任务",
        keywords=["关键词"],
        account_ids=["账号1"],
        prompt_template="{keyword}",
        scheduled_at=None,
        interval_seconds=5,
        account_cooldown_seconds=0,
        max_attempts=1,
    )
    task = store.due_tasks()[0]
    assert store.mark_task_running(task["id"], "账号1")

    store.set_job_status(job["id"], "cancelled")

    updated = store.get_job(job["id"])
    assert updated["status"] == "cancelled"
    assert updated["tasks"][0]["status"] == "cancelled"
    assert updated["tasks"][0]["finished_at"] is not None


def test_database_indexes_exist(tmp_path: Path) -> None:
    import sqlite3

    database = tmp_path / "indexes.sqlite3"
    ResearchStore(database)
    connection = sqlite3.connect(database)
    try:
        indexes = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'index'"
            ).fetchall()
        }
    finally:
        connection.close()

    expected = {
        "idx_research_tasks_due",
        "idx_research_results_keyword",
        "idx_research_results_job",
        "idx_research_results_account",
        "idx_research_results_date",
        "idx_research_results_type",
        "idx_account_runtime_paused",
    }
    assert expected.issubset(indexes), f"缺少索引: {expected - indexes}"


def _create_repeat_job(store: ResearchStore, **overrides: object) -> dict:
    defaults = {
        "name": "重复采集",
        "keywords": ["关键词 A", "关键词 B"],
        "account_ids": [],
        "prompt_template": "{keyword}",
        "scheduled_at": None,
        "interval_seconds": 10,
        "account_cooldown_seconds": 0,
        "max_attempts": 1,
        "repeat_count": 3,
    }
    defaults.update(overrides)
    return store.create_job(**defaults)


def test_repeat_job_expands_tasks_round_interleaved(tmp_path: Path) -> None:
    store = ResearchStore(tmp_path / "repeat.sqlite3")
    job = _create_repeat_job(store)

    assert job["repeat_count"] == 3
    assert job["total"] == 6
    tasks = job["tasks"]
    # 轮次交错：全部关键词第 1 轮 → 第 2 轮 → 第 3 轮
    assert [(task["keyword"], task["round_number"]) for task in tasks] == [
        ("关键词 A", 1),
        ("关键词 B", 1),
        ("关键词 A", 2),
        ("关键词 B", 2),
        ("关键词 A", 3),
        ("关键词 B", 3),
    ]
    assert [task["position"] for task in tasks] == list(range(6))
    # 启动间隔按展开后的顺序递增
    scheduled = [datetime.fromisoformat(task["scheduled_at"]) for task in tasks]
    assert all(later > earlier for earlier, later in zip(scheduled, scheduled[1:], strict=False))


def test_repeat_job_defaults_to_single_round(tmp_path: Path) -> None:
    store = ResearchStore(tmp_path / "single.sqlite3")
    job = store.create_job(
        name="单次",
        keywords=["关键词"],
        account_ids=[],
        prompt_template="{keyword}",
        scheduled_at=None,
        interval_seconds=5,
        account_cooldown_seconds=0,
        max_attempts=1,
    )

    assert job["repeat_count"] == 1
    assert job["total"] == 1
    assert job["tasks"][0]["round_number"] == 1


def test_repeat_job_rejects_out_of_range_repeat_count(tmp_path: Path) -> None:
    store = ResearchStore(tmp_path / "range.sqlite3")
    for bad_value in (0, 51):
        with pytest.raises(ValueError, match="采集次数"):
            _create_repeat_job(store, repeat_count=bad_value)


def test_repeat_job_rejects_too_many_units(tmp_path: Path) -> None:
    store = ResearchStore(tmp_path / "units.sqlite3")
    with pytest.raises(ValueError, match="采集单元"):
        _create_repeat_job(store, keywords=[f"关键词 {index}" for index in range(4000)])


def test_repeat_results_deduplicate_within_task_but_not_across_rounds(
    tmp_path: Path,
) -> None:
    store = ResearchStore(tmp_path / "rounds.sqlite3")
    job = _create_repeat_job(store, keywords=["关键词 A"], repeat_count=2)
    tasks = job["tasks"]
    assert [task["round_number"] for task in tasks] == [1, 2]
    item = {"link": "https://example.com/a", "platform": "示例", "title": "来源"}

    assert store.add_result(tasks[0]["id"], item=item, account_id="账号1")
    # 同一轮（同一 task）内重复链接被忽略
    assert not store.add_result(tasks[0]["id"], item=item, account_id="账号1")
    # 跨轮次（不同 task）同一链接允许重复保存，供频次统计
    assert store.add_result(tasks[1]["id"], item=item, account_id="账号1")

    results = store.list_results(job_id=job["id"])
    assert len(results) == 2
    assert {result["round_number"] for result in results} == {1, 2}


def test_round_interval_delays_later_rounds(tmp_path: Path) -> None:
    store = ResearchStore(tmp_path / "round_interval.sqlite3")
    job = _create_repeat_job(
        store,
        keywords=["关键词 A", "关键词 B"],
        interval_seconds=10,
        repeat_count=3,
        round_interval_seconds=300,
    )
    assert job["round_interval_seconds"] == 300

    scheduled = [datetime.fromisoformat(task["scheduled_at"]) for task in job["tasks"]]
    base = scheduled[0]
    offsets = [(value - base).total_seconds() for value in scheduled]
    # 第 1 轮：0、10；第 2 轮整体 +300：320、330；第 3 轮整体 +600：640、650
    assert offsets == [0, 10, 320, 330, 640, 650]


def test_round_interval_defaults_to_zero(tmp_path: Path) -> None:
    store = ResearchStore(tmp_path / "no_interval.sqlite3")
    job = _create_repeat_job(store, repeat_count=2)
    assert job["round_interval_seconds"] == 0

    scheduled = [datetime.fromisoformat(task["scheduled_at"]) for task in job["tasks"]]
    offsets = [(value - scheduled[0]).total_seconds() for value in scheduled]
    assert offsets == [0, 10, 20, 30]


def test_round_interval_rejects_out_of_range(tmp_path: Path) -> None:
    store = ResearchStore(tmp_path / "bad_interval.sqlite3")
    for bad_value in (-1, 86401):
        with pytest.raises(ValueError, match="轮次间等待时间"):
            _create_repeat_job(store, round_interval_seconds=bad_value)


def test_repeat_columns_added_to_existing_database(tmp_path: Path) -> None:
    import sqlite3

    if sqlite3.sqlite_version_info < (3, 35, 0):
        pytest.skip("当前 SQLite 版本不支持 DROP COLUMN，无法模拟旧库")
    database = tmp_path / "legacy.sqlite3"
    store = ResearchStore(database)
    _create_repeat_job(store, repeat_count=2)
    del store

    connection = sqlite3.connect(database)
    try:
        connection.execute("ALTER TABLE research_jobs DROP COLUMN repeat_count")
        connection.execute("ALTER TABLE research_jobs DROP COLUMN round_interval_seconds")
        connection.execute("ALTER TABLE research_tasks DROP COLUMN round_number")
        connection.execute("ALTER TABLE research_job_templates DROP COLUMN repeat_count")
        connection.execute("ALTER TABLE research_job_templates DROP COLUMN round_interval_seconds")
        connection.commit()
    finally:
        connection.close()

    reopened = ResearchStore(database)
    connection = sqlite3.connect(database)
    try:
        job_columns = {row[1] for row in connection.execute("PRAGMA table_info(research_jobs)")}
        task_columns = {row[1] for row in connection.execute("PRAGMA table_info(research_tasks)")}
        template_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(research_job_templates)")
        }
    finally:
        connection.close()
    assert "repeat_count" in job_columns
    assert "round_interval_seconds" in job_columns
    assert "round_number" in task_columns
    assert "repeat_count" in template_columns
    assert "round_interval_seconds" in template_columns
    # 迁移后旧任务仍可读取，被删除的列按默认值重建
    old_job = reopened.list_jobs()[0]
    assert old_job["repeat_count"] == 1
    assert old_job["round_interval_seconds"] == 0
