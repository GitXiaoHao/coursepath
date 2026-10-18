"""插件内共用的装载/解析助手。

设计取舍：插件**不依赖任何外部服务**（不调用 campus-kb），核算全在本进程内完成，
所以这里只做三件事——把 JSON 变成 ``CreditPlan``、把逗号串变成已修课程、补齐学分。
"""

from __future__ import annotations

import json
from pathlib import Path

from core.schema import CompletedCourse, CreditPlan, parse_completed_text

#: 未提供 plan_json 时使用的内置合成样例（不含任何真实院校信息）
DEMO_PLAN = Path(__file__).resolve().parent.parent / "_assets" / "plan-demo.json"


def load_plan(plan_json: str | None) -> CreditPlan:
    raw = (plan_json or "").strip()
    if not raw:
        raw = DEMO_PLAN.read_text(encoding="utf-8")
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"plan_json 不是合法 JSON：{exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError("plan_json 必须是培养方案对象（含 plan_id/major/total_credits/modules/courses）")
    try:
        return CreditPlan(**payload)
    except Exception as exc:  # pydantic 校验失败统一转成可读错误
        raise ValueError(f"培养方案字段不合法：{exc}") from exc


def parse_completed(completed: str | None, plan: CreditPlan) -> list[CompletedCourse]:
    courses, _warnings = parse_completed_text(completed)
    by_code = plan.course_map()
    merged: dict[str, CompletedCourse] = {}
    for item in courses:
        registered = by_code.get(item.code)
        if registered is not None and item.credits <= 0:
            item = CompletedCourse(
                code=item.code,
                credits=registered.credits,
                grade=item.grade,
                passed=item.passed,
            )
        merged.setdefault(item.code, item)
    return list(merged.values())
