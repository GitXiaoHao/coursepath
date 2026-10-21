"""修读规划工具：只根据已登记的培养方案和已修课程给出候选课。

它刻意不把“可修”说成“可以成功选上”：上课学期、人数容量、时间冲突与教务系统
限制不在培养方案数据内。这样插件下载到任何 Dify 环境后仍能独立、可复现地运行。
"""

from __future__ import annotations

from collections.abc import Generator
from typing import Any

from core.graph import EvalContext, check_prerequisites
from core.scoring import audit_credits
from dify_plugin import Tool
from dify_plugin.entities.tool import ToolInvokeMessage

from ._shared import load_plan, parse_completed


def _limit(value: Any) -> int:
    try:
        parsed = int(value if value is not None else 6)
    except (TypeError, ValueError):
        return 6
    return max(1, min(parsed, 12))


def build_suggestions(plan, completed, limit: int) -> tuple[list[dict[str, Any]], Any]:
    """返回当前满足直接先修的未修课程及核算结果，供文本/JSON 输出共用。"""
    audit = audit_credits(plan, completed)
    passed = {item.code for item in completed if item.passed}
    context = EvalContext(passed=frozenset(passed), earned_by_module=audit.earned_by_module)
    gaps = {row["module"]: row["missing"] for row in audit.modules}
    candidates: list[dict[str, Any]] = []
    for course in plan.courses:
        if course.code in passed:
            continue
        prereq = check_prerequisites(plan, course.code, context)
        if not prereq.ok:
            continue
        candidates.append(
            {
                "code": course.code,
                "name": course.name,
                "credits": course.credits,
                "module": course.module,
                "module_gap": gaps.get(course.module, 0.0),
                "terms": course.terms,
            }
        )
    candidates.sort(key=lambda row: (-row["module_gap"], -row["credits"], row["code"]))
    return candidates[:limit], audit


def summary(candidates: list[dict[str, Any]], audit) -> str:
    lines = ["### 修读规划建议"]
    if candidates:
        lines.append("以下课程目前满足培养方案中登记的先修条件，按模块缺口和课程学分排序：")
        for row in candidates:
            gap = f"该模块还缺 {row['module_gap']:g} 学分" if row["module_gap"] else "该模块当前无学分缺口"
            lines.append(f"- {row['name']}（{row['code']}，{row['credits']:g} 学分，{row['module']}；{gap}）")
    else:
        lines.append("没有找到可直接建议的未修课程；请先补齐上游先修课，或人工核对培养方案。")
    lines.append(f"\n当前按模块核算还差 {audit.remaining_credits:g} 学分。")
    lines.append("- 注意：本建议不含实际上课学期、课程容量、时间冲突和教务系统限制，不能替代正式选课结果。")
    return "\n".join(lines)


class CourseStudyPlanTool(Tool):
    def _invoke(self, tool_parameters: dict[str, Any]) -> Generator[ToolInvokeMessage]:
        try:
            plan = load_plan(tool_parameters.get("plan_json"))
            completed = parse_completed(tool_parameters.get("completed"), plan)
        except ValueError as exc:
            yield self.create_text_message(f"输入有误：{exc}")
            return

        if not plan.calculation_available:
            yield self.create_text_message(plan.calculation_notice or "当前培养方案不能用于修读规划。")
            return
        candidates, audit = build_suggestions(plan, completed, _limit(tool_parameters.get("limit")))
        result = {
            "candidates": candidates,
            "remaining_credits": audit.remaining_credits,
            "summary": summary(candidates, audit),
        }
        yield self.create_text_message(result["summary"])
        yield self.create_json_message(result)
