"""先修链判定工具：判断某门课能否选，并给出直接/间接缺失先修。

表达式支持 ``and`` / ``or`` / ``credits``（某模块已修学分不少于 N）三类节点，
``or`` 不满足时返回"阻塞最少"的分支，方便学生选择补齐路径。
"""

from __future__ import annotations

from collections.abc import Generator
from typing import Any

from core.graph import EvalContext, check_prerequisites
from core.scoring import audit_credits
from dify_plugin import Tool
from dify_plugin.entities.tool import ToolInvokeMessage

from ._shared import load_plan, parse_completed


class CoursePrereqTool(Tool):
    def _invoke(self, tool_parameters: dict[str, Any]) -> Generator[ToolInvokeMessage]:
        identifier = (tool_parameters.get("course") or "").strip()
        if not identifier:
            yield self.create_text_message("请提供课程名称或课程代码，例如「编译原理」或 CS305。")
            return
        try:
            plan = load_plan(tool_parameters.get("plan_json"))
            completed = parse_completed(tool_parameters.get("completed"), plan)
        except ValueError as exc:
            yield self.create_text_message(f"输入有误：{exc}")
            return

        # credits 类先修需要"各模块已修学分"，因此先跑一遍核算
        audit = audit_credits(plan, completed)
        context = EvalContext(
            passed=frozenset(item.code for item in completed if item.passed),
            earned_by_module=audit.earned_by_module,
        )
        report = check_prerequisites(plan, identifier, context)
        yield self.create_text_message(report.summary())
        yield self.create_json_message(report.to_dict())
