"""培养方案课程信息查询工具。"""

from __future__ import annotations

from collections.abc import Generator
from typing import Any

from dify_plugin import Tool
from dify_plugin.entities.tool import ToolInvokeMessage

from ._shared import load_plan


def _summary(course) -> str:
    lines = [
        f"### {course.name}（{course.code}）",
        f"- 所属模块：{course.module or '培养方案未标注'}",
        f"- 学分：{course.credits:g} 学分",
    ]
    if course.terms:
        lines.append(f"- 建议开课学期：{'、'.join(course.terms)}")
    if course.source:
        location: list[str] = []
        if course.source.page:
            location.append(f"第 {course.source.page} 页")
        if course.source.table_row:
            location.append(f"第 {course.source.table_row} 行")
        lines.append(
            f"\n依据：培养方案课程目录{'（' + '，'.join(location) + '）' if location else ''}："
            f"{course.source.quote or '课程目录'}"
        )
    return "\n".join(lines)


class CourseInfoTool(Tool):
    def _invoke(self, tool_parameters: dict[str, Any]) -> Generator[ToolInvokeMessage]:
        try:
            plan = load_plan(tool_parameters.get("plan_json"))
        except ValueError as exc:
            yield self.create_text_message(f"输入有误：{exc}")
            return

        query = str(tool_parameters.get("course") or "").strip()
        course = plan.resolve_in_text(query)
        if course is None:
            yield self.create_text_message("未识别到唯一课程。请提供课程代码（如 CS101）或完整课程名称。")
            return
        result = {
            "code": course.code,
            "name": course.name,
            "module": course.module,
            "credits": course.credits,
            "terms": course.terms,
            "source": course.source.model_dump() if course.source else None,
        }
        yield self.create_text_message(_summary(course))
        yield self.create_json_message(result)
