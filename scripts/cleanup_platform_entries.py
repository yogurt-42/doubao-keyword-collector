"""One-time cleanup: rewrite PLATFORM_ENTRIES as a plain list without comments.

Run this after earlier imports left the file with many repeated category
comments. Future imports via platform_editor.py will preserve the plain format.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SRC_ROOT = PROJECT_ROOT / "src"
TARGET_FILE = SRC_ROOT / "doubao2api" / "research_platforms.py"

sys.path.insert(0, str(SRC_ROOT))
from doubao2api.research_platforms import PLATFORM_ENTRIES  # noqa: E402


def main() -> int:
    source = TARGET_FILE.read_text(encoding="utf-8")
    lines = source.splitlines(keepends=True)

    start = None
    end = None
    for index, line in enumerate(lines):
        if start is None and re.match(
            r"^PLATFORM_ENTRIES\s*:\s*list\[dict\[str,\s*str\]\]\s*=\s*\[\s*$",
            line,
        ):
            start = index
        elif start is not None and re.match(r"^\s*\]\s*,?\s*$", line):
            end = index
            break

    if start is None or end is None:
        print("错误：未找到 PLATFORM_ENTRIES 列表", file=sys.stderr)
        return 1

    entry_lines = [
        f"    {json.dumps(entry, ensure_ascii=False)},\n" for entry in PLATFORM_ENTRIES
    ]
    new_block = ["PLATFORM_ENTRIES: list[dict[str, str]] = [\n", *entry_lines, "]\n"]
    new_lines = lines[:start] + new_block + lines[end + 1 :]
    TARGET_FILE.write_text("".join(new_lines), encoding="utf-8")
    print(f"已清理：{TARGET_FILE}")
    print(f"共 {len(PLATFORM_ENTRIES)} 条规则，已重写为无注释的平铺列表")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
