from __future__ import annotations

import sys
from contextlib import suppress
from pathlib import Path
from typing import Any

import httpx
import pytest

from doubao2api.update_checker import AssetInfo, UpdateChecker, UpdateInfo


def _release_payload(version: str) -> dict[str, Any]:
    return {
        "tag_name": f"v{version}",
        "name": f"Release v{version}",
        "published_at": "2026-08-17T00:00:00Z",
        "html_url": (
            f"https://github.com/yogurt-42/doubao-keyword-collector/releases/tag/v{version}"
        ),
        "body": f"## v{version}\n\n- 修复了一些问题",
        "assets": [
            {
                "name": f"AI信源采集工具-v{version}.exe",
                "browser_download_url": (
                    f"https://github.com/yogurt-42/doubao-keyword-collector/"
                    f"releases/download/v{version}/AI信源采集工具-v{version}.exe"
                ),
                "size": 12345678,
            },
            {
                "name": f"AI信源采集工具-v{version}-便携版.zip",
                "browser_download_url": (
                    f"https://github.com/yogurt-42/doubao-keyword-collector/"
                    f"releases/download/v{version}/AI信源采集工具-v{version}-便携版.zip"
                ),
                "size": 9876543,
            },
        ],
    }


class _FakeResponse:
    def __init__(
        self,
        payload: dict[str, Any] | None = None,
        *,
        status_code: int = 200,
        headers: dict[str, str] | None = None,
    ) -> None:
        self._payload = payload or {}
        self.status_code = status_code
        self.headers = headers or {}
        self.url = httpx.URL(
            "https://github.com/yogurt-42/doubao-keyword-collector/releases/latest"
        )

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                "error",
                request=None,  # type: ignore[arg-type]
                response=None,  # type: ignore[arg-type]
            )

    def json(self) -> dict[str, Any]:
        return self._payload


def _make_async_get(
    payload: dict[str, Any] | None = None,
    *,
    status_code: int = 200,
    headers: dict[str, str] | None = None,
    exc: Exception | None = None,
):
    async def fake_get(*_args: Any, **_kwargs: Any) -> _FakeResponse:
        if exc is not None:
            raise exc
        return _FakeResponse(payload or {}, status_code=status_code, headers=headers)

    return fake_get


@pytest.mark.parametrize(
    ("current", "latest", "expected"),
    [
        ("1.0.0", "1.0.1", True),
        ("1.0.0", "1.0.0", False),
        ("1.0.1", "1.0.0", False),
        ("1.0.0", "1.0.10", True),
        ("1.0.9", "1.0.10", True),
        ("1.0.10", "1.0.2", False),
        ("1.0.9", "1.1", True),
        ("1.1", "1.0.9", False),
        ("v1.0.0", "v1.0.1", True),
    ],
)
def test_is_newer(current: str, latest: str, expected: bool) -> None:
    checker = UpdateChecker(current_version=current)
    assert checker.is_newer(latest) is expected


