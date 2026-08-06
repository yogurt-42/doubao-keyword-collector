from __future__ import annotations

import io
from typing import Any

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill

from .research_platforms import category_for_url, platform_category


def _platform_type_for_result(item: dict[str, Any]) -> str:
    platform_type = item.get("platform_type", "")
    if platform_type:
        return platform_type
    # 如果记录插入时平台类型为空，按当前最新的平台规则再查一次
    return category_for_url(item.get("link", "")) or platform_category(item.get("platform", ""))


def build_results_workbook(rows: list[dict[str, Any]]) -> bytes:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "思考过程资料链接"
    sheet.append(
        ["任务", "日期", "提问关键词", "资料名称", "检索资料链接", "检索资料平台", "平台类型"]
    )
    for item in rows:
        sheet.append(
            [
                item.get("job_name", ""),
                item["collected_date"],
                item["keyword"],
                item.get("title", ""),
                item["link"],
                item["platform"],
                _platform_type_for_result(item),
            ]
        )

    header_fill = PatternFill("solid", fgColor="183153")
    for cell in sheet[1]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = header_fill
        cell.alignment = Alignment(horizontal="center")
    sheet.freeze_panes = "A2"
    sheet.auto_filter.ref = sheet.dimensions
    sheet.column_dimensions["A"].width = 28
    sheet.column_dimensions["B"].width = 14
    sheet.column_dimensions["C"].width = 32
    sheet.column_dimensions["D"].width = 52
    sheet.column_dimensions["E"].width = 80
    sheet.column_dimensions["F"].width = 22
    sheet.column_dimensions["G"].width = 20
    for row in sheet.iter_rows(min_row=2):
        row[4].hyperlink = row[4].value
        row[4].style = "Hyperlink"
        for cell in row:
            cell.alignment = Alignment(vertical="top", wrap_text=True)

    output = io.BytesIO()
    workbook.save(output)
    workbook.close()
    return output.getvalue()
