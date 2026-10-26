"""核算接口：``/audit``（学分缺口）与 ``/prereq``（先修链）。

这两个接口是"Dify 做不到"的部分：结果是确定性计算，可复现、可归因。
工作流通过 **HTTP 请求节点**调用（代码节点 sandbox 无外网，见 docs/00 §B4）。
"""

from __future__ import annotations

import re
from typing import Literal

from fastapi import APIRouter, Header, Request
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field

from app.api.retrieval import api_error, check_bearer
from core.graph import EvalContext, check_prerequisites
from core.scoring import audit_credits
from core.schema import CompletedCourse, CreditPlan, parse_completed_text

router = APIRouter(tags=["course-audit"])


# 仅在用户明确用「已修／修过／已通过」这类措辞陈述课程时才兜底识别，
# 避免将问题中被查询的课程代码误当成已完成课程。
_COMPLETED_CLAUSE_RE = re.compile(
    r"(?:已修(?:完)?|修过|已通过|通过了|完成(?:了)?)([^。！？!?；;]*)",
    re.IGNORECASE,
)
_COURSE_CODE_RE = re.compile(r"(?<![A-Za-z0-9])([A-Za-z]{2,6}\d{2,4})(?![A-Za-z0-9])")


class Target(BaseModel):
    major: str | None = None
    module: str | None = None


class AuditRequest(BaseModel):
    plan_id: str | None = None
    target: Target | None = None
    completed: list[CompletedCourse] = Field(default_factory=list)
    #: 对话友好的输入形态：用户在对话框直接粘的课程串，如 ``MA101,CS201:4``
    completed_text: str | None = None
    profile_id: str | None = None
    #: ``text`` 时直接返回 Markdown 摘要（Chatflow 的 answer 节点可直接引用
    #: ``{{#node.body#}}``；JSON 响应体在 Dify 里是字符串，取不到子字段）
    format: Literal["json", "text", "brief"] = "json"


class PrereqRequest(BaseModel):
    plan_id: str | None = None
    course_code: str | None = None
    course_name: str | None = None
    completed: list[CompletedCourse] = Field(default_factory=list)
    completed_text: str | None = None
    profile_id: str | None = None
    format: Literal["json", "text", "brief"] = "json"


class CourseInfoRequest(BaseModel):
    """按课程代码或名称查询培养方案中的确定性课程信息。"""

    query: str
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


def _infer_completed_text_from_question(question: str | None) -> str | None:
    """从自然语言问题中提取明确声明为已修的课程代码。

    这是 Dify HTTP 节点字段被旧草稿覆盖或未映射时的兼容措施；它不是跨轮次
    的学生档案，也不会根据任意出现在问题里的课程代码臆测已修状态。
    """
    codes: list[str] = []
    for clause in _COMPLETED_CLAUSE_RE.findall(question or ""):
        for code in _COURSE_CODE_RE.findall(clause):
            normalized = code.upper()
            if normalized not in codes:
                codes.append(normalized)
    return ",".join(codes) or None


def _resolve_completed(request: Request, profile_id: str | None, payload_completed: list[CompletedCourse], completed_text: str | None) -> list[CompletedCourse]:
    """合并档案与本次输入；仅将本次明确给出的课程写入档案。"""
    current = _merge_completed(payload_completed, completed_text, request.app.state.plan)
    return request.app.state.profile_store.merge_courses(profile_id, current)


def _get_plan(request: Request, plan_id: str | None):
    plan = getattr(request.app.state, "plan", None)
    if plan is None:
        raise api_error(503, 2001, "服务端未配置培养方案（KB_PLAN 未指向有效文件）")
    if plan_id and plan_id != plan.plan_id:
        raise api_error(404, 1002, f"培养方案不存在: {plan_id}")
    return plan


