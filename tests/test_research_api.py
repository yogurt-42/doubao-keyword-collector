from io import BytesIO
from pathlib import Path

from fastapi.testclient import TestClient
from openpyxl import Workbook, load_workbook

from doubao2api.config import RuntimeConfig, SettingsStore
from doubao2api.server import create_app


def make_client(tmp_path: Path) -> TestClient:
    return TestClient(
        create_app(
            store=SettingsStore(tmp_path),
            runtime=RuntimeConfig(open_admin_browser=False),
        ),
        client=("127.0.0.1", 50000),
    )


def keyword_workbook() -> bytes:
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(["关键词"])
    sheet.append(["新能源汽车"])
    sheet.append(["家庭储能"])
    output = BytesIO()
    workbook.save(output)
    workbook.close()
    return output.getvalue()


def test_import_create_and_export(tmp_path: Path) -> None:
    with make_client(tmp_path) as client:
        imported = client.post(
            "/admin/api/research/import",
            files={
                "file": (
                    "keywords.xlsx",
                    keyword_workbook(),
                    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                )
            },
        )
        assert imported.status_code == 200
        assert imported.json()["keywords"] == ["新能源汽车", "家庭储能"]

        created = client.post(
            "/admin/api/research/jobs",
            json={
                "name": "测试任务",
                "keywords": imported.json()["keywords"],
                "prompt_template": "请调研 {keyword}",
                "scheduled_at": "2099-01-01T00:00:00+08:00",
                "interval_seconds": 60,
                "account_cooldown_seconds": 120,
            },
        )
        assert created.status_code == 200
        assert created.json()["total"] == 2

        store = client.app.state.research_store
        task = store.get_job(created.json()["id"])["tasks"][0]
        assert store.mark_task_running(task["id"], "default")
        store.complete_task(
            task["id"],
            answer="",
            account_id="default",
            links=[
                {
                    "link": "https://example.com/report",
                    "platform": "example.com",
                    "title": "报告",
                }
            ],
        )

        exported = client.get("/admin/api/research/results/export.xlsx")
        assert exported.status_code == 200
        workbook = load_workbook(BytesIO(exported.content), read_only=True)
        rows = list(workbook.active.values)
        workbook.close()
        assert rows[0] == (
            "任务",
            "日期",
            "提问关键词",
            "资料名称",
            "检索资料链接",
            "检索资料平台",
            "平台类型",
        )
        assert rows[1][3] == "报告"
        assert rows[1] == (
            "测试任务",
            task["created_at"][:10],
            "新能源汽车",
            "报告",
            "https://example.com/report",
            "example.com",
            None,
        )
