"""领域模型单测：先修表达式树的形状约束与叶子展开。"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from core.schema import Course, CreditPlan, ModuleRequirement, PrereqExpr, SourceRef


def test_course_leaf_requires_code() -> None:
    assert PrereqExpr(kind="course", course_code="CS201").leaf_codes() == ["CS201"]
    with pytest.raises(ValidationError):
        PrereqExpr(kind="course")


def test_course_leaf_rejects_children() -> None:
    with pytest.raises(ValidationError):
        PrereqExpr(kind="course", course_code="CS201", children=[PrereqExpr(kind="course", course_code="X")])


def test_branch_requires_two_children() -> None:
    with pytest.raises(ValidationError):
        PrereqExpr(kind="and", children=[PrereqExpr(kind="course", course_code="CS201")])


def test_branch_rejects_course_code() -> None:
    with pytest.raises(ValidationError):
        PrereqExpr(
            kind="or",
            course_code="CS201",
            children=[
                PrereqExpr(kind="course", course_code="A"),
                PrereqExpr(kind="course", course_code="B"),
            ],
        )


def test_leaf_codes_nested() -> None:
    expr = PrereqExpr(
        kind="and",
        children=[
            PrereqExpr(kind="course", course_code="CS201"),
            PrereqExpr(
                kind="or",
                children=[
                    PrereqExpr(kind="course", course_code="CS202"),
                    PrereqExpr(kind="course", course_code="CS203"),
                ],
            ),
        ],
    )
    assert expr.leaf_codes() == ["CS201", "CS202", "CS203"]


def test_credit_plan_course_map() -> None:
    plan = CreditPlan(
        plan_id="plan-2026",
        major="示例专业",
        total_credits=160,
        modules=[ModuleRequirement(module="专业核心课", required_credits=56)],
        courses=[
            Course(code="CS201", name="数据结构与算法", credits=4, module="专业核心课"),
            Course(code="CS305", name="编译原理", credits=3, module="专业核心课"),
        ],
    )
    assert set(plan.course_map()) == {"CS201", "CS305"}


def test_source_ref_roundtrip() -> None:
    ref = SourceRef(doc_id="plan-2026", page=14, table_row=3, quote="先修课程为数据结构与算法")
    assert ref.model_dump()["table_row"] == 3
