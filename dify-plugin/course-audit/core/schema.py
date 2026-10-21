"""课程实体与来源引用的领域模型。

设计约束（来自 docs/01 §2.3）：
- 每个可对外回答的事实都必须能回指到 ``SourceRef``（文档/页/表格行/原文片段），
  否则无法满足"引用可逐项核对"的验收口径。
- 先修关系用表达式树表示，以覆盖"修完 A 且 B""A 或 B""A 且（B 或 C）"等条款写法。
"""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, Field, model_validator

PrereqKind = Literal["course", "and", "or", "credits"]


class SourceRef(BaseModel):
    """一条事实的出处，用于答案归因。"""

    doc_id: str = Field(description="文档标识，例如培养方案文件名或哈希")
    title: str = Field(default="", description="文档标题")
    page: int | None = Field(default=None, description="页码，从 1 开始")
    table_row: int | None = Field(default=None, description="表格行号，从 1 开始")
    quote: str = Field(default="", description="支撑该事实的原文片段")


class PrereqExpr(BaseModel):
    """先修关系表达式树。

    - ``kind="course"``：叶子节点，必须给 ``course_code``
    - ``kind="and"`` / ``"or"``：分支节点，必须给 ``children``（至少 2 个）
    - ``kind="credits"``：学分门槛，如"专业核心课已修不少于 40 学分"，
      必须给 ``module`` 与 ``min_credits``
    """

    kind: PrereqKind
    course_code: str | None = None
    children: list["PrereqExpr"] = Field(default_factory=list)
    module: str | None = Field(default=None, description="credits 节点：模块名")
    min_credits: float | None = Field(default=None, description="credits 节点：最低学分")
    source: SourceRef | None = None

    @model_validator(mode="after")
    def _check_shape(self) -> "PrereqExpr":
        if self.kind == "course":
            if not self.course_code:
                raise ValueError("course 节点必须提供 course_code")
            if self.children:
                raise ValueError("course 节点不应有 children")
            if self.module or self.min_credits is not None:
                raise ValueError("course 节点不应有 module/min_credits")
        elif self.kind == "credits":
            if not self.module or self.min_credits is None:
                raise ValueError("credits 节点必须提供 module 与 min_credits")
            if self.min_credits <= 0:
                raise ValueError("min_credits 必须为正数")
            if self.course_code or self.children:
                raise ValueError("credits 节点不应有 course_code/children")
        else:
            if self.course_code is not None:
                raise ValueError(f"{self.kind} 节点不应有 course_code")
            if len(self.children) < 2:
                raise ValueError(f"{self.kind} 节点至少需要 2 个 children")
            if self.module or self.min_credits is not None:
                raise ValueError(f"{self.kind} 节点不应有 module/min_credits")
        return self

    def leaf_codes(self) -> list[str]:
        """展开出所有叶子课程代码（用于建图与可达性分析）。"""
        if self.kind == "course":
            return [self.course_code or ""]
        if self.kind == "credits":
            return []
        out: list[str] = []
        for child in self.children:
            out.extend(child.leaf_codes())
        return out


class Course(BaseModel):
    """一门课程及其学分、所属模块、先修要求。"""

    code: str
    name: str
    credits: float = Field(ge=0, description="学分，允许 0.5 粒度")
    module: str = Field(default="", description="所属课程模块/课程组")
    terms: list[str] = Field(default_factory=list, description="开课学期标签，如 2026-2027-1")
    prerequisites: PrereqExpr | None = Field(
        default=None, description="先修要求；None 表示无先修"
    )
    source: SourceRef | None = None


class ModuleRequirement(BaseModel):
    """培养方案对某个模块的学分要求。"""

    module: str
    required_credits: float = Field(ge=0)
    source: SourceRef | None = None


