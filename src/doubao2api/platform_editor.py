"""Helpers for editing the platform mapping library at runtime.

The mapping library in ``research_platforms.py`` is the single source of truth.
This module lets the UI (and CLI scripts) add entries while keeping the
ordering rule: more specific domains must appear before generic ones.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Any

if __name__ == "__main__":
    # Allow running the file directly for quick tests without import surprises.
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from doubao2api.research_platforms import (  # noqa: E402
    _DOMAIN_ENTRIES,
    _NAME_TO_CATEGORY,
    PLATFORM_CATEGORIES,
    PLATFORM_ENTRIES,
    _build_domain_suffix_map,
)

TARGET_FILE = Path(__file__).resolve().parent / "research_platforms.py"

ENTRY_LINE_RE = re.compile(r'^\s*\{"domain":\s*"([^"]+)"')


def normalize_domain(value: str) -> str:
    value = value.strip().lower()
    if value.startswith(("http://", "https://")):
        from urllib.parse import urlsplit

        value = urlsplit(value).hostname or value
    return value.removeprefix("www.")


def validate_category(value: str) -> str:
    category = value.strip()
    if category not in PLATFORM_CATEGORIES:
        raise ValueError(
            f"类型 {category!r} 不在可选列表中。可选：{', '.join(PLATFORM_CATEGORIES)}"
        )
    return category


def find_insert_position(new_domain: str, existing: list[str]) -> int:
    """Return the index at which ``new_domain`` should be inserted.

    More specific domains must appear before any generic domain they are a
    suffix of, and after any more specific domain that is a suffix of them.
    """

    before = len(existing)
    after = -1
    for index, domain in enumerate(existing):
        if new_domain == domain:
            raise ValueError(f"域名 {new_domain!r} 已存在")
        if new_domain.endswith(f".{domain}"):
            before = min(before, index)
        if domain.endswith(f".{new_domain}"):
            after = max(after, index)

    if after >= 0 and before < len(existing):
        return max(after + 1, before)
    if after >= 0:
        return after + 1
    if before < len(existing):
        return before
    return len(existing)


def _format_entry(entry: dict[str, str]) -> str:
    return (
        f'    {{"domain": "{entry["domain"]}", '
        f'"name": "{entry["name"]}", '
        f'"category": "{entry["category"]}"}},\n'
    )


def _entry_line_indices(source: str) -> list[tuple[int, str]]:
    return [
        (line_index, match.group(1))
        for line_index, line in enumerate(source.splitlines())
        if (match := ENTRY_LINE_RE.match(line))
    ]


def _insert_entries_into_source(
    source: str,
    insertions: list[tuple[int, dict[str, str]]],
) -> str:
    """Insert new entry lines into the existing PLATFORM_ENTRIES block.

    Preserves comments and formatting. ``insertions`` is a list of
    ``(position, entry)`` tuples where ``position`` is relative to the current
    entries in the source file.
    """

    lines = source.splitlines(keepends=True)
    entry_matches = _entry_line_indices(source)

    if not entry_matches:
        raise ValueError("未在文件中定位到 PLATFORM_ENTRIES 列表")

    # Sort by position descending. Callers that need a specific order among
    # equal positions should pre-sort; this sort is stable and will keep it.
    sorted_insertions = sorted(insertions, key=lambda item: item[0], reverse=True)

    for position, entry in sorted_insertions:
        new_line = _format_entry(entry)
        if position < len(entry_matches):
            insert_line = entry_matches[position][0]
        else:
            last_entry_line = entry_matches[-1][0]
            insert_line = None
            for line_index in range(last_entry_line + 1, len(lines)):
                if re.match(r"^\s*\]\s*,?\s*$", lines[line_index]):
                    insert_line = line_index
                    break
            if insert_line is None:
                raise ValueError("未找到 PLATFORM_ENTRIES 列表的结束位置")
        lines.insert(insert_line, new_line)

    return "".join(lines)


def _refresh_derived_mappings() -> None:
    _DOMAIN_ENTRIES[:] = [
        (entry["domain"], entry["name"], entry["category"]) for entry in PLATFORM_ENTRIES
    ]
    _build_domain_suffix_map()
    _NAME_TO_CATEGORY.clear()
    _NAME_TO_CATEGORY.update({entry["name"]: entry["category"] for entry in PLATFORM_ENTRIES})


def add_entry(domain: str, name: str, category: str) -> dict[str, Any]:
    """Add a single entry to the in-memory library and persist it."""

    domain = normalize_domain(domain)
    name = name.strip()
    category = validate_category(category)

    if not domain:
        raise ValueError("域名不能为空")
    if not name:
        raise ValueError("平台名不能为空")

    existing = [entry[0] for entry in _DOMAIN_ENTRIES]
    position = find_insert_position(domain, existing)
    entry = {"domain": domain, "name": name, "category": category}
    PLATFORM_ENTRIES.insert(position, entry)
    _refresh_derived_mappings()

    source = TARGET_FILE.read_text(encoding="utf-8")
    new_source = _insert_entries_into_source(source, [(position, entry)])
    TARGET_FILE.write_text(new_source, encoding="utf-8")
    return {"entry": entry, "position": position}


def add_entries(rows: list[dict[str, str]]) -> dict[str, int]:
    """Add multiple entries from an import. Returns counts of added / ignored."""

    added = 0
    ignored = 0
    seen: set[str] = {entry[0] for entry in _DOMAIN_ENTRIES}
    domain_list: list[str] = [entry[0] for entry in _DOMAIN_ENTRIES]
    insertions: list[tuple[int, dict[str, str], int]] = []

    for input_index, raw in enumerate(rows):
        url = str(raw.get("url") or raw.get("域名") or "").strip()
        name = str(raw.get("平台名") or raw.get("平台名称") or "").strip()
        category = str(raw.get("平台类型") or raw.get("类型") or "").strip()

        if not url or not name or not category:
            ignored += 1
            continue

        try:
            domain = normalize_domain(url)
            category = validate_category(category)
        except ValueError:
            ignored += 1
            continue

        if domain in seen:
            ignored += 1
            continue

        try:
            position = find_insert_position(domain, domain_list)
        except ValueError:
            ignored += 1
            continue

        entry = {"domain": domain, "name": name, "category": category}
        PLATFORM_ENTRIES.insert(position, entry)
        domain_list.insert(position, domain)
        seen.add(domain)
        insertions.append((position, entry, input_index))
        added += 1

    if added:
        _refresh_derived_mappings()
        source = TARGET_FILE.read_text(encoding="utf-8")
        # Normalize sort so that equal positions preserve input order.
        normalized = sorted(
            insertions,
            key=lambda item: (item[0], item[2]),
            reverse=True,
        )
        new_source = _insert_entries_into_source(
            source, [(pos, entry) for pos, entry, _ in normalized]
        )
        TARGET_FILE.write_text(new_source, encoding="utf-8")

    return {"added": added, "ignored": ignored}


def all_entries() -> list[dict[str, str]]:
    return [dict(entry) for entry in PLATFORM_ENTRIES]
