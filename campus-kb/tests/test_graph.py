"""先修关系求值单测：确定性、可归因、能处理环。"""

from __future__ import annotations

import pytest

from core.graph import EvalContext, check_prerequisites, evaluate
from core.schema import Course, CreditPlan, ModuleRequirement, PrereqExpr


def course(code: str, prereq: PrereqExpr | None = None, name: str = "") -> Course:
    return Course(code=code, name=name or code, credits=3, module="专业核心课", prerequisites=prereq)


def leaf(code: str) -> PrereqExpr:
    return PrereqExpr(kind="course", course_code=code)


def ctx(*passed: str, earned: dict[str, float] | None = None) -> EvalContext:
    return EvalContext(passed=frozenset(passed), earned_by_module=earned or {})


def test_none_prereq_is_satisfied() -> None:
    result = evaluate(None, ctx())
    assert result.satisfied is True


def test_course_leaf() -> None:
    assert evaluate(leaf("CS101"), ctx("CS101")).satisfied is True
    missed = evaluate(leaf("CS101"), ctx())
    assert missed.satisfied is False
    assert missed.blockers[0]["course_code"] == "CS101"
    assert missed.missing_codes == ["CS101"]


def test_and_requires_all() -> None:
    expr = PrereqExpr(kind="and", children=[leaf("A"), leaf("B")])
    assert evaluate(expr, ctx("A", "B")).satisfied is True
    partial = evaluate(expr, ctx("A"))
    assert partial.satisfied is False
    assert partial.missing_codes == ["B"]


def test_and_collects_all_blockers() -> None:
    expr = PrereqExpr(kind="and", children=[leaf("A"), leaf("B")])
    assert sorted(evaluate(expr, ctx()).missing_codes) == ["A", "B"]


def test_or_any_satisfies() -> None:
    expr = PrereqExpr(kind="or", children=[leaf("A"), leaf("B")])
    assert evaluate(expr, ctx("B")).satisfied is True


def test_or_suggests_cheapest_path() -> None:
    # 分支1：缺 A、B 两门；分支2：缺 C 一门 → 建议先补 C
    expr = PrereqExpr(
        kind="or",
        children=[
            PrereqExpr(kind="and", children=[leaf("A"), leaf("B")]),
            leaf("C"),
        ],
    )
    result = evaluate(expr, ctx())
    assert result.satisfied is False
    assert result.missing_codes == ["C"]


def test_credits_node() -> None:
    expr = PrereqExpr(kind="credits", module="专业核心课", min_credits=40)
    assert evaluate(expr, ctx(earned={"专业核心课": 40})).satisfied is True
    blocked = evaluate(expr, ctx(earned={"专业核心课": 33}))
    assert blocked.satisfied is False
    assert blocked.blockers[0]["kind"] == "credits"
    assert "还差 7" in blocked.blockers[0]["reason"]


def test_credits_node_missing_module_counts_as_zero() -> None:
    expr = PrereqExpr(kind="credits", module="专业核心课", min_credits=1)
    assert evaluate(expr, ctx()).satisfied is False


def test_check_prerequisites_direct(plan: CreditPlan) -> None:
    report = check_prerequisites(plan, "CS305", ctx("CS101"))
    assert report.ok is False
    assert {blocker["course_code"] for blocker in report.direct} == {"CS201", "CS202"}
    assert report.target["course_code"] == "CS305"


def test_check_prerequisites_passes_when_all_met(plan: CreditPlan) -> None:
    report = check_prerequisites(plan, "CS305", ctx("CS101", "CS201", "CS202"))
    assert report.ok is True
    assert report.direct == []


def test_check_prerequisites_resolves_by_name(plan: CreditPlan) -> None:
    report = check_prerequisites(plan, "编译原理", ctx("CS201", "CS202"))
    assert report.target["course_code"] == "CS305"
    assert report.ok is True


def test_check_prerequisites_indirect_chain(plan: CreditPlan) -> None:
    """一门先修都没修时，应指出更上游的 CS101（间接阻塞）。"""
    report = check_prerequisites(plan, "CS305", ctx())
    indirect_codes = {blocker["course_code"] for blocker in report.indirect}
    assert "CS101" in indirect_codes
    assert report.chain, "应给出先修路径"
    assert all(path[0] == "CS305" for path in report.chain)


def test_check_prerequisites_graduation_design_needs_credits_and_internship(plan: CreditPlan) -> None:
    report = check_prerequisites(plan, "CS401", ctx(earned={"专业核心课": 20}))
    kinds = {blocker.get("kind") for blocker in report.direct}
    assert kinds == {"credits", "course"}
    assert report.ok is False


def test_check_prerequisites_credits_gate_can_pass(plan: CreditPlan) -> None:
    report = check_prerequisites(
        plan, "CS401", ctx("CS350", earned={"专业核心课": 44})
    )
    assert report.ok is True


def test_unresolvable_course(plan: CreditPlan) -> None:
    report = check_prerequisites(plan, "不存在的课程", ctx())
    assert report.ok is False
    assert report.target["resolved"] is False
    assert report.notes


def test_cycle_is_detected_not_hung() -> None:
    a = course("A", leaf("B"))
    b = course("B", leaf("A"))
    cyclic = CreditPlan(
        plan_id="p",
        major="m",
        total_credits=30,
        modules=[ModuleRequirement(module="专业核心课", required_credits=30)],
        courses=[a, b],
    )
    report = check_prerequisites(cyclic, "A", EvalContext())
    assert report.ok is False
    assert report.cycle, "应记录环而不是无限递归"


def test_depth_limit_is_respected() -> None:
    courses = [course(f"C{index}", leaf(f"C{index - 1}")) for index in range(1, 20)]
    courses.insert(0, course("C0"))
    long_chain = CreditPlan(
        plan_id="p",
        major="m",
        total_credits=10,
        modules=[ModuleRequirement(module="专业核心课", required_credits=10)],
        courses=courses,
    )
    report = check_prerequisites(long_chain, "C19", EvalContext())
    assert report.ok is False  # 深链不会递归爆炸
