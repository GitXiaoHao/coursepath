"""解析器测试：Markdown 表格 → CreditPlan，并验证与示例 plan.json 等价。"""

from __future__ import annotations

from pathlib import Path

import pytest

from core.corpus import load_plan
from core.parser import (
    build_chunks,
    build_plan,
    classify,
    load_tables,
    parse_prereq_text,
    split_markdown_tables,
)
from core.schema import CreditPlan, PrereqExpr

BASE = Path(__file__).resolve().parent.parent
MD = BASE / "data" / "samples" / "md" / "plan-demo.md"
NAMES = {
    "程序设计基础": "CS101",
    "数据结构与算法": "CS201",
    "计算机组成原理": "CS202",
    "操作系统": "CS303",
    "编译原理": "CS305",
    "专业实习": "CS350",
}
MODULES = ["通识教育", "专业核心课", "专业选修课", "实践教学环节"]


def signature(expr: PrereqExpr | None):
    """把先修表达式压成可比较的结构：('and', ('course','CS201'), ...)。"""
    if expr is None:
        return None
    if expr.kind == "course":
        return ("course", expr.course_code)
    if expr.kind == "credits":
        return ("credits", expr.module, expr.min_credits)
    return (expr.kind, tuple(signature(child) for child in expr.children))


# --------------------------------------------------------------------------- 表格切分
def test_split_markdown_tables() -> None:
    tables = split_markdown_tables(MD.read_text(encoding="utf-8"))
    assert [table.title for table in tables] == ["一、学分结构与毕业要求", "二、课程设置表", "三、先修要求表"]
    assert [table.page for table in tables] == [1, 2, 3]
    assert tables[0].headers == ("模块", "要求学分", "说明")
    assert len(tables[1].rows) == 16
    assert len(tables[2].rows) == 6


def test_classify_tables() -> None:
    tables = split_markdown_tables(MD.read_text(encoding="utf-8"))
    assert [classify(table) for table in tables] == ["modules", "courses", "prereq"]


def test_separator_rows_are_dropped() -> None:
    tables = split_markdown_tables("| a | b |\n| --- | --- |\n| 1 | 2 |\n")
    assert len(tables) == 1
    assert tables[0].rows == (("1", "2"),)


def test_table_cell_and_column() -> None:
    table = split_markdown_tables("| 模块 | 要求学分 |\n| --- | --- |\n| 核心 | 56 |\n")[0]
    row = table.rows[0]
    assert table.cell(row, ("模块",)) == "核心"
    assert table.cell(row, ("不存在的列",)) == ""
    assert table.column(("要求学分",)) == 1


# --------------------------------------------------------------------------- 先修文本
def test_prereq_simple() -> None:
    parsed = parse_prereq_text("先修程序设计基础", NAMES)
    assert parsed.ok
    assert signature(parsed.expr) == ("course", "CS101")


def test_prereq_or() -> None:
    parsed = parse_prereq_text("数据结构与算法或计算机组成原理之一", NAMES)
    assert parsed.ok
    assert signature(parsed.expr) == ("or", (("course", "CS201"), ("course", "CS202")))


def test_prereq_and_with_comma() -> None:
    parsed = parse_prereq_text("数据结构与算法，且需修完计算机组成原理", NAMES)
    assert parsed.ok
    assert signature(parsed.expr) == ("and", (("course", "CS201"), ("course", "CS202")))


def test_prereq_credits_gate() -> None:
    parsed = parse_prereq_text("已完成专业核心课不少于 40 学分，且专业实习考核合格", NAMES, MODULES)
    assert parsed.ok
    assert signature(parsed.expr) == (
        "and",
        (("credits", "专业核心课", 40.0), ("course", "CS350")),
    )


def test_prereq_credits_gate_without_module_list_falls_back() -> None:
    parsed = parse_prereq_text("专业核心课不少于 40 学分", NAMES)
    assert parsed.ok
    assert signature(parsed.expr) == ("credits", "专业核心课", 40.0)


def test_prereq_none_values() -> None:
    for text in ("无", "无先修要求", "—", ""):
        parsed = parse_prereq_text(text, NAMES)
        assert parsed.expr is None
        assert parsed.unresolved == []


def test_prereq_unknown_course_is_reported() -> None:
    parsed = parse_prereq_text("先修量子力学导论", NAMES)
    assert parsed.expr is None
    assert parsed.unresolved == ["量子力学导论"]


def test_prereq_ambiguous_name_is_not_guessed() -> None:
    names = {"数据结构与算法": "CS201", "数据结构与算法（进阶）": "CS301"}
    parsed = parse_prereq_text("先修数据结构", names)
    assert parsed.expr is None
    assert parsed.unresolved


# --------------------------------------------------------------------------- 组装计划
@pytest.fixture(scope="module")
def built():
    tables = load_tables(MD)
    return build_plan(
        tables,
        doc_id="plan-demo-2026",
        major="示范专业",
        plan_id="plan-demo-2026",
        grade="2026",
        doc_title="培养方案 2026",
    )


def test_build_modules(built) -> None:
    rows = {module.module: module.required_credits for module in built.plan.modules}
    assert rows == {"通识教育": 48, "专业核心课": 56, "专业选修课": 24, "实践教学环节": 32}
    assert built.plan.total_credits == 160


def test_build_courses(built) -> None:
    assert len(built.plan.courses) == 16
    course = built.plan.resolve("CS305")
    assert course is not None
    assert course.name == "编译原理"
    assert course.credits == 3
    assert list(course.terms) == ["2026-2027-2"]


