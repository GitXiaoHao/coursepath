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
    "高等数学 I": "GE101",
    "工程力学": "ME102",
    "机械原理": "ME103",
    "机械设计": "ME104",
    "专业实习": "PR103",
    "汽车构造": "VE201",
    "汽车工程概论": "AE101",
    "汽车电子控制": "VE205",
    "程序设计基础": "CS101",
    "电工电子技术": "EE101",
    "工程制图": "ME101",
}
MODULES = ["通识教育", "工程基础", "专业核心课", "专业选修课", "实践教学环节"]


def signature(expr: PrereqExpr | None):
    """把先修表达式压成可比较的结构：('and', ('course', 'ME101'), ...)。"""
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
    assert len(tables[0].rows) == 6  # 5 个模块 + 合计
    assert len(tables[1].rows) == 44
    assert len(tables[2].rows) == 30


def test_classify_tables() -> None:
    tables = split_markdown_tables(MD.read_text(encoding="utf-8"))
    assert [classify(table) for table in tables] == ["modules", "courses", "prereq"]


def test_separator_rows_are_dropped() -> None:
    tables = split_markdown_tables("| a | b |\n| --- | --- |\n| 1 | 2 |\n")
    assert len(tables) == 1
    assert tables[0].rows == (("1", "2"),)


def test_table_cell_and_column() -> None:
    table = split_markdown_tables("| 模块 | 要求学分 |\n| --- | --- |\n| 核心 | 46 |\n")[0]
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
    parsed = parse_prereq_text("机械原理或机械设计之一", NAMES)
    assert parsed.ok
    assert signature(parsed.expr) == ("or", (("course", "ME103"), ("course", "ME104")))


def test_prereq_and_with_comma() -> None:
    parsed = parse_prereq_text("工程力学，且需修完机械原理", NAMES)
    assert parsed.ok
    assert signature(parsed.expr) == ("and", (("course", "ME102"), ("course", "ME103")))


def test_prereq_and_three_children() -> None:
    """汽车电子控制：且（电工电子技术、程序设计基础、汽车构造）。"""
    parsed = parse_prereq_text("先修电工电子技术、程序设计基础，且需修完汽车构造", NAMES)
    assert parsed.ok
    assert signature(parsed.expr) == (
        "and",
        (("course", "EE101"), ("course", "CS101"), ("course", "VE201")),
    )


def test_prereq_credits_gate() -> None:
    parsed = parse_prereq_text("已完成专业核心课不少于 40 学分，且专业实习考核合格", NAMES, MODULES)
    assert parsed.ok
    assert signature(parsed.expr) == (
        "and",
        (("credits", "专业核心课", 40.0), ("course", "PR103")),
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
    names = {"机械原理": "ME103", "机械原理（进阶）": "ME203"}
    parsed = parse_prereq_text("先修机械", names)
    assert parsed.expr is None
    assert parsed.unresolved


# --------------------------------------------------------------------------- 组装计划
@pytest.fixture(scope="module")
def built():
    tables = load_tables(MD)
    return build_plan(
        tables,
        doc_id="vehicle-demo-plan-2025",
        major="车辆工程（示范培养方案）",
        plan_id="vehicle-engineering-demo-2025",
        grade="2025",
        doc_title="车辆工程（示范培养方案）",
    )


def test_build_modules(built) -> None:
    rows = {module.module: module.required_credits for module in built.plan.modules}
    assert rows == {"通识教育": 48, "工程基础": 34, "专业核心课": 46, "专业选修课": 16, "实践教学环节": 16}
    assert built.plan.total_credits == 160


def test_build_courses(built) -> None:
    assert len(built.plan.courses) == 44
    course = built.plan.resolve("VE205")
    assert course is not None
    assert course.name == "汽车电子控制"
    assert course.credits == 4
    assert list(course.terms) == ["2027-2028-1"]


def test_build_prerequisites(built) -> None:
    got = {course.code: signature(course.prerequisites) for course in built.plan.courses}
    assert got["VE201"] == ("and", (("course", "ME101"), ("course", "AE101")))
    assert got["VE205"] == ("and", (("course", "EE101"), ("course", "CS101"), ("course", "VE201")))
    assert got["PR104"] == ("and", (("credits", "专业核心课", 40.0), ("course", "PR103")))
    assert got["GE101"] is None


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
    chunks = build_chunks(tables, doc_id="vehicle-demo-plan-2025")
    assert len(chunks) == 6 + 44 + 30  # 学分结构表 6 行（含合计）+ 课程 44 + 先修 30
    first = chunks[0]
    assert first.metadata["page"] == 1
    assert first.metadata["table_row"] == 1
    assert first.metadata["module"] == "通识教育"
    assert "48" in first.content

    course_chunk = next(chunk for chunk in chunks if chunk.metadata.get("course_code") == "VE205")
    assert course_chunk.metadata["doc_type"] == "培养方案"
    assert "汽车电子控制" in course_chunk.content
    assert all(isinstance(chunk.metadata.get("page"), int) for chunk in chunks)


def test_metadata_survives_json_roundtrip(built) -> None:
    import json

    chunks = build_chunks(load_tables(MD), doc_id="vehicle-demo-plan-2025")
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
    leaf = next(course for course in exported["courses"] if course["code"] == "GE102")
    assert "children" not in leaf["prerequisites"]
