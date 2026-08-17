from __future__ import annotations

import sys
from contextlib import suppress
from pathlib import Path
from typing import Any

import httpx
import pytest

from doubao2api.update_checker import (
    AssetInfo,
    DownloadError,
    UpdateChecker,
    UpdateInfo,
    load_cached_update_info,
    save_cached_update_info,
    update_info_from_dict,
    update_info_to_dict,
)


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
                "name": f"doubao-keyword-collector-v{version}.exe",
                "browser_download_url": (
                    f"https://github.com/yogurt-42/doubao-keyword-collector/"
                    f"releases/download/v{version}/doubao-keyword-collector-v{version}.exe"
                ),
                "size": 12345678,
            },
            {
                "name": f"doubao-keyword-collector-v{version}-portable.zip",
                "browser_download_url": (
                    f"https://github.com/yogurt-42/doubao-keyword-collector/"
                    f"releases/download/v{version}/doubao-keyword-collector-v{version}-portable.zip"
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
        text: str | None = None,
    ) -> None:
        self._payload = payload or {}
        self.status_code = status_code
        self.headers = headers or {}
        self.url = httpx.URL(
            "https://github.com/yogurt-42/doubao-keyword-collector/releases/latest"
        )
        self._text = text

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                "error",
                request=None,  # type: ignore[arg-type]
                response=None,  # type: ignore[arg-type]
            )

    def json(self) -> dict[str, Any]:
        return self._payload

    @property
    def text(self) -> str:
        if self._text is not None:
            return self._text
        return ""


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
    assert info.assets["single"].name == "doubao-keyword-collector-v1.0.2.exe"
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
    monkeypatch.setattr(
        sys, "executable", str(exe_dir / "doubao-keyword-collector.exe"), raising=False
    )
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


class _FakeStreamResponse:
    def __init__(self, content: bytes, *, status_code: int = 200) -> None:
        self._content = content
        self.status_code = status_code
        self.headers = {"content-length": str(len(content))}

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                "error",
                request=None,  # type: ignore[arg-type]
                response=None,  # type: ignore[arg-type]
            )

    async def aiter_bytes(self, *, chunk_size: int = 8192) -> Any:
        for i in range(0, len(self._content), chunk_size):
            yield self._content[i : i + chunk_size]

    async def __aenter__(self) -> _FakeStreamResponse:
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None


class _FakeClient:
    def __init__(self, responses: dict[str, Any]) -> None:
        self._responses = responses

    async def get(self, url: str, **kwargs: Any) -> _FakeResponse:
        response = self._responses.get("get", {}).get(url)
        if response is None:
            raise httpx.HTTPError(f"Unexpected GET URL: {url}")
        if isinstance(response, Exception):
            raise response
        return response

    def stream(self, method: str, url: str, **kwargs: Any) -> _FakeStreamResponse:
        assert method == "GET"
        response = self._responses.get("stream", {}).get(url)
        if response is None:
            raise httpx.HTTPError(f"Unexpected stream URL: {url}")
        if isinstance(response, Exception):
            raise response
        return response

    async def __aenter__(self) -> _FakeClient:
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None


def _make_fake_client(responses: dict[str, Any]) -> _FakeClient:
    return _FakeClient(responses)


def _patch_httpx_client(monkeypatch: pytest.MonkeyPatch, responses: dict[str, Any]) -> None:
    def fake_async_client(*args: Any, **kwargs: Any) -> _FakeClient:
        return _make_fake_client(responses)

    monkeypatch.setattr(httpx, "AsyncClient", fake_async_client)


@pytest.fixture
def sample_update_info() -> UpdateInfo:
    return UpdateInfo(
        version="1.0.2",
        tag_name="v1.0.2",
        title="Release v1.0.2",
        published_at="2026-08-17T00:00:00Z",
        release_notes="- 修复了一些问题",
        release_url="https://example.com/release",
        assets={
            "single": AssetInfo(
                name="doubao-keyword-collector-v1.0.2.exe",
                url="https://example.com/single.exe",
                size=0,
            ),
            "portable": AssetInfo(
                name="doubao-keyword-collector-v1.0.2-portable.zip",
                url="https://example.com/portable.zip",
                size=0,
            ),
        },
    )