async def test_check_latest_parses_release(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(httpx.AsyncClient, "get", _make_async_get(_release_payload("1.0.2")))
    checker = UpdateChecker(current_version="1.0.0")
    info = await checker.check_latest()
    assert info is not None
    assert info.version == "1.0.2"
    assert info.tag_name == "v1.0.2"
    assert "修复了一些问题" in info.release_notes
    assert info.release_url == (
        "https://github.com/yogurt-42/doubao-keyword-collector/releases/tag/v1.0.2"
    )
    assert set(info.assets.keys()) == {"single", "portable"}
    assert info.assets["single"].name == "AI信源采集工具-v1.0.2.exe"
    assert info.assets["portable"].size == 9876543


async def test_fetch_latest_release_ignores_version_comparison(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(httpx.AsyncClient, "get", _make_async_get(_release_payload("1.0.1")))
    checker = UpdateChecker(current_version="1.0.1")
    info = await checker.fetch_latest_release()
    assert info is not None
    assert info.version == "1.0.1"
    assert info.tag_name == "v1.0.1"


async def test_fetch_latest_release_fallback_on_rate_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    responses = [
        _FakeResponse(
            {"message": "API rate limit exceeded"},
            status_code=403,
        ),
        _FakeResponse(
            status_code=302,
            headers={"location": "/yogurt-42/doubao-keyword-collector/releases/tag/v1.0.2"},
        ),
    ]
    call_index = 0

    async def fake_get(*_args: Any, **_kwargs: Any) -> _FakeResponse:
        nonlocal call_index
        response = responses[call_index]
        call_index += 1
        return response

    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)
    checker = UpdateChecker(current_version="1.0.0")
    info = await checker.fetch_latest_release()
    assert info is not None
    assert info.version == "1.0.2"
    assert info.tag_name == "v1.0.2"
    assert "请求频率已达上限" in info.release_notes
    assert info.release_url == (
        "https://github.com/yogurt-42/doubao-keyword-collector/releases/tag/v1.0.2"
    )
    assert "single" in info.assets
    assert "portable" in info.assets


async def test_check_latest_uses_fallback_when_api_rate_limited(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    responses = [
        _FakeResponse(
            {"message": "API rate limit exceeded"},
            status_code=403,
        ),
        _FakeResponse(
            status_code=302,
            headers={"location": "/yogurt-42/doubao-keyword-collector/releases/tag/v1.0.2"},
        ),
    ]
    call_index = 0

    async def fake_get(*_args: Any, **_kwargs: Any) -> _FakeResponse:
        nonlocal call_index
        response = responses[call_index]
        call_index += 1
        return response

    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)
    checker = UpdateChecker(current_version="1.0.0")
    info = await checker.check_latest()
    assert info is not None
    assert info.version == "1.0.2"


async def test_check_latest_no_update(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(httpx.AsyncClient, "get", _make_async_get(_release_payload("1.0.1")))
    checker = UpdateChecker(current_version="1.0.1")
    assert await checker.check_latest() is None


async def test_check_latest_ignored_version(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(httpx.AsyncClient, "get", _make_async_get(_release_payload("1.0.2")))
    checker = UpdateChecker(current_version="1.0.0")
    assert await checker.check_latest(ignored_version="1.0.2") is None
    assert await checker.check_latest(ignored_version="v1.0.2") is None


async def test_check_latest_api_error_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        httpx.AsyncClient,
        "get",
        _make_async_get(exc=httpx.HTTPError("network down")),
    )
    checker = UpdateChecker(current_version="1.0.0")
    assert await checker.check_latest() is None


async def test_check_latest_bad_response_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        httpx.AsyncClient,
        "get",
        _make_async_get(exc=httpx.HTTPError("server error")),
    )
    checker = UpdateChecker(current_version="1.0.0")
    assert await checker.check_latest() is None


def _patch_runtime(
    monkeypatch: pytest.MonkeyPatch, exe_dir: Path, portable: bool, single: bool
) -> None:
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(exe_dir / "AI信源采集工具.exe"), raising=False)
    if single:
        monkeypatch.setattr(sys, "_MEIPASS", str(exe_dir / "_MEI12345"), raising=False)
    else:
        monkeypatch.delattr(sys, "_MEIPASS", raising=False)
    if portable:
        (exe_dir / "_internal").mkdir(exist_ok=True)
    else:
        with suppress(FileNotFoundError):
            (exe_dir / "_internal").rmdir()


def test_recommended_asset_portable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_runtime(monkeypatch, tmp_path, portable=True, single=False)
    checker = UpdateChecker(current_version="1.0.0")
    info = UpdateInfo(
        version="1.0.2",
        tag_name="v1.0.2",
        title="",
        published_at="",
        release_notes="",
        release_url="http://release",
        assets={
            "single": AssetInfo(name="a.exe", url="http://exe", size=1),
            "portable": AssetInfo(name="b.zip", url="http://zip", size=2),
        },
    )
    asset = checker.recommended_asset(info)
    assert asset is not None
    assert asset.name == "b.zip"


def test_recommended_asset_single(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_runtime(monkeypatch, tmp_path, portable=False, single=True)
    checker = UpdateChecker(current_version="1.0.0")
    info = UpdateInfo(
        version="1.0.2",
        tag_name="v1.0.2",
        title="",
        published_at="",
        release_notes="",
        release_url="http://release",
        assets={
            "single": AssetInfo(name="a.exe", url="http://exe", size=1),
            "portable": AssetInfo(name="b.zip", url="http://zip", size=2),
        },
    )
    asset = checker.recommended_asset(info)
    assert asset is not None
    assert asset.name == "a.exe"


def test_recommended_asset_unknown(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "frozen", False, raising=False)
    checker = UpdateChecker(current_version="1.0.0")
    info = UpdateInfo(
        version="1.0.2",
        tag_name="v1.0.2",
        title="",
        published_at="",
        release_notes="",
        release_url="http://release",
        assets={"single": AssetInfo(name="a.exe", url="http://exe", size=1)},
    )
    assert checker.recommended_asset(info) is None


def test_recommended_asset_missing_asset(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_runtime(monkeypatch, tmp_path, portable=True, single=False)
    checker = UpdateChecker(current_version="1.0.0")
    info = UpdateInfo(
        version="1.0.2",
        tag_name="v1.0.2",
        title="",
        published_at="",
        release_notes="",
        release_url="http://release",
        assets={"single": AssetInfo(name="a.exe", url="http://exe", size=1)},
    )
    assert checker.recommended_asset(info) is None


async def test_check_latest_partial_assets_ignores_unmatched(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = _release_payload("1.0.2")
    payload["assets"] = [payload["assets"][0]]  # 只保留 exe
    monkeypatch.setattr(httpx.AsyncClient, "get", _make_async_get(payload))
    checker = UpdateChecker(current_version="1.0.0")
    info = await checker.check_latest()
    assert info is not None
    assert set(info.assets.keys()) == {"single"}
    assert "portable" not in info.assets
