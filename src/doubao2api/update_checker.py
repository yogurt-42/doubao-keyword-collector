from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import tempfile
import zipfile
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

_GITHUB_API_URL = "https://api.github.com/repos/{owner}/{repo}/releases/latest"
_GITHUB_LATEST_RELEASE_URL = "https://github.com/{owner}/{repo}/releases/latest"


@dataclass(frozen=True)
class AssetInfo:
    """Release 中的一个可下载附件。"""

    name: str
    url: str
    size: int


@dataclass(frozen=True)
class UpdateInfo:
    """解析后的最新版本信息。"""

    version: str
    tag_name: str
    title: str
    published_at: str
    release_notes: str
    release_url: str
    assets: dict[str, AssetInfo]


@dataclass(frozen=True)
class DownloadResult:
    """下载并校验后的 asset 结果。"""

    asset: AssetInfo
    path: Path
    size: int
    sha256_expected: str | None
    sha256_actual: str | None
    verified: bool
    verification_method: str  # "sha256" or "fallback"


class DownloadError(RuntimeError):
    """下载或校验 asset 失败。"""

    pass


def _normalize_version(version: str) -> str:
    """去掉版本号前导的 'v' 并清理首尾空白。"""
    return version.lstrip("vV").strip()


def _parse_version(version: str) -> tuple[int, ...]:
    """把版本号拆成整数元组，用于比较。

    只取每个段前面的数字部分，遇到非数字内容即停止该段解析。
    """
    normalized = _normalize_version(version)
    parts: list[int] = []
    for segment in normalized.split("."):
        match = re.match(r"(\d+)", segment)
        parts.append(int(match.group(1)) if match else 0)
    return tuple(parts)


def _is_newer(base: str, target: str) -> bool:
    """判断 `target` 是否比 `base` 新。"""
    return _parse_version(target) > _parse_version(base)


def _detect_variant() -> str:
    """判断当前运行形态。

    返回值：
    - "portable"：便携版（onedir，可执行文件旁有 _internal 目录）
    - "single"：单文件版（PyInstaller one-file）
    - "unknown"：开发环境或其他无法识别的情况
    """
    if not getattr(sys, "frozen", False):
        return "unknown"

    exe_path = Path(sys.executable)
    exe_dir = exe_path.parent

    # 便携版解压后 exe 同级存在一个 _internal 目录
    if (exe_dir / "_internal").is_dir():
        return "portable"

    # 单文件版没有 _internal 目录，但 PyInstaller 会注入 sys._MEIPASS
    if getattr(sys, "_MEIPASS", None):
        return "single"

    return "unknown"


