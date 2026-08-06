"""CLI helper to add a new platform entry to research_platforms.py.

Usage:
    python scripts/add_platform_entry.py \
        --domain jiaju.sina.cn \
        --name 新浪家居 \
        --category "地方/行业新闻媒体"

The script preserves the ordering rule: more specific domains must appear
before generic ones (e.g. ``jiaju.sina.cn`` before ``sina.cn``).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SRC_ROOT = PROJECT_ROOT / "src"
sys.path.insert(0, str(SRC_ROOT))

from doubao2api.platform_editor import (  # noqa: E402
    PLATFORM_CATEGORIES,
    add_entry,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="往 research_platforms.py 添加一条 URL → 平台名 → 类型规则"
    )
    parser.add_argument("--domain", required=True, help="域名或完整 URL")
    parser.add_argument("--name", required=True, help="中文平台名")
    parser.add_argument(
        "--category",
        required=True,
        help=f"类型，可选：{', '.join(PLATFORM_CATEGORIES)}",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        result = add_entry(args.domain, args.name, args.category)
    except ValueError as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 1

    entry = result["entry"]
    print(f"已添加：{entry['domain']} -> {entry['name']}（{entry['category']}）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
