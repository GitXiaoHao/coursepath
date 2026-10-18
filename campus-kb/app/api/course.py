"""核算接口：``/audit``（学分缺口）与 ``/prereq``（先修链）。

这两个接口是"Dify 做不到"的部分：结果是确定性计算，可复现、可归因。
工作流通过 **HTTP 请求节点**调用（代码节点 sandbox 无外网，见 docs/00 §B4）。
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Header, Request
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field

from app.api.retrieval import api_error, check_bearer
from core.graph import EvalContext, check_prerequisites
from core.scoring import audit_credits
from core.schema import CompletedCourse, CreditPlan, parse_completed_text

router = APIRouter(tags=["course-audit"])


class Target(BaseModel):
    major: str | None = None
    module: str | None = None


class AuditRequest(BaseModel):
    plan_id: str | None = None
    target: Target | None = None
    completed: list[CompletedCourse] = Field(default_factory=list)
    #: 对话友好的输入形态：用户在对话框直接粘的课程串，如 ``MA101,CS201:4``
    completed_text: str | None = None
    #: ``text`` 时直接返回 Markdown 摘要（Chatflow 的 answer 节点可直接引用
    #: ``{{#node.body#}}``；JSON 响应体在 Dify 里是字符串，取不到子字段）
    format: Literal["json", "text"] = "json"


class PrereqRequest(BaseModel):
    plan_id: str | None = None
    course_code: str | None = None
    course_name: str | None = None
    completed: list[CompletedCourse] = Field(default_factory=list)
    completed_text: str | None = None
    format: Literal["json", "text"] = "json"


def _merge_completed(payload_completed: list[CompletedCourse], completed_text: str | None, plan: CreditPlan) -> list[CompletedCourse]:
    """合并结构化与文本两种输入，并按培养方案补全缺失学分。

    学分缺省补全的依据是规则 R3"以培养方案登记的值为准"：学生只说"我修过 CS201"
    时，不该因为没报学分而被判 0 学分或触发 R3 告警。
    """
    parsed, _warnings = parse_completed_text(completed_text)
    merged: list[CompletedCourse] = []
    seen: set[str] = set()
    course_map = plan.course_map()
    for item in [*payload_completed, *parsed]:
        code = item.code.upper()
        if code in seen:
            continue
        seen.add(code)
        credits = item.credits
        if credits <= 0 and code in course_map:
            credits = course_map[code].credits
        merged.append(item.model_copy(update={"code": code, "credits": credits}))
    return merged


def _get_plan(request: Request, plan_id: str | None):
    plan = getattr(request.app.state, "plan", None)
    if plan is None:
        raise api_error(503, 2001, "服务端未配置培养方案（KB_PLAN 未指向有效文件）")
    if plan_id and plan_id != plan.plan_id:
        raise api_error(404, 1002, f"培养方案不存在: {plan_id}")
    return plan


@router.post("/audit", summary="学分缺口核算")
def audit(payload: AuditRequest, request: Request, authorization: str | None = Header(default=None)):
    settings = request.app.state.settings
    check_bearer(settings, authorization)
    plan = _get_plan(request, payload.plan_id)

    completed = _merge_completed(payload.completed, payload.completed_text, plan)
    result = audit_credits(plan, completed)
    if payload.format == "text":
        return PlainTextResponse(result.summary())
    body = result.to_dict()
    body["target"] = payload.target.model_dump() if payload.target else None
    return body


@router.post("/prereq", summary="先修链检查")
def prereq(payload: PrereqRequest, request: Request, authorization: str | None = Header(default=None)):
    settings = request.app.state.settings
    check_bearer(settings, authorization)
    plan = _get_plan(request, payload.plan_id)

    identifier = payload.course_code or payload.course_name or ""
    if not identifier:
        raise api_error(400, 1001, "必须提供 course_code 或 course_name")

    completed = _merge_completed(payload.completed, payload.completed_text, plan)
    # credits 节点的求值需要各模块已修学分，因此先跑一遍核算
    audit_result = audit_credits(plan, completed)
    context = EvalContext(
        passed=frozenset(item.code for item in completed if item.passed),
        earned_by_module=audit_result.earned_by_module,
    )
    report = check_prerequisites(plan, identifier, context)
    if payload.format == "text":
        return PlainTextResponse(report.summary())
    body = report.to_dict()
    body["plan_id"] = plan.plan_id
    return body
