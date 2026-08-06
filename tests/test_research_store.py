from pathlib import Path

from doubao2api.research_store import ResearchStore


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
    assert store.list_results(job_id=job["id"])[0]["job_name"] == "测试"
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
    collected_date = result[0]["collected_date"]
    comparison = store.source_comparison(
        date_a_from=collected_date,
        date_a_to=collected_date,
        date_b_from="2999-01-01",
        date_b_to="2999-12-31",
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
    assert store.result_jobs() == [{"id": job["id"], "name": "测试", "result_count": 1}]
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
