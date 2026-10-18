"""学分核算：确定性规则引擎，每条结论都可回溯到某条规则与出处。

规则（编号会出现在 ``explain`` 中，报告"可核验"章节直接引用）：

- ``R1`` 仅计入 ``passed=True`` 的课程
- ``R2`` 培养方案之外的课程不予认定（记入 ``unrecognized``，不产生学分）
- ``R3`` 学分以培养方案登记的值为准；成绩单给的值仅用于比对，不一致时告警
- ``R4`` 模块超额不冲抵其他模块（"超额部分不能冲抵"）
- ``R5`` 总剩余学分 = 各模块缺口之和，而非 ``总学分 - 已修``
"""

from __future__ import annotations

from dataclasses import dataclass, field

from core.schema import CompletedCourse, CreditPlan


@dataclass
class AuditResult:
    plan_id: str
    major: str
    version: str
    total_credits: float
    earned_credits: float
    remaining_credits: float
    modules: list[dict] = field(default_factory=list)
    gaps: list[dict] = field(default_factory=list)
    unrecognized: list[dict] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    explain: list[dict] = field(default_factory=list)

    @property
    def earned_by_module(self) -> dict[str, float]:
        return {row["module"]: row["earned"] for row in self.modules}

    def to_dict(self) -> dict:
        return {
            "plan_id": self.plan_id,
            "major": self.major,
            "version": self.version,
            "total_credits": self.total_credits,
            "earned_credits": self.earned_credits,
            "remaining_credits": self.remaining_credits,
            "modules": self.modules,
            "gaps": self.gaps,
            "unrecognized": self.unrecognized,
            "warnings": self.warnings,
            "explain": self.explain,
            "earned_by_module": self.earned_by_module,
        }


def audit_credits(plan: CreditPlan, completed: list[CompletedCourse]) -> AuditResult:
    course_map = plan.course_map()
    earned: dict[str, float] = {module.module: 0.0 for module in plan.modules}
    unrecognized: list[dict] = []
    warnings: list[str] = []

    for item in completed:
        if not item.passed:
            continue  # R1
        course = course_map.get(item.code)
        if course is None:
            unrecognized.append({"code": item.code, "credits": item.credits})
            continue  # R2
        if abs(course.credits - item.credits) > 1e-9:
            warnings.append(
                f"{course.code} 提交学分 {item.credits:g} 与培养方案 {course.credits:g} 不一致，按方案计"
            )  # R3
        module = course.module
        if not module:
            unrecognized.append({"code": item.code, "credits": course.credits})
            warnings.append(f"{course.code} 未标注所属模块，无法计入任何模块要求")
            continue
        earned[module] = round(earned.get(module, 0.0) + course.credits, 4)

    modules: list[dict] = []
    for requirement in plan.modules:
        module_earned = earned.get(requirement.module, 0.0)
        surplus = max(0.0, module_earned - requirement.required_credits)
        missing = max(0.0, requirement.required_credits - module_earned)
        modules.append(
            {
                "module": requirement.module,
                "required": requirement.required_credits,
                "earned": module_earned,
                "missing": round(missing, 4),
                "surplus": round(surplus, 4),
                "satisfied": missing == 0,
                "source": requirement.source.model_dump() if requirement.source else None,
            }
        )

    # 方案里出现过但未列入模块要求的课程模块（脏数据保护）
    for module in earned:
        if module and module not in {row["module"] for row in modules}:
            modules.append(
                {
                    "module": module,
                    "required": 0.0,
                    "earned": earned[module],
                    "missing": 0.0,
                    "surplus": round(earned[module], 4),
                    "satisfied": True,
                    "source": None,
                }
            )

    gaps = [
        {"module": row["module"], "need": row["required"], "missing": row["missing"]}
        for row in modules
        if row["missing"] > 0
    ]
    total_earned = round(sum(row["earned"] for row in modules), 4)
    # R5：剩余 = 各模块缺口之和（R4 保证超额不冲抵）
    remaining = round(sum(row["missing"] for row in modules), 4)

    explain: list[dict] = [
        {"rule": "R1", "detail": f"计入 {sum(1 for c in completed if c.passed)} 门已通过课程"},
        {
            "rule": "R4",
            "detail": "按模块分别核算：" + "；".join(
                f"{row['module']} {row['earned']:g}/{row['required']:g}"
                for row in modules
                if row["required"] > 0
            ),
        },
        {
            "rule": "R5",
            "detail": f"总剩余 {remaining:g} = 各模块缺口之和 "
            + ("（" + " + ".join(f"{row['missing']:g}" for row in modules if row["missing"] > 0) + "）" if gaps else "（无缺口）"),
        },
    ]
    for row in modules:
        if row["required"] > 0:
            explain.append(
                {
                    "rule": "module",
                    "detail": (
                        f"{row['module']}：要求 {row['required']:g}，已修 {row['earned']:g}，"
                        + (f"缺 {row['missing']:g}" if row["missing"] > 0 else f"超额 {row['surplus']:g}（不冲抵）")
                    ),
                    "source": row["source"],
                }
            )
    if unrecognized:
        explain.append(
            {
                "rule": "R2",
                "detail": f"{len(unrecognized)} 门课程未在培养方案中登记，未计入学分",
            }
        )

    return AuditResult(
        plan_id=plan.plan_id,
        major=plan.major,
        version=plan.version,
        total_credits=plan.total_credits,
        earned_credits=total_earned,
        remaining_credits=remaining,
        modules=modules,
        gaps=gaps,
        unrecognized=unrecognized,
        warnings=warnings,
        explain=explain,
    )
