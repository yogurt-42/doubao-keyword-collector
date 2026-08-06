from __future__ import annotations

from typing import Any


def _text_from_content(content: Any) -> str:
    """Extract plain text from a message content value."""

    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict) and isinstance(item.get("text"), str):
                parts.append(item["text"])
        return "\n".join(part for part in parts if part)
    return str(content) if content is not None else ""


def _collect_text(value: Any, output: list[str]) -> None:
    """Recursively collect text fields from a Doubao SSE event."""

    if isinstance(value, dict):
        for key, item in value.items():
            if key in {"text", "content", "answer", "delta"} and isinstance(item, str):
                if item.strip():
                    output.append(item)
            else:
                _collect_text(item, output)
    elif isinstance(value, list):
        for item in value:
            _collect_text(item, output)


def _merge_text_fragments(fragments: list[str]) -> str:
    """Merge streaming text fragments, removing partial overlaps.

    The naive implementation only handled cases where one fragment fully
    contained another. Doubao's SSE stream sometimes splits at arbitrary
    positions, producing partial overlaps like:

        ["北京装修公司", "装修公司推荐"] -> "北京装修公司推荐"
    """

    result = ""
    max_scan = 256
    for fragment in fragments:
        if not fragment or not fragment.strip():
            continue
        if result.endswith(fragment):
            continue
        overlap = 0
        max_possible = min(len(result), len(fragment), max_scan)
        for length in range(max_possible, 0, -1):
            if result[-length:] == fragment[:length]:
                overlap = length
                break
        result += fragment[overlap:]
    return result.strip()
