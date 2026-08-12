from pathlib import Path

from fastapi.testclient import TestClient

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


def test_health_is_free_and_local(tmp_path: Path) -> None:
    with make_client(tmp_path) as client:
        response = client.get("/health")
        assert response.status_code == 200
        assert response.json()["activation_required"] is False
        assert response.json()["open_source"] is True


def test_account_contract(tmp_path: Path) -> None:
    with make_client(tmp_path) as client:
        created = client.post(
            "/admin/api/accounts",
            json={"account_id": "test-account", "start_browser": False},
        )
        assert created.status_code == 200
        assert created.json()["account_id"] == "test-account"
        assert created.json()["environment_exists"] is True

        listing = client.get("/admin/api/accounts").json()
        assert any(account["account_id"] == "test-account" for account in listing["accounts"])

        deleted = client.delete("/admin/api/accounts/test-account")
        assert deleted.status_code == 200
        assert deleted.json()["deleted"] is True


def test_account_tab_hidden_api(tmp_path: Path) -> None:
    with make_client(tmp_path) as client:
        created = client.post(
            "/admin/api/accounts",
            json={"account_id": "hidden-tab-account", "start_browser": False},
        )
        assert created.status_code == 200
        assert created.json()["tab_hidden"] is False

        updated = client.post(
            "/admin/api/accounts/hidden-tab-account/tab-hidden",
            json={"hidden": True},
        )
        assert updated.status_code == 200
        assert updated.json()["tab_hidden"] is True

        listing = client.get("/admin/api/accounts").json()
        account = next(
            item for item in listing["accounts"] if item["account_id"] == "hidden-tab-account"
        )
        assert account["tab_hidden"] is True

        client.delete("/admin/api/accounts/hidden-tab-account")


def test_models_do_not_require_activation(tmp_path: Path) -> None:
    with make_client(tmp_path) as client:
        response = client.get("/v1/models")
        assert response.status_code == 200
        model_ids = {item["id"] for item in response.json()["data"]}
        assert "doubao" in model_ids
