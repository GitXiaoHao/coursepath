"""课程实体与来源引用的领域模型。

设计约束（来自 docs/01 §2.3）：
- 每个可对外回答的事实都必须能回指到 ``SourceRef``（文档/页/表格行/原文片段），
  否则无法满足"引用可逐项核对"的验收口径。
- 先修关系用表达式树表示，以覆盖"修完 A 且 B""A 或 B""A 且（B 或 C）"等条款写法。
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator

PrereqKind = Literal["course", "and", "or"]


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
    """

    kind: PrereqKind
    course_code: str | None = None
    children: list["PrereqExpr"] = Field(default_factory=list)
    source: SourceRef | None = None

    @model_validator(mode="after")
    def _check_shape(self) -> "PrereqExpr":
        if self.kind == "course":
            if not self.course_code:
                raise ValueError("course 节点必须提供 course_code")
            if self.children:
                raise ValueError("course 节点不应有 children")
        else:
            if self.course_code is not None:
                raise ValueError(f"{self.kind} 节点不应有 course_code")
            if len(self.children) < 2:
                raise ValueError(f"{self.kind} 节点至少需要 2 个 children")
        return self

    def leaf_codes(self) -> list[str]:
        """展开出所有叶子课程代码（用于建图与可达性分析）。"""
        if self.kind == "course":
            return [self.course_code or ""]
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

    def course_map(self) -> dict[str, Course]:
        return {course.code: course for course in self.courses}


class CompletedCourse(BaseModel):
    """学生已修课程（成绩可选，用于"是否通过"判定时缺省视为通过）。"""

    code: str
    credits: float = Field(ge=0)
    grade: float | None = None
    passed: bool = True


class Chunk(BaseModel):
    """检索单元：一段可引用的文本及其元数据。

    ``metadata`` 必须始终是扁平字典（值可为 str/int/float/bool/list），
    因为 Dify 的 ``metadata_condition`` 只能对扁平字段做比较。
    """

    id: str
    content: str
    title: str = ""
    metadata: dict = Field(default_factory=dict)