class UpdateChecker:
    """检查 GitHub Releases 是否有新版本。

    优先调用 GitHub API；若触发未认证请求的速率限制，则退化为读取
    `/releases/latest` 的 302 跳转地址来获取版本号，保证用户仍能收到
    更新提示并跳转到 Release 页面。
    """

    def __init__(
        self,
        current_version: str,
        owner: str = "yogurt-42",
        repo: str = "doubao-keyword-collector",
        timeout: float = 5.0,
    ) -> None:
        self.current_version = _normalize_version(current_version)
        self.owner = owner
        self.repo = repo
        self.timeout = timeout

    def _user_agent(self) -> str:
        return f"doubao-keyword-collector/{self.current_version}"

    def is_newer(self, latest_version: str) -> bool:
        """判断远端版本是否比当前版本新。"""
        return _is_newer(self.current_version, latest_version)

    def recommended_asset(self, update_info: UpdateInfo) -> AssetInfo | None:
        """根据当前运行形态推荐要下载的 asset。"""
        variant = _detect_variant()
        if variant == "unknown":
            return None
        return update_info.assets.get(variant)

    def asset_for_variant(self, update_info: UpdateInfo, variant: str) -> AssetInfo | None:
        """获取指定 variant 的 asset（single / portable）。"""
        return update_info.assets.get(variant)

    def _match_assets(
        self, release_assets: list[dict[str, object]], version: str
    ) -> dict[str, AssetInfo]:
        """从 release assets 中匹配单文件 exe、便携版 zip 及其 SHA256 文件。"""
        escaped = re.escape(version)
        patterns = {
            "single": re.compile(rf"doubao-keyword-collector-v{escaped}\.exe"),
            "portable": re.compile(rf"doubao-keyword-collector-v{escaped}-portable\.zip"),
            "single_sha256": re.compile(rf"doubao-keyword-collector-v{escaped}\.exe\.sha256"),
            "portable_sha256": re.compile(
                rf"doubao-keyword-collector-v{escaped}-portable\.zip\.sha256"
            ),
        }
        matched: dict[str, AssetInfo] = {}
        for asset in release_assets:
            name = str(asset.get("name", ""))
            url = str(asset.get("browser_download_url", ""))
            size = int(asset.get("size", 0) or 0)
            for variant, pattern in patterns.items():
                if variant not in matched and pattern.fullmatch(name):
                    matched[variant] = AssetInfo(name=name, url=url, size=size)
        return matched

    def _guessed_assets(self, version: str, tag_name: str) -> dict[str, AssetInfo]:
        """API 不可用时，按固定命名规则推测 asset 下载地址。"""
        base = f"https://github.com/{self.owner}/{self.repo}/releases/download/{tag_name}"
        return {
            "single": AssetInfo(
                name=f"doubao-keyword-collector-v{version}.exe",
                url=f"{base}/doubao-keyword-collector-v{version}.exe",
                size=0,
            ),
            "portable": AssetInfo(
                name=f"doubao-keyword-collector-v{version}-portable.zip",
                url=f"{base}/doubao-keyword-collector-v{version}-portable.zip",
                size=0,
            ),
            "single_sha256": AssetInfo(
                name=f"doubao-keyword-collector-v{version}.exe.sha256",
                url=f"{base}/doubao-keyword-collector-v{version}.exe.sha256",
                size=0,
            ),
            "portable_sha256": AssetInfo(
                name=f"doubao-keyword-collector-v{version}-portable.zip.sha256",
                url=f"{base}/doubao-keyword-collector-v{version}-portable.zip.sha256",
                size=0,
            ),
        }

    def _find_sha256_asset(
        self, update_info: UpdateInfo, asset: AssetInfo, variant: str
    ) -> AssetInfo | None:
        """在 UpdateInfo 中查找对应 asset 的 SHA256 文件。"""
        sha256_variant = f"{variant}_sha256"
        if sha256_variant in update_info.assets:
            return update_info.assets[sha256_variant]
        expected_name = f"{asset.name}.sha256"
        for info in update_info.assets.values():
            if info.name == expected_name:
                return info
        return None

    async def _fetch_text(self, url: str, timeout: float | None = None) -> str | None:
        """获取给定 URL 的文本内容。"""
        try:
            async with httpx.AsyncClient(
                timeout=timeout or self.timeout, follow_redirects=True
            ) as client:
                response = await client.get(url, headers={"User-Agent": self._user_agent()})
                response.raise_for_status()
                return response.text
        except Exception:  # noqa: BLE001
            return None

    def _parse_sha256_text(self, text: str) -> str | None:
        """解析 SHA256 文件内容，返回 64 位十六进制字符串。"""
        if not text:
            return None
        first = text.strip().splitlines()[0].strip()
        parts = first.split()
        candidate = parts[0] if parts else ""
        candidate = candidate.lower()
        if re.fullmatch(r"[0-9a-f]{64}", candidate):
            return candidate
        return None

    def _compute_sha256(self, path: Path) -> str:
        """计算文件 SHA256。"""
        hasher = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(65536), b""):
                hasher.update(chunk)
        return hasher.hexdigest()

    def _is_windows_executable(self, path: Path) -> bool:
        """简单检查文件是否为 Windows PE 可执行文件。"""
        if path.suffix.lower() != ".exe":
            return False
        try:
            with path.open("rb") as stream:
                header = stream.read(2)
            return header == b"MZ"
        except OSError:
            return False

    def _verify_download(
        self,
        path: Path,
        asset: AssetInfo,
        *,
        expected_sha256: str | None = None,
    ) -> DownloadResult:
        """校验已下载文件。

        优先使用 SHA256；未提供时做完整性兜底校验。
        """
        actual_size = path.stat().st_size if path.exists() else 0
        if actual_size == 0:
            raise DownloadError(f"下载文件为空：{path}")
        if asset.size > 0 and actual_size != asset.size:
            raise DownloadError(f"文件大小不匹配：期望 {asset.size}，实际 {actual_size}")

        if expected_sha256:
            actual_sha256 = self._compute_sha256(path)
            if actual_sha256 != expected_sha256.lower():
                raise DownloadError(
                    f"SHA256 校验失败：期望 {expected_sha256}，实际 {actual_sha256}"
                )
            return DownloadResult(
                asset=asset,
                path=path,
                size=actual_size,
                sha256_expected=expected_sha256,
                sha256_actual=actual_sha256,
                verified=True,
                verification_method="sha256",
            )

        # 兜底校验
        if asset.name.lower().endswith(".zip"):
            if not zipfile.is_zipfile(path):
                raise DownloadError(f"ZIP 文件格式异常：{path}")
        elif asset.name.lower().endswith(".exe"):
            if not self._is_windows_executable(path):
                raise DownloadError(f"EXE 文件头异常：{path}")
        else:
            raise DownloadError(f"不支持的文件类型：{asset.name}")

        return DownloadResult(
            asset=asset,
            path=path,
            size=actual_size,
            sha256_expected=None,
            sha256_actual=None,
            verified=True,
            verification_method="fallback",
        )

    async def download_asset(
        self,
        update_info: UpdateInfo,
        asset: AssetInfo,
        output_dir: Path,
        *,
        variant: str | None = None,
        progress_callback: Callable[[int, int], Any] | None = None,
        chunk_size: int = 8192,
        timeout: float | None = None,
    ) -> DownloadResult:
        """下载指定 asset 到 output_dir 并校验。

        下载过程中会调用 progress_callback(downloaded, total)。
        """
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        dest_path = output_dir / asset.name
        temp_path = Path(tempfile.mktemp(prefix=f"{asset.name}.", dir=output_dir))

        sha256_asset = None
        expected_sha256: str | None = None
        if variant:
            sha256_asset = self._find_sha256_asset(update_info, asset, variant)
        if sha256_asset is None:
            sha256_url = f"{asset.url}.sha256"
            expected_sha256 = await self._fetch_text(sha256_url, timeout=timeout)
            if expected_sha256:
                expected_sha256 = self._parse_sha256_text(expected_sha256)
        else:
            sha256_text = await self._fetch_text(sha256_asset.url, timeout=timeout)
            expected_sha256 = self._parse_sha256_text(sha256_text or "")

        try:
            async with (
                httpx.AsyncClient(timeout=timeout or 300.0, follow_redirects=True) as client,
                client.stream(
                    "GET", asset.url, headers={"User-Agent": self._user_agent()}
                ) as response,
            ):
                response.raise_for_status()
                total = int(response.headers.get("content-length", asset.size) or 0)
                downloaded = 0
                with temp_path.open("wb") as stream:
                    async for chunk in response.aiter_bytes(chunk_size=chunk_size):
                        if not chunk:
                            continue
                        stream.write(chunk)
                        downloaded += len(chunk)
                        if progress_callback:
                            progress_callback(downloaded, total)
            os.replace(temp_path, dest_path)
            return self._verify_download(dest_path, asset, expected_sha256=expected_sha256)
        except DownloadError:
            with suppress(FileNotFoundError):
                temp_path.unlink()
            with suppress(FileNotFoundError):
                dest_path.unlink()
            raise
        except Exception as exc:  # noqa: BLE001
            with suppress(FileNotFoundError):
                temp_path.unlink()
            raise DownloadError(f"下载失败：{exc}") from exc

    def _parse_release_data(self, data: dict[str, Any]) -> UpdateInfo:
        version = _normalize_version(str(data.get("tag_name", "")))
        tag_name = str(data.get("tag_name", ""))
        return UpdateInfo(
            version=version,
            tag_name=tag_name,
            title=str(data.get("name", "")),
            published_at=str(data.get("published_at", "")),
            release_notes=str(data.get("body") or ""),
            release_url=str(data.get("html_url", "")),
            assets=self._match_assets(data.get("assets", []), version),
        )

    async def _fetch_from_api(self) -> UpdateInfo | None:
        """通过 GitHub API 获取最新 release。"""
        url = _GITHUB_API_URL.format(owner=self.owner, repo=self.repo)
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": self._user_agent(),
        }
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.get(url, headers=headers)
                # 未认证请求触发速率限制时返回 403
                if response.status_code == 403 and "rate limit" in response.text.lower():
                    return None
                response.raise_for_status()
                data = response.json()
        except Exception:  # noqa: BLE001
            return None

        latest_version = _normalize_version(str(data.get("tag_name", "")))
        if not latest_version:
            return None
        return self._parse_release_data(data)

    async def _fetch_from_release_page_redirect(self) -> UpdateInfo | None:
        """通过 GitHub Release 页面 302 跳转地址获取版本号（退化方案）。"""
        url = _GITHUB_LATEST_RELEASE_URL.format(owner=self.owner, repo=self.repo)
        headers = {"User-Agent": self._user_agent()}
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.get(url, headers=headers, follow_redirects=False)
                if response.status_code not in (301, 302, 303, 307, 308):
                    return None
                location = response.headers.get("location", "")
                tag_name = Path(location).name
                if not tag_name:
                    return None
                version = _normalize_version(tag_name)
                release_url = str(response.url.join(location))
                return UpdateInfo(
                    version=version,
                    tag_name=tag_name,
                    title="",
                    published_at="",
                    release_notes=(
                        "GitHub API 请求频率已达上限，更新内容暂无法显示，请前往 Release 页面查看。"
                    ),
                    release_url=release_url,
                    assets=self._guessed_assets(version, tag_name),
                )
        except Exception:  # noqa: BLE001
            return None

    async def check_latest(
        self,
        ignored_version: str | None = None,
    ) -> UpdateInfo | None:
        """查询 GitHub 最新 release，如果有更新则返回信息，否则返回 None。

        任何网络或解析异常都会被吞掉并返回 None，避免在启动时打扰用户。
        """
        info = await self._fetch_from_api()
        if info is None:
            info = await self._fetch_from_release_page_redirect()
        if info is None:
            return None

        if not self.is_newer(info.version):
            return None

        if ignored_version and _normalize_version(ignored_version) == info.version:
            return None

        return info

    async def fetch_latest_release(self) -> UpdateInfo | None:
        """查询 GitHub 最新 release 信息，不比较版本，仅用于展示。

        任何网络或解析异常都会被吞掉并返回 None。
        """
        info = await self._fetch_from_api()
        if info is None:
            info = await self._fetch_from_release_page_redirect()
        return info


