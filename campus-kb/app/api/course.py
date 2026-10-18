"""核算接口：``/audit``（学分缺口）与 ``/prereq``（先修链）。

这两个接口是"Dify 做不到"的部分：结果是确定性计算，可复现、可归因。
工作流通过 **HTTP 请求节点**调用（代码节点 sandbox 无外网，见 docs/00 §B4）。
"""

from __future__ import annotations

from fastapi import APIRouter, Header, Request
from pydantic import BaseModel, Field

from app.api.retrieval import api_error, check_bearer
from core.graph import EvalContext, check_prerequisites
from core.scoring import audit_credits
from core.schema import CompletedCourse

router = APIRouter(tags=["course-audit"])


class Target(BaseModel):
    major: str | None = None
    module: str | None = None


class AuditRequest(BaseModel):
    plan_id: str | None = None
    target: Target | None = None
    completed: list[CompletedCourse] = Field(default_factory=list)


class PrereqRequest(BaseModel):
    plan_id: str | None = None
    course_code: str | None = None
    course_name: str | None = None
    completed: list[CompletedCourse] = Field(default_factory=list)


def _get_plan(request: Request, plan_id: str | None):
    plan = getattr(request.app.state, "plan", None)
    if plan is None:
        raise api_error(503, 2001, "服务端未配置培养方案（KB_PLAN 未指向有效文件）")
    if plan_id and plan_id != plan.plan_id:
        raise api_error(404, 1002, f"培养方案不存在: {plan_id}")
    return plan


@router.post("/audit", summary="学分缺口核算")
def audit(payload: AuditRequest, request: Request, authorization: str | None = Header(default=None)) -> dict:
    settings = request.app.state.settings
    check_bearer(settings, authorization)
    plan = _get_plan(request, payload.plan_id)

    result = audit_credits(plan, payload.completed)
    body = result.to_dict()
    body["target"] = payload.target.model_dump() if payload.target else None
    return body


@router.post("/prereq", summary="先修链检查")
def prereq(payload: PrereqRequest, request: Request, authorization: str | None = Header(default=None)) -> dict:
    settings = request.app.state.settings
    check_bearer(settings, authorization)
    plan = _get_plan(request, payload.plan_id)

    identifier = payload.course_code or payload.course_name or ""
    if not identifier:
        raise api_error(400, 1001, "必须提供 course_code 或 course_name")

    # credits 节点的求值需要各模块已修学分，因此先跑一遍核算
    audit_result = audit_credits(plan, payload.completed)
    context = EvalContext(
        passed=frozenset(item.code for item in payload.completed if item.passed),
        earned_by_module=audit_result.earned_by_module,
    )
    report = check_prerequisites(plan, identifier, context)
    body = report.to_dict()
    body["plan_id"] = plan.plan_id
    return body
