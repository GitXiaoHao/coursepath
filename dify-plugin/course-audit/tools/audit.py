"""学分缺口核算工具：输入培养方案 + 已修课程，输出分模块缺口与计算过程。

为什么用代码而不是让模型算：学分核算是确定性规则（只计及格课、超额不冲抵、
剩余=各模块缺口之和），模型算这类问题会错且无法逐项核对。
"""

from __future__ import annotations

from collections.abc import Generator
from typing import Any

from core.scoring import audit_credits
from dify_plugin import Tool
from dify_plugin.entities.tool import ToolInvokeMessage

from ._shared import load_plan, parse_completed


class CourseAuditTool(Tool):
    def _invoke(self, tool_parameters: dict[str, Any]) -> Generator[ToolInvokeMessage]:
        try:
            plan = load_plan(tool_parameters.get("plan_json"))
            completed = parse_completed(tool_parameters.get("completed"), plan)
        except ValueError as exc:
            yield self.create_text_message(f"输入有误：{exc}")
            return

        result = audit_credits(plan, completed)
        yield self.create_text_message(result.summary())
        yield self.create_json_message(result.to_dict())
