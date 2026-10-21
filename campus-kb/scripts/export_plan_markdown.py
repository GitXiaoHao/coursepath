#!/usr/bin/env python
"""把结构化培养方案（plan.json）反向渲染成 Markdown 培养方案。

用途有两个：
1. 生成随仓库分发的样例文档 ``data/samples/md/plan-demo.md``，让"文档"与"结构化方案"只有一份事实来源；
2. 作为解析器的**往返测试**：``plan.json → Markdown → parser → plan`` 应得到同一套课程与先修关系。

用法::

    python -m scripts.export_plan_markdown data/samples/plan.json -o data/samples/md/plan-demo.md
    python -m scripts.export_plan_markdown data/samples/plan.json --check   # 只校验往返一致
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from core.corpus import load_plan
from core.parser import build_plan, load_tables, parse_prereq_text
from core.schema import CreditPlan, PrereqExpr

#: 条款表（教务口径）。合成内容，用于演示"条款类问题"分支，不涉及任何真实院校。
CLAUSES = [
    (
        "转专业学分认定",
        "从其他专业转入车辆工程",
        "原专业已修且成绩合格的同名课程可直接认定；名称不同但内容相近的课程须提交课程大纲"
        "经认定后方可替代，认定结果计入专业选修课学分，不重复计入专业核心课。",
    ),
    (
        "跨专业学分上限",
        "以外专业课程申请替代",
        "替代课程合计不超过专业选修课要求的 16 学分，超出部分不计入毕业学分。",
    ),
    (
        "重修与免修",
        "课程重修或免修",
        "重修以最高一次成绩计入；免修须通过免修考核，考核合格后按培养方案登记的学分认定。",
    ),
]

MODULE_NOTES = {
    "通识教育": "含思想政治、外语、体育与通识选修",
    "工程基础": "本专业需具备的工程学科基础",
    "专业核心课": "必须先修后修，见先修要求表",
    "专业选修课": "在专业选修池内修满即可",
    "实践教学环节": "含实训、实习与毕业设计",
}


def prereq_text(expr: PrereqExpr | None, plan: CreditPlan) -> str:
    """把先修表达式渲染成解析器能原样读回的写法。"""
    if expr is None:
        return "无"

    def leaf(code: str) -> str:
        course = plan.course_map().get(code)
        return course.name if course else code

    if expr.kind == "course":
        return f"先修{leaf(expr.course_code or '')}"
    if expr.kind == "credits":
        return f"已完成{expr.module}不少于 {float(expr.min_credits or 0):g} 学分"
    if expr.kind == "or":
        return "或".join(leaf(child.course_code or "") for child in expr.children if child.kind == "course") + "之一"
    if expr.kind == "and":
        parts: list[str] = []
        for index, child in enumerate(expr.children):
            if child.kind == "course":
                name = leaf(child.course_code or "")
                if index == 0:
                    parts.append(f"先修{name}")
                elif index == len(expr.children) - 1:
                    parts.append(f"且需修完{name}")
                else:
                    parts.append(name)
            elif child.kind == "credits":
                parts.append(f"已完成{child.module}不少于 {float(child.min_credits or 0):g} 学分")
            elif child.kind == "or":
                parts.append("或".join(leaf(grand.course_code or "") for grand in child.children if grand.kind == "course") + "之一")
            else:
                parts.append(prereq_text(child, plan))
        return "，".join(parts) if len(parts) > 1 else parts[0]
    raise ValueError(f"未知先修类型：{expr.kind}")


def render(plan: CreditPlan, *, title: str, note: str) -> str:
    lines: list[str] = [f"# {title}", "", f"> {note}", ""]

    lines += ["## 一、学分结构与毕业要求", "", "| 模块 | 要求学分 | 说明 |", "| --- | --- | --- |"]
    for module in plan.modules:
        note_text = MODULE_NOTES.get(module.module, "")
        lines.append(f"| {module.module} | {module.required_credits:g} | {note_text} |")
    lines.append(f"| 合计 | {plan.total_credits:g} | 毕业总学分 |")
    lines.append("")

    lines += ["## 二、课程设置表", "", "| 课程代码 | 课程名称 | 学分 | 模块 | 开课学期 |", "| --- | --- | --- | --- | --- |"]
    for course in plan.courses:
        terms = "、".join(course.terms) if course.terms else "—"
        lines.append(f"| {course.code} | {course.name} | {course.credits:g} | {course.module} | {terms} |")
    lines.append("")

    lines += ["## 三、先修要求表", "", "| 课程代码 | 课程名称 | 先修要求 |", "| --- | --- | --- |"]
    for course in plan.courses:
        if course.prerequisites is None:
            continue
        lines.append(f"| {course.code} | {course.name} | {prereq_text(course.prerequisites, plan)} |")
    lines.append("")

    lines += [
        "## 四、学分认定与条款",
        "",
        "| 条款主题 | 适用情形 | 认定规则 |",
        "| --- | --- | --- |",
    ]
    for topic, scene, rule in CLAUSES:
        lines.append(f"| {topic} | {scene} | {rule} |")
    lines.append("")
    return "\n".join(lines)


def check_roundtrip(plan: CreditPlan, text: str, md_path: Path) -> list[str]:
    """按解析器路径重读 Markdown，返回不一致项（空列表表示完全一致）。"""
    tables = load_tables_text(text, md_path)
    doc_id = plan.courses[0].source.doc_id if plan.courses and plan.courses[0].source else "demo"
    built = build_plan(
        tables,
        doc_id=doc_id,
        major=plan.major,
        plan_id=plan.plan_id,
        grade=plan.grade,
        version=plan.version,
        total_credits=plan.total_credits,
        doc_title=plan.major,
    )
    problems: list[str] = []
    if built.warnings:
        problems += [f"解析告警：{warning}" for warning in built.warnings]
    if built.unresolved:
        problems += [f"先修未解析：{item}" for item in built.unresolved]
    got_modules = [(m.module, m.required_credits) for m in built.plan.modules]
    want_modules = [(m.module, m.required_credits) for m in plan.modules]
    if got_modules != want_modules:
        problems.append(f"模块不一致：{got_modules} != {want_modules}")
    if built.plan.total_credits != plan.total_credits:
        problems.append(f"总学分不一致：{built.plan.total_credits} != {plan.total_credits}")
    got = {
        c.code: (c.name, c.credits, c.module, c.prerequisites.model_dump() if c.prerequisites else None)
        for c in built.plan.courses
    }
    want = {
        c.code: (c.name, c.credits, c.module, c.prerequisites.model_dump() if c.prerequisites else None)
        for c in plan.courses
    }
    for code in sorted(set(got) | set(want)):
        if got.get(code) != want.get(code):
            problems.append(f"课程 {code} 不一致：{got.get(code)} != {want.get(code)}")
    return problems


def load_tables_text(text: str, path: Path):
    """复用解析器的 Markdown 切分，但直接吃内存里的文本（避免先落盘）。"""
    from core.parser import split_markdown_tables

    del path
    return split_markdown_tables(text)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="plan.json → Markdown 培养方案（含往返校验）")
    parser.add_argument("plan", type=Path, help="结构化培养方案 JSON")
    parser.add_argument("-o", "--out", type=Path, help="输出 Markdown 路径")
    parser.add_argument("--check", action="store_true", help="只做往返校验，不写文件")
    parser.add_argument("--title", default="", help="文档标题（默认用方案的专业名）")
    args = parser.parse_args(argv)

    plan = load_plan(args.plan)
    if plan is None:
        print(f"读取失败：{args.plan}", file=sys.stderr)
        return 2
    title = args.title or plan.major
    note = (
        "语料说明：本文件为**合成样例**，专业名称、课程与学分全部虚构，不含任何真实院校信息，"
        "用于单测与演示。真实培养方案请放 `campus-kb/data/real/`（已 gitignore，不随仓库分发）。"
    )
    text = render(plan, title=title, note=note)

    problems = check_roundtrip(plan, text, args.out or args.plan)
    if problems:
        print("往返校验失败：", file=sys.stderr)
        for problem in problems:
            print("  - " + problem, file=sys.stderr)
        return 1
    print(f"往返校验通过：{len(plan.courses)} 门课程 / {len(plan.modules)} 个模块 / 总学分 {plan.total_credits:g}")

    if not args.check:
        if args.out is None:
            print("未指定 --out，仅校验", file=sys.stderr)
            return 0
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text, encoding="utf-8", newline="\n")
        print(f"已写入 {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
