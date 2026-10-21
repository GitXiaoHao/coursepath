"""把结构化培养方案转换为可检索的目录分块。

外部知识库中的原始段落适合回答概述和制度条款；但学生往往按课程代码提问，原始
分块未必逐门列出课程。这个适配层直接从同一份 ``CreditPlan`` 生成课程/模块目录，
因此“检索到的出处”和“核算实际使用的规则”不会发生两份数据漂移。
"""

from __future__ import annotations

from core.schema import Chunk, CreditPlan, PrereqExpr, SourceRef


def _metadata(source: SourceRef | None, *, module: str = "", course_code: str = "") -> dict:
    return {
        "doc_id": source.doc_id if source else "",
        "doc_title": source.title if source else "培养方案",
        "doc_type": "培养方案",
        "page": source.page if source else None,
        "table_row": source.table_row if source else None,
        "module": module,
        "course_code": course_code,
    }


def _prereq_text(expr: PrereqExpr | None, plan: CreditPlan) -> str:
    if expr is None:
        return "无登记先修要求"
    if expr.kind == "course":
        course = plan.course_map().get(expr.course_code or "")
        return f"{course.name}（{course.code}）" if course else (expr.course_code or "")
    if expr.kind == "credits":
        return f"{expr.module}已修不少于 {float(expr.min_credits or 0):g} 学分"
    separator = "且" if expr.kind == "and" else "或"
    return separator.join(_prereq_text(child, plan) for child in expr.children)


def build_plan_catalog_chunks(plan: CreditPlan) -> list[Chunk]:
    """生成模块与课程条目，每条保留 ``SourceRef`` 中的页码、表格行和原文。"""
    chunks: list[Chunk] = []
    for module in plan.modules:
        source = module.source
        content = f"培养方案模块｜{module.module}：要求修满 {module.required_credits:g} 学分。"
        if source and source.quote:
            content += f"原文依据：{source.quote}"
        chunks.append(
            Chunk(
                id=f"plan-catalog:{plan.plan_id}:module:{module.module}",
                title=f"{plan.major} · 模块学分要求",
                content=content,
                metadata=_metadata(source, module=module.module),
            )
        )
    for course in plan.courses:
        source = course.source
        content = (
            f"培养方案课程｜{course.name}（{course.code}）：{course.credits:g} 学分，"
            f"所属{course.module}；先修要求：{_prereq_text(course.prerequisites, plan)}。"
        )
        if course.terms:
            content += "建议开课学期：" + "、".join(course.terms) + "。"
        if source and source.quote:
            content += f"原文依据：{source.quote}"
        chunks.append(
            Chunk(
                id=f"plan-catalog:{plan.plan_id}:course:{course.code}",
                title=f"{plan.major} · 课程目录",
                content=content,
                metadata=_metadata(source, module=course.module, course_code=course.code),
            )
        )
    return chunks
