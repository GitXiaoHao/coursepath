"""学生档案管理接口：查看、合并、覆盖、删除课程。"""

from __future__ import annotations

import re

from fastapi import APIRouter, Header, Request
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field

from app.api.retrieval import api_error, check_bearer
from core.schema import CompletedCourse, parse_completed_text

router = APIRouter(tags=["student-profile"])


class CoursesPayload(BaseModel):
    completed: list[CompletedCourse] = Field(default_factory=list)
    completed_text: str | None = None


class ProfileSummaryRequest(BaseModel):
    profile_id: str
    format: str = "json"


class ProfileCommandRequest(BaseModel):
    profile_id: str
    query: str
    format: str = "json"


_COURSE_CODE_RE = re.compile(r"(?<![A-Za-z0-9])([A-Za-z]{2,6}\d{2,4})(?![A-Za-z0-9])", re.IGNORECASE)


def _validate(request: Request, profile_id: str, authorization: str | None) -> None:
    check_bearer(request.app.state.settings, authorization)
    if not request.app.state.profile_store.valid_profile_id(profile_id):
        raise api_error(400, 1001, "profile_id 格式无效")


def _courses(payload: CoursesPayload, request: Request) -> list[CompletedCourse]:
    parsed, _warnings = parse_completed_text(payload.completed_text)
    course_map = request.app.state.plan.course_map()
    merged: dict[str, CompletedCourse] = {}
    for item in [*payload.completed, *parsed]:
        code = item.code.upper()
        credits = item.credits if item.credits > 0 else course_map.get(code, item).credits
        merged[code] = item.model_copy(update={"code": code, "credits": credits})
    return list(merged.values())


def _response(request: Request, courses: list[CompletedCourse]) -> dict:
    course_map = request.app.state.plan.course_map()
    return {
        "course_count": len(courses),
        "courses": [
            {
                "code": item.code,
                "name": course_map[item.code].name if item.code in course_map else None,
                "credits": item.credits,
                "passed": item.passed,
            }
            for item in sorted(courses, key=lambda row: row.code)
        ],
    }


def _summary(request: Request, courses: list[CompletedCourse]) -> str:
    if not courses:
        return "### 我的课程档案\n当前还没有已修课程记录。你可以说：`我已修 EE101、CS101`。"
    course_map = request.app.state.plan.course_map()
    lines = [f"### 我的课程档案\n已记录 **{len(courses)}** 门通过课程："]
    for item in sorted(courses, key=lambda row: row.code):
        name = course_map[item.code].name if item.code in course_map else "课程名称待补全"
        lines.append(f"- {name}（{item.code}，{item.credits:g} 学分）")
    lines.append("\n新增课程时直接说“我已修 + 课程代码”；后续核算会自动使用这些记录。")
    return "\n".join(lines)


@router.post("/profile-summary", summary="以文本形式查看当前学生档案")
def profile_summary(payload: ProfileSummaryRequest, request: Request, authorization: str | None = Header(default=None)):
    _validate(request, payload.profile_id, authorization)
    courses = request.app.state.profile_store.get_courses(payload.profile_id)
    if payload.format == "text":
        return PlainTextResponse(_summary(request, courses))
    return _response(request, courses)


@router.post("/profile-command", summary="带二次确认的课程档案删除与清空")
def profile_command(payload: ProfileCommandRequest, request: Request, authorization: str | None = Header(default=None)):
    _validate(request, payload.profile_id, authorization)
    query = payload.query.strip()
    store = request.app.state.profile_store
    normalized = query.upper()
    codes = list(dict.fromkeys(code.upper() for code in _COURSE_CODE_RE.findall(query)))

    if "确认清空" in query:
        store.clear(payload.profile_id)
        text = "### 我的课程档案\n已清空当前档案。之后可用“我已修 + 课程代码”重新录入。"
    elif "清空" in query:
        text = "### 请确认清空\n这会删除当前档案中的全部课程记录。若确认，请回复：`确认清空档案`。"
    elif "确认删除" in query and codes:
        remaining = store.remove_course(payload.profile_id, codes[0])
        text = f"### 我的课程档案\n已删除 {codes[0]}。\n\n" + _summary(request, remaining)
    elif "删除" in query and codes:
        text = f"### 请确认删除\n将从当前档案删除 {codes[0]}。若确认，请回复：`确认删除 {codes[0]}`。"
    elif "删除" in query:
        text = "### 删除课程\n请提供课程代码，例如：`删除 EE101`。删除前还需要你再回复一次确认。"
    else:
        text = "### 档案管理\n可使用：`删除 EE101` 或 `清空我的档案`。系统会先要求确认，不会立即删除。"

    if payload.format == "text":
        return PlainTextResponse(text)
    return {"message": text, "courses": _response(request, store.get_courses(payload.profile_id))["courses"]}


@router.get("/profiles/{profile_id}", summary="查看学生已修课程档案")
def get_profile(profile_id: str, request: Request, authorization: str | None = Header(default=None)) -> dict:
    _validate(request, profile_id, authorization)
    return _response(request, request.app.state.profile_store.get_courses(profile_id))


@router.post("/profiles/{profile_id}/courses", summary="向学生档案合并课程")
def merge_profile_courses(profile_id: str, payload: CoursesPayload, request: Request, authorization: str | None = Header(default=None)) -> dict:
    _validate(request, profile_id, authorization)
    courses = request.app.state.profile_store.merge_courses(profile_id, _courses(payload, request))
    return _response(request, courses)


@router.put("/profiles/{profile_id}/courses", summary="覆盖学生档案课程")
def replace_profile_courses(profile_id: str, payload: CoursesPayload, request: Request, authorization: str | None = Header(default=None)) -> dict:
    _validate(request, profile_id, authorization)
    courses = request.app.state.profile_store.replace_courses(profile_id, _courses(payload, request))
    return _response(request, courses)


@router.delete("/profiles/{profile_id}/courses/{course_code}", summary="删除档案中的一门课程")
def remove_profile_course(profile_id: str, course_code: str, request: Request, authorization: str | None = Header(default=None)) -> dict:
    _validate(request, profile_id, authorization)
    courses = request.app.state.profile_store.remove_course(profile_id, course_code)
    return _response(request, courses)


@router.delete("/profiles/{profile_id}", summary="清空学生档案")
def clear_profile(profile_id: str, request: Request, authorization: str | None = Header(default=None)) -> dict:
    _validate(request, profile_id, authorization)
    request.app.state.profile_store.clear(profile_id)
    return _response(request, [])