async def test_download_asset_success(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    sample_update_info: UpdateInfo,
) -> None:
    content = b"MZ" + b"A" * 8  # fake exe with MZ header
    sha256_hash = "not-checked-in-this-test"
    responses = {
        "stream": {"https://example.com/single.exe": _FakeStreamResponse(content)},
        "get": {
            "https://example.com/single.exe.sha256": _FakeResponse(
                {"hash": sha256_hash}, status_code=404
            )
        },
    }
    _patch_httpx_client(monkeypatch, responses)

    checker = UpdateChecker(current_version="1.0.1")
    progress_calls: list[tuple[int, int]] = []

    result = await checker.download_asset(
        sample_update_info,
        sample_update_info.assets["single"],
        tmp_path,
        variant="single",
        progress_callback=lambda d, t: progress_calls.append((d, t)),
    )

    assert result.path.exists()
    assert result.path.read_bytes() == content
    assert result.verification_method == "fallback"
    assert result.verified
    assert progress_calls
    assert progress_calls[-1][0] == len(content)


async def test_download_asset_with_sha256(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    sample_update_info: UpdateInfo,
) -> None:
    import hashlib

    content = b"fake zip content"
    sha256_hash = hashlib.sha256(content).hexdigest()
    responses = {
        "stream": {"https://example.com/portable.zip": _FakeStreamResponse(content)},
        "get": {
            "https://example.com/portable.zip.sha256": _FakeResponse(
                {}, text=f"{sha256_hash}  doubao-keyword-collector-v1.0.2-portable.zip"
            )
        },
    }
    _patch_httpx_client(monkeypatch, responses)

    checker = UpdateChecker(current_version="1.0.1")
    result = await checker.download_asset(
        sample_update_info,
        sample_update_info.assets["portable"],
        tmp_path,
        variant="portable",
    )

    assert result.verified
    assert result.verification_method == "sha256"
    assert result.sha256_expected == sha256_hash


async def test_download_asset_sha256_mismatch_raises(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    sample_update_info: UpdateInfo,
) -> None:
    content = b"fake zip content"
    responses = {
        "stream": {"https://example.com/portable.zip": _FakeStreamResponse(content)},
        "get": {"https://example.com/portable.zip.sha256": _FakeResponse({}, text="0" * 64)},
    }
    _patch_httpx_client(monkeypatch, responses)

    checker = UpdateChecker(current_version="1.0.1")
    with pytest.raises(DownloadError, match="SHA256"):
        await checker.download_asset(
            sample_update_info,
            sample_update_info.assets["portable"],
            tmp_path,
            variant="portable",
        )

    # 校验失败时不应保留目标文件
    assert not (tmp_path / sample_update_info.assets["portable"].name).exists()


async def test_download_asset_fallback_zip_invalid(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    sample_update_info: UpdateInfo,
) -> None:
    content = b"not a zip"
    responses = {
        "stream": {"https://example.com/portable.zip": _FakeStreamResponse(content)},
        "get": {"https://example.com/portable.zip.sha256": _FakeResponse({}, status_code=404)},
    }
    _patch_httpx_client(monkeypatch, responses)

    checker = UpdateChecker(current_version="1.0.1")
    with pytest.raises(DownloadError, match="ZIP"):
        await checker.download_asset(
            sample_update_info,
            sample_update_info.assets["portable"],
            tmp_path,
            variant="portable",
        )


async def test_match_assets_includes_sha256(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(httpx.AsyncClient, "get", _make_async_get(_release_payload("1.0.2")))
    payload = _release_payload("1.0.2")
    payload["assets"].extend(
        [
            {
                "name": "doubao-keyword-collector-v1.0.2.exe.sha256",
                "browser_download_url": "https://example.com/single.exe.sha256",
                "size": 64,
            },
            {
                "name": "doubao-keyword-collector-v1.0.2-portable.zip.sha256",
                "browser_download_url": "https://example.com/portable.zip.sha256",
                "size": 64,
            },
        ]
    )
    monkeypatch.setattr(httpx.AsyncClient, "get", _make_async_get(payload))
    checker = UpdateChecker(current_version="1.0.0")
    info = await checker.fetch_latest_release()
    assert info is not None
    assert "single_sha256" in info.assets
    assert "portable_sha256" in info.assets
    assert info.assets["single_sha256"].name.endswith(".sha256")


def test_update_info_roundtrip(tmp_path: Path) -> None:
    info = UpdateInfo(
        version="1.0.2",
        tag_name="v1.0.2",
        title="t",
        published_at="2026-08-17T00:00:00Z",
        release_notes="notes",
        release_url="https://example.com",
        assets={
            "single": AssetInfo(name="a.exe", url="https://a.exe", size=1),
        },
    )
    save_cached_update_info(tmp_path, info)
    loaded = load_cached_update_info(tmp_path)
    assert loaded is not None
    assert loaded.version == info.version
    assert loaded.assets["single"].name == "a.exe"
    assert update_info_to_dict(info) == update_info_to_dict(loaded)


def test_update_info_from_dict_missing_version_returns_none() -> None:
    assert update_info_from_dict({}) is None