def update_info_to_dict(info: UpdateInfo) -> dict[str, Any]:
    """把 UpdateInfo 序列化为可 JSON 持久化的字典。"""
    return {
        "version": info.version,
        "tag_name": info.tag_name,
        "title": info.title,
        "published_at": info.published_at,
        "release_notes": info.release_notes,
        "release_url": info.release_url,
        "assets": {
            variant: {"name": asset.name, "url": asset.url, "size": asset.size}
            for variant, asset in info.assets.items()
        },
    }


def update_info_from_dict(data: dict[str, Any]) -> UpdateInfo | None:
    """从字典反序列化 UpdateInfo；字段缺失时返回 None。"""
    version = data.get("version")
    if not version:
        return None
    assets_data = data.get("assets") or {}
    assets: dict[str, AssetInfo] = {}
    for variant, asset in assets_data.items():
        if isinstance(asset, dict) and asset.get("name") and asset.get("url"):
            assets[str(variant)] = AssetInfo(
                name=str(asset["name"]),
                url=str(asset["url"]),
                size=int(asset.get("size", 0) or 0),
            )
    return UpdateInfo(
        version=str(version),
        tag_name=str(data.get("tag_name", version)),
        title=str(data.get("title", "")),
        published_at=str(data.get("published_at", "")),
        release_notes=str(data.get("release_notes", "")),
        release_url=str(data.get("release_url", "")),
        assets=assets,
    )


def cached_update_info_path(data_root: Path) -> Path:
    """返回缓存更新信息的本地文件路径。"""
    return Path(data_root) / "cached_update.json"


def load_cached_update_info(data_root: Path) -> UpdateInfo | None:
    """从本地读取缓存的最新版本信息。"""
    path = cached_update_info_path(data_root)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return update_info_from_dict(data)
    except (OSError, json.JSONDecodeError):
        return None


def save_cached_update_info(data_root: Path, info: UpdateInfo) -> None:
    """把最新版本信息持久化到本地。"""
    path = cached_update_info_path(data_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(update_info_to_dict(info), ensure_ascii=False, indent=2)
    path.write_text(payload, encoding="utf-8")