class CreditPlan(BaseModel):
    """一份培养方案（当前 MVP 只覆盖一个专业）。"""

    plan_id: str
    major: str
    grade: str = Field(default="", description="适用年级，如 2026")
    total_credits: float = Field(ge=0)
    modules: list[ModuleRequirement] = Field(default_factory=list)
    courses: list[Course] = Field(default_factory=list)
    version: str = Field(default="0.0.0")
    #: 只有拿到可核验的培养方案课程表后才允许作确定性学分/先修核算。
    #: 公开简介版资料库可用于检索，但绝不能把推测出的学分或先修关系当成校方规则。
    calculation_available: bool = True
    calculation_notice: str = ""

    def course_map(self) -> dict[str, Course]:
        return {course.code: course for course in self.courses}

    def resolve(self, identifier: str) -> Course | None:
        """按课程代码或名称解析课程。

        名称匹配顺序：精确 → 唯一子串。**子串命中多于一门时返回 None**，
        宁可让上层要求用户澄清，也不猜一个课程去算分。
        """
        if not identifier:
            return None
        code = identifier.strip()
        if code in self.course_map():
            return self.course_map()[code]
        name = identifier.strip()
        exact = [course for course in self.courses if course.name == name]
        if len(exact) == 1:
            return exact[0]
        partial = [course for course in self.courses if name and name in course.name]
        return partial[0] if len(partial) == 1 else None

    def resolve_in_text(self, text: str) -> Course | None:
        """从一句自然语言里找出被问到的课程（对话输入专用）。

        为什么需要它：Chatflow 里用户问的是"编译原理的先修要求是什么？我修过
        CS101"，把这整句传给 ``resolve`` 永远匹配不上。这里先按**最长课程名**
        命中（"编译原理实验" 与 "编译原理" 同时出现时取更长的那个），再退回
        按课程代码找。

        顺序很关键：问句里出现的代码经常是"我已修过"的课，名字才是被问的那门。
        若先认代码，"编译原理的先修要求？我修过 CS101" 会被答成 CS101。
        """
        if not text:
            return None
        hits = [course for course in self.courses if course.name and course.name in text]
        if hits:
            # 先看"谁出现得更靠前"——问句通常把被问的课放在最前面，
            # "已修过的课"跟在后面；位置相同再取更长的名字
            # （"汽车车身设计" 与 "汽车设计" 同时命中时取前者）。
            return min(hits, key=lambda course: (text.index(course.name), -len(course.name)))
        courses = self.course_map()
        codes = [code for code in _CODE_IN_TEXT.findall(text.upper()) if code in courses]
        if codes:
            return courses[max(codes, key=len)]
        return None

    def to_dict(self) -> dict:
        """导出为 ``data/samples/plan.json`` 同构的结构（省略空字段）。"""

        def clean(value):
            if isinstance(value, dict):
                out = {key: clean(item) for key, item in value.items()}
                return {key: item for key, item in out.items() if item not in (None, [], "")}
            if isinstance(value, list):
                return [clean(item) for item in value]
            return value

        return {
            "plan_id": self.plan_id,
            "major": self.major,
            "grade": self.grade,
            "version": self.version,
            "total_credits": self.total_credits,
            "modules": [clean(module.model_dump(exclude_none=True)) for module in self.modules],
            "courses": [clean(course.model_dump(exclude_none=True)) for course in self.courses],
            "calculation_available": self.calculation_available,
            "calculation_notice": self.calculation_notice,
        }


class CompletedCourse(BaseModel):
    """学生已修课程（成绩可选，用于"是否通过"判定时缺省视为通过）。"""

    code: str
    credits: float = Field(ge=0)
    grade: float | None = None
    passed: bool = True


#: 已修课程文本里允许出现的分隔符：中英文逗号、顿号、分号、竖线、空白
_COMPLETED_SEPARATORS = re.compile(r"[,，、;；|\s]+")
#: 自由文本里识别课程代码：2–6 个字母 + 3–4 位数字，前后不能紧邻字母数字
_CODE_IN_TEXT = re.compile(r"(?<![A-Za-z0-9])([A-Za-z]{2,6}\d{3,4})(?![A-Za-z0-9])")
#: 单个记号：课程代码（可含字母数字连字符），可选 `:学分` / `(学分)`
_COMPLETED_TOKEN = re.compile(r"^(?P<code>[A-Za-z0-9][A-Za-z0-9_\-\.]*)(?::|\(|（)?(?P<credits>\d+(?:\.\d+)?)?\)?$")


def parse_completed_text(text: str | None) -> tuple[list[CompletedCourse], list[str]]:
    """把用户在对话框里直接粘的课程串解析成结构化已修课程。

    为什么放在这里：工作流没有"把逗号串切成对象数组"的能力（代码节点无外网、
    Jinja 模板过滤器有限），所以把这一步做进服务端，DSL 只需原样转发文本。

    支持 ``MA101,CS201``、``MA101:3、CS201:4``、``MA101(3)`` 等写法；
    学分缺省为 0，由调用方按培养方案补齐。返回 ``(课程列表, 告警列表)``，
    无法识别的记号进告警而不是抛错——对话场景下拒答比崩溃更友好。
    """
    courses: list[CompletedCourse] = []
    warnings: list[str] = []
    seen: set[str] = set()
    for token in _COMPLETED_SEPARATORS.split(text or ""):
        if not token:
            continue
        match = _COMPLETED_TOKEN.match(token.strip())
        if not match:
            warnings.append(f"无法识别的课程记号：{token}")
            continue
        code = match.group("code").upper()
        if code in seen:
            continue
        seen.add(code)
        credits_text = match.group("credits")
        courses.append(CompletedCourse(code=code, credits=float(credits_text) if credits_text else 0.0))
    return courses, warnings


class Chunk(BaseModel):
    """检索单元：一段可引用的文本及其元数据。

    ``metadata`` 必须始终是扁平字典（值可为 str/int/float/bool/list），
    因为 Dify 的 ``metadata_condition`` 只能对扁平字段做比较。
    """

    id: str
    content: str
    title: str = ""
    metadata: dict = Field(default_factory=dict)

    def to_dict(self) -> dict:
        return {"id": self.id, "content": self.content, "title": self.title, "metadata": dict(self.metadata)}
