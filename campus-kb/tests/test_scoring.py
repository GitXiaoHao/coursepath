"""学分核算单测：规则逐条可验证。"""

from __future__ import annotations

from core.scoring import audit_credits
from core.schema import CompletedCourse, Course, CreditPlan, ModuleRequirement


def make_plan(
    modules: list[tuple[str, float]],
    courses: list[tuple[str, str, float]],
    total: float = 100,
) -> CreditPlan:
    return CreditPlan(
        plan_id="p-test",
        major="示范专业",
        total_credits=total,
        modules=[ModuleRequirement(module=name, required_credits=credits) for name, credits in modules],
        courses=[
            Course(code=code, name=code, credits=credits, module=module)
            for code, module, credits in courses
        ],
    )


def completed(*items: tuple[str, float], passed: bool = True) -> list[CompletedCourse]:
    return [CompletedCourse(code=code, credits=credits, passed=passed) for code, credits in items]


def test_module_arithmetic() -> None:
    plan = make_plan([("核心", 10), ("选修", 5)], [("C1", "核心", 4), ("C2", "核心", 3), ("E1", "选修", 2)])
    result = audit_credits(plan, completed(("C1", 4), ("C2", 3), ("E1", 2)))
    rows = {row["module"]: row for row in result.modules}
    assert rows["核心"]["earned"] == 7
    assert rows["核心"]["missing"] == 3
    assert rows["选修"]["earned"] == 2
    assert rows["选修"]["missing"] == 3
    assert result.remaining_credits == 6


def test_surplus_is_not_transferable() -> None:
    """R4：模块超额不能冲抵其他模块缺口。"""
    plan = make_plan([("核心", 8), ("选修", 5)], [("C1", "核心", 10), ("E1", "选修", 0)])
    result = audit_credits(plan, completed(("C1", 10)))
    rows = {row["module"]: row for row in result.modules}
    assert rows["核心"]["surplus"] == 2
    assert rows["选修"]["missing"] == 5
    assert result.remaining_credits == 5  # 而不是 3


def test_remaining_equals_sum_of_gaps() -> None:
    """R5：剩余 = 缺口之和，不等于总学分 - 已修。"""
    plan = make_plan([("核心", 10), ("选修", 10)], [("C1", "核心", 10), ("E1", "选修", 2)], total=20)
    result = audit_credits(plan, completed(("C1", 10), ("E1", 2)))
    assert result.earned_credits == 12
    assert result.total_credits - result.earned_credits == 8
    assert result.remaining_credits == 8
    assert sum(row["missing"] for row in result.modules) == result.remaining_credits


def test_remaining_differs_from_total_minus_earned_when_surplus_exists() -> None:
    """有超额时不冲抵：剩余 > 总学分 - 已修。"""
    plan = make_plan([("核心", 8), ("选修", 10)], [("C1", "核心", 12), ("E1", "选修", 2)], total=18)
    result = audit_credits(plan, completed(("C1", 12), ("E1", 2)))
    assert result.earned_credits == 14
    assert result.total_credits - result.earned_credits == 4
    assert result.remaining_credits == 8  # 选修缺口 8，核心超额 4 不冲抵


def test_failed_course_is_ignored() -> None:
    """R1：只计已通过课程。"""
    plan = make_plan([("核心", 10)], [("C1", "核心", 6)])
    result = audit_credits(plan, completed(("C1", 6), passed=False))
    assert result.earned_credits == 0
    assert result.remaining_credits == 10


def test_unrecognized_course_is_not_counted() -> None:
    """R2：方案外课程不产生学分，但要报出来便于人工核对。"""
    plan = make_plan([("核心", 10)], [("C1", "核心", 4)])
    result = audit_credits(plan, completed(("C1", 4), ("XX999", 3)))
    assert result.earned_credits == 4
    assert result.unrecognized == [{"code": "XX999", "credits": 3}]
    assert any(row["rule"] == "R2" for row in result.explain)


def test_credit_mismatch_uses_plan_value_and_warns() -> None:
    """R3：学分以培养方案为准，不一致要告警。"""
    plan = make_plan([("核心", 10)], [("C1", "核心", 4)])
    result = audit_credits(plan, completed(("C1", 5)))
    assert result.earned_credits == 4
    assert any("不一致" in warning for warning in result.warnings)


def test_no_gap_explain() -> None:
    plan = make_plan([("核心", 4)], [("C1", "核心", 4)])
    result = audit_credits(plan, completed(("C1", 4)))
    assert result.gaps == []
    assert any("无缺口" in row["detail"] for row in result.explain)


def test_explain_contains_rules() -> None:
    plan = make_plan([("核心", 8)], [("C1", "核心", 4)])
    result = audit_credits(plan, completed(("C1", 4)))
    rules = {row["rule"] for row in result.explain}
    assert {"R1", "R4", "R5"} <= rules


def test_half_credit_rounding() -> None:
    plan = make_plan([("核心", 7)], [("C1", "核心", 3.5), ("C2", "核心", 3.5)])
    result = audit_credits(plan, completed(("C1", 3.5), ("C2", 3.5)))
    assert result.earned_credits == 7
    assert result.remaining_credits == 0


def test_earned_by_module_matches_rows() -> None:
    plan = make_plan([("核心", 10), ("选修", 5)], [("C1", "核心", 4), ("E1", "选修", 2)])
    result = audit_credits(plan, completed(("C1", 4), ("E1", 2)))
    assert result.earned_by_module == {"核心": 4.0, "选修": 2.0}