def test_build_prerequisites(built) -> None:
    got = {course.code: signature(course.prerequisites) for course in built.plan.courses}
    assert got["CS201"] == ("course", "CS101")
    assert got["CS303"] == ("or", (("course", "CS201"), ("course", "CS202")))
    assert got["CS305"] == ("and", (("course", "CS201"), ("course", "CS202")))
    assert got["CS401"] == ("and", (("credits", "专业核心课", 40.0), ("course", "CS350")))
    assert got["CS101"] is None


def test_build_has_no_warnings(built) -> None:
    assert built.warnings == []
    assert built.unresolved == []


def test_parsed_plan_matches_sample_json(built) -> None:
    """样例 Markdown 与 hand-written plan.json 必须解析出同一套课程/学分/先修关系。"""
    expected = load_plan(BASE / "data" / "samples" / "plan.json")
    assert isinstance(expected, CreditPlan)
    assert [module.module for module in built.plan.modules] == [module.module for module in expected.modules]
    assert [module.required_credits for module in built.plan.modules] == [
        module.required_credits for module in expected.modules
    ]
    assert built.plan.total_credits == expected.total_credits

    got_courses = {
        course.code: (course.name, course.credits, course.module, signature(course.prerequisites))
        for course in built.plan.courses
    }
    want_courses = {
        course.code: (course.name, course.credits, course.module, signature(course.prerequisites))
        for course in expected.courses
    }
    assert got_courses == want_courses


def test_missing_total_credits_is_derived_with_warning() -> None:
    text = (
        "| 模块 | 要求学分 |\n| --- | --- |\n"
        "| 核心 | 30 |\n| 选修 | 10 |\n"
        "\n| 课程代码 | 课程名称 | 学分 | 模块 |\n| --- | --- | --- | --- |\n"
        "| C1 | 课程一 | 4 | 核心 |\n"
    )
    built = build_plan(split_markdown_tables(text), doc_id="d", major="m", plan_id="p")
    assert built.plan.total_credits == 40
    assert any("总学分" in warning for warning in built.warnings)


def test_unknown_module_is_warned() -> None:
    text = (
        "| 模块 | 要求学分 |\n| --- | --- |\n| 核心 | 30 |\n"
        "\n| 课程代码 | 课程名称 | 学分 | 模块 |\n| --- | --- | --- | --- |\n"
        "| C1 | 课程一 | 4 | 不存在的模块 |\n"
    )
    built = build_plan(split_markdown_tables(text), doc_id="d", major="m", plan_id="p")
    assert any("不在学分结构表中" in warning for warning in built.warnings)


def test_unresolved_prereq_does_not_fake_relation() -> None:
    text = (
        "| 模块 | 要求学分 |\n| --- | --- |\n| 核心 | 30 |\n"
        "\n| 课程代码 | 课程名称 | 学分 | 模块 |\n| --- | --- | --- | --- |\n"
        "| C1 | 课程一 | 4 | 核心 |\n| C2 | 课程二 | 4 | 核心 |\n"
        "\n| 课程代码 | 课程名称 | 先修要求 |\n| --- | --- | --- |\n"
        "| C2 | 课程二 | 先修量子力学 |\n"
    )
    built = build_plan(split_markdown_tables(text), doc_id="d", major="m", plan_id="p")
    course = built.plan.resolve("C2")
    assert course is not None
    assert course.prerequisites is None  # 宁可不写，也不伪造一条错误先修边
    assert built.unresolved and built.unresolved[0]["items"] == ["量子力学"]


def test_synthetic_course_code_when_missing() -> None:
    text = (
        "| 模块 | 要求学分 |\n| --- | --- |\n| 核心 | 30 |\n"
        "\n| 课程名称 | 学分 | 模块 |\n| --- | --- | --- |\n| 无代码课程 | 3 | 核心 |\n"
    )
    built = build_plan(split_markdown_tables(text), doc_id="d", major="m", plan_id="p")
    assert built.plan.courses[0].code.startswith("X")


# --------------------------------------------------------------------------- 语料生成
def test_build_chunks(built) -> None:
    tables = load_tables(MD)
    chunks = build_chunks(tables, doc_id="plan-demo-2026")
    assert len(chunks) == 5 + 16 + 6  # 学分结构表 5 行（含合计）+ 课程 16 + 先修 6
    first = chunks[0]
    assert first.metadata["page"] == 1
    assert first.metadata["table_row"] == 1
    assert first.metadata["module"] == "通识教育"
    assert "48" in first.content

    course_chunk = next(chunk for chunk in chunks if chunk.metadata.get("course_code") == "CS305")
    assert course_chunk.metadata["doc_type"] == "培养方案"
    assert "编译原理" in course_chunk.content
    assert all(isinstance(chunk.metadata.get("page"), int) for chunk in chunks)


def test_metadata_survives_json_roundtrip(built) -> None:
    import json

    chunks = build_chunks(load_tables(MD), doc_id="plan-demo-2026")
    for chunk in chunks:
        restored = json.loads(json.dumps(chunk.to_dict(), ensure_ascii=False))
        assert restored["metadata"]["page"] == chunk.metadata["page"]
        assert restored["content"] == chunk.content


def test_plan_dict_roundtrip_is_lossless(built) -> None:
    """to_dict() → JSON → 重新校验，先修关系与学分必须完全一致（入库/入库复查用）。"""
    import json

    from core.schema import CreditPlan as Plan

    exported = json.loads(json.dumps(built.plan.to_dict(), ensure_ascii=False))
    restored = Plan.model_validate(exported)
    assert len(restored.courses) == len(built.plan.courses)
    assert {course.code: signature(course.prerequisites) for course in restored.courses} == {
        course.code: signature(course.prerequisites) for course in built.plan.courses
    }
    # 叶子节点不应写出空的 children 字段
    leaf = next(course for course in exported["courses"] if course["code"] == "CS201")
    assert "children" not in leaf["prerequisites"]
