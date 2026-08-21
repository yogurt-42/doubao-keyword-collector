from __future__ import annotations

import re
from typing import Any
from urllib.parse import parse_qs, unquote, urlsplit

from .platforms import get_platform
from .research_platforms import category_for_url, platform_for_url

URL_RE = re.compile(r"https?://[^\s<>'\"\]\)）】}，。；、]+", re.IGNORECASE)
MARKDOWN_LINK_RE = re.compile(
    r"\[([^\]]+)]\((https?://[^)\s]+)\)",
    re.IGNORECASE,
)
URL_KEYS = {
    "url",
    "href",
    "link",
    "source_url",
    "web_url",
    "reference_url",
    "site_url",
    "page_url",
}
IGNORED_HOSTS = {
    "www.doubao.com",
    "doubao.com",
    "lf-flow-web-cdn.doubao.com",
}
#: Re-exported for backward compatibility. Prefer research_platforms.py.
PLATFORM_NAMES = {}


def _ignored_hosts_for_platform(platform_key: str | None) -> set[str]:
    platform = get_platform(platform_key)
    return set(platform.ignored_hosts) | IGNORED_HOSTS


def _clean_url(value: str) -> str:
    return value.strip().rstrip(".,;:!?，。；：！？、）)]}】>\"'")


def _unwrap_source_url(value: str, platform_key: str | None = None) -> str:
    url = _clean_url(value)
    parsed = urlsplit(url)
    ignored_hosts = _ignored_hosts_for_platform(platform_key)
    if (parsed.hostname or "").casefold() not in ignored_hosts:
        return url
    query = parse_qs(parsed.query)
    for key in ("url", "target", "target_url", "redirect_url"):
        for candidate in query.get(key, []):
            decoded = unquote(candidate)
            if decoded.startswith(("http://", "https://")):
                return decoded
    return url


def platform_for_reference(url: str, provided: str = "") -> str:
    """Use the link owner as platform; page labels are often article titles."""

    host = (urlsplit(url).hostname or "").casefold().removeprefix("www.")
    if host:
        return platform_for_url(url)
    return provided.strip() or "未知平台"


def _collect_event_links(value: Any, output: list[tuple[str, str]]) -> None:
    if isinstance(value, dict):
        title = ""
        for title_key in ("title", "name", "source_name", "site_name"):
            candidate = value.get(title_key)
            if isinstance(candidate, str) and candidate.strip():
                title = candidate.strip()
                break
        for key, item in value.items():
            if isinstance(item, str):
                if key.casefold() in URL_KEYS and item.startswith(("http://", "https://")):
                    output.append((item, title))
                else:
                    for match in URL_RE.findall(item):
                        output.append((match, title))
            else:
                _collect_event_links(item, output)
    elif isinstance(value, list):
        for item in value:
            _collect_event_links(item, output)
    elif isinstance(value, str):
        for match in URL_RE.findall(value):
            output.append((match, ""))


def extract_research_links(
    answer_text: str,
    events: list[Any] | None = None,
    platform_key: str | None = None,
) -> list[dict[str, str]]:
    candidates: list[tuple[str, str]] = []
    for title, url in MARKDOWN_LINK_RE.findall(answer_text or ""):
        candidates.append((url, title.strip()))
    for url in URL_RE.findall(answer_text or ""):
        candidates.append((url, ""))
    if events:
        _collect_event_links(events, candidates)

    ignored_hosts = _ignored_hosts_for_platform(platform_key)
    found: list[dict[str, str]] = []
    seen: set[str] = set()
    for raw_url, title in candidates:
        url = _clean_url(raw_url)
        parsed = urlsplit(url)
        host = (parsed.hostname or "").casefold()
        if parsed.scheme not in {"http", "https"} or not host or host in ignored_hosts:
            continue
        normalized = parsed._replace(fragment="").geturl()
        if normalized in seen:
            continue
        seen.add(normalized)
        found.append(
            {
                "link": normalized,
                "platform": platform_for_url(normalized),
                "platform_type": category_for_url(normalized),
                "title": title,
            }
        )
    return found


def normalize_thinking_references(
    references: list[dict[str, str]],
    platform_key: str | None = None,
) -> list[dict[str, str]]:
    """Normalize only links exposed by thinking/reference controls."""
    found: list[dict[str, str]] = []
    seen: set[str] = set()
    ignored_hosts = _ignored_hosts_for_platform(platform_key)
    for item in references:
        url = _unwrap_source_url(item.get("link", ""), platform_key)
        parsed = urlsplit(url)
        host = (parsed.hostname or "").casefold()
        if parsed.scheme not in {"http", "https"} or not host or host in ignored_hosts:
            continue
        normalized = parsed._replace(fragment="").geturl()
        if normalized in seen:
            continue
        seen.add(normalized)
        platform = platform_for_reference(
            normalized,
            str(item.get("platform", "")),
        )
        found.append(
            {
                "link": normalized,
                "platform": platform,
                "platform_type": category_for_url(normalized),
                "title": item.get("title", "").strip(),
            }
        )
    return found
