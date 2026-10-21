"""基于学生档案的确定性修读候选推荐。"""

from __future__ import annotations

from fastapi import APIRouter, Header, Request
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field

from app.api.retrieval import api_error, check_bearer
from core.graph import EvalContext, check_prerequisites
from core.scoring import audit_credits

router = APIRouter(tags=["study-planning"])


class PlanRequest(BaseModel):
    profile_id: str
    limit: int = Field(default=6, ge=1, le=12)
    format: str = "json"


def _candidates(request: Request, profile_id: str, limit: int) -> tuple[list[dict], object]:
    plan = request.app.state.plan
    completed = request.app.state.profile_store.get_courses(profile_id)
    audit = audit_credits(plan, completed)
    passed = {course.code for course in completed if course.passed}
    context = EvalContext(passed=frozenset(passed), earned_by_module=audit.earned_by_module)
    gaps = {row["module"]: row["missing"] for row in audit.modules}
    candidates: list[dict] = []
    for course in plan.courses:
        if course.code in passed:
            continue
        report = check_prerequisites(plan, course.code, context)
        if not report.ok:
            continue
        candidates.append(
            {
                "code": course.code,
                "name": course.name,
                "credits": course.credits,
                "module": course.module,
                "module_gap": gaps.get(course.module, 0.0),
                "terms": course.terms,
                "source": course.source.model_dump() if course.source else None,
            }
        )
    candidates.sort(key=lambda item: (-item["module_gap"], -item["credits"], item["code"]))
    return candidates[:limit], audit


def _summary(candidates: list[dict], audit) -> str:
    lines = ["### 当前可优先考虑的课程"]
    if not candidates:
        lines.append("当前培养方案中没有可直接推荐的未修课程；可能需要先补齐上游先修课，或人工核对课程表。")
    else:
        lines.append("以下课程当前满足已登记的先修条件，并按所属模块缺口与课程学分排序：")
        for row in candidates:
            gap = f"该模块还缺 {row['module_gap']:g} 学分" if row["module_gap"] > 0 else "所属模块当前无学分缺口"
            lines.append(f"- {row['name']}（{row['code']}，{row['credits']:g} 学分，{row['module']}；{gap}）")
    lines.append(f"\n当前还差 {audit.remaining_credits:g} 学分。此结果不含实际上课学期、容量、时间冲突和教务限制，不能替代正式选课结果。")
    return "\n".join(lines)


@router.post("/study-plan", summary="根据档案生成可修课程候选")
def study_plan(payload: PlanRequest, request: Request, authorization: str | None = Header(default=None)):
    check_bearer(request.app.state.settings, authorization)
    if not request.app.state.profile_store.valid_profile_id(payload.profile_id):
        raise api_error(400, 1001, "profile_id 格式无效")
    plan = request.app.state.plan
    if not plan.calculation_available:
        raise api_error(503, 2001, plan.calculation_notice or "当前培养方案不能用于修读规划")
    candidates, audit = _candidates(request, payload.profile_id, payload.limit)
    if payload.format == "text":
        return PlainTextResponse(_summary(candidates, audit))
    return {"candidates": candidates, "remaining_credits": audit.remaining_credits}