def _course_info_summary(course) -> str:
    """把课程目录中的事实组织成短答，避免把问题误送入通用检索。"""
    lines = [
        f"### {course.name}（{course.code}）",
        f"- 所属模块：{course.module or '培养方案未标注'}",
        f"- 学分：{course.credits:g} 学分",
    ]
    if course.terms:
        lines.append(f"- 建议开课学期：{'、'.join(course.terms)}")
    if course.source:
        location = []
        if course.source.page:
            location.append(f"第 {course.source.page} 页")
        if course.source.table_row:
            location.append(f"第 {course.source.table_row} 行")
        origin = course.source.quote or "课程目录"
        lines.append(f"\n依据：培养方案课程目录{'（' + '，'.join(location) + '）' if location else ''}：{origin}")
    return "\n".join(lines)


@router.post("/audit", summary="学分缺口核算")
def audit(payload: AuditRequest, request: Request, authorization: str | None = Header(default=None)):
    settings = request.app.state.settings
    check_bearer(settings, authorization)
    plan = _get_plan(request, payload.plan_id)

    if not plan.calculation_available:
        message = plan.calculation_notice or "当前资料库缺少可核验的培养方案课程表，不能进行学分核算。"
        if payload.format == "text":
            return PlainTextResponse(f"### 学分核算暂不可用\n{message}")
        return {"status": "unavailable", "plan_id": plan.plan_id, "message": message}

    completed = _resolve_completed(request, payload.profile_id, payload.completed, payload.completed_text)
    result = audit_credits(plan, completed)
    if payload.format == "text":
        return PlainTextResponse(result.summary())
    body = result.to_dict()
    body["target"] = payload.target.model_dump() if payload.target else None
    return body


@router.post("/course-info", summary="查询课程所属模块、学分与建议开课学期")
def course_info(payload: CourseInfoRequest, request: Request, authorization: str | None = Header(default=None)):
    check_bearer(request.app.state.settings, authorization)
    plan = _get_plan(request, None)
    course = plan.resolve_in_text(payload.query)
    if course is None:
        message = "未识别到唯一课程。请提供课程代码（如 CS101）或完整课程名称。"
        if payload.format == "text":
            return PlainTextResponse(f"### 课程信息\n{message}")
        return {"found": False, "message": message}
    result = {
        "found": True,
        "code": course.code,
        "name": course.name,
        "module": course.module,
        "credits": course.credits,
        "terms": course.terms,
        "source": course.source.model_dump() if course.source else None,
    }
    if payload.format == "text":
        return PlainTextResponse(_course_info_summary(course))
    return result


@router.post("/prereq", summary="先修链检查")
def prereq(payload: PrereqRequest, request: Request, authorization: str | None = Header(default=None)):
    settings = request.app.state.settings
    check_bearer(settings, authorization)
    plan = _get_plan(request, payload.plan_id)

    if not plan.calculation_available:
        message = plan.calculation_notice or "当前资料库缺少可核验的培养方案课程表，不能进行先修判定。"
        if payload.format == "text":
            return PlainTextResponse(f"### 先修判定暂不可用\n{message}")
        return {"status": "unavailable", "plan_id": plan.plan_id, "message": message}

    identifier = payload.course_code or payload.course_name or ""
    if not identifier:
        raise api_error(400, 1001, "必须提供 course_code 或 course_name")

    completed_text = payload.completed_text
    if not payload.completed and not completed_text:
        completed_text = _infer_completed_text_from_question(payload.course_name)
    completed = _resolve_completed(request, payload.profile_id, payload.completed, completed_text)
    # credits 节点的求值需要各模块已修学分，因此先跑一遍核算
    audit_result = audit_credits(plan, completed)
    context = EvalContext(
        passed=frozenset(item.code for item in completed if item.passed),
        earned_by_module=audit_result.earned_by_module,
    )
    report = check_prerequisites(plan, identifier, context)
    if payload.format == "text":
        return PlainTextResponse(report.summary())
    if payload.format == "brief":
        return PlainTextResponse(report.brief())
    body = report.to_dict()
    body["plan_id"] = plan.plan_id
    return body
