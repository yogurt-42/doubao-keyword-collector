from __future__ import annotations

import re
import sys
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

    def _match_assets(
        self, release_assets: list[dict[str, object]], version: str
    ) -> dict[str, AssetInfo]:
        """从 release assets 中匹配单文件 exe 和便携版 zip。"""
        escaped = re.escape(version)
        patterns = {
            "single": re.compile(rf"AI信源采集工具-v{escaped}\.exe"),
            "portable": re.compile(rf"AI信源采集工具-v{escaped}-便携版\.zip"),
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
                name=f"AI信源采集工具-v{version}.exe",
                url=f"{base}/AI信源采集工具-v{version}.exe",
                size=0,
            ),
            "portable": AssetInfo(
                name=f"AI信源采集工具-v{version}-便携版.zip",
                url=f"{base}/AI信源采集工具-v{version}-便携版.zip",
                size=0,
            ),
        }

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
