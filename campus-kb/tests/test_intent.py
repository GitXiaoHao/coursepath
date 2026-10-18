"""意图识别测试：规则必须可枚举、可解释，且不误伤无关查询。"""

from __future__ import annotations

from core.index import HybridIndex
from core.intent import KIND_TO_TABLE, detect_kinds, preferred_tables

from tests.conftest import CHUNKS


def test_credits_intent() -> None:
    assert detect_kinds("数据结构与算法几个学分") == ("credits",)
    assert preferred_tables("数据结构与算法几个学分") == frozenset({"courses"})


def test_prereq_intent() -> None:
    assert "prereq" in detect_kinds("编译原理需要先修哪些课")
    assert preferred_tables("编译原理需要先修哪些课") == frozenset({"prereq"})


def test_term_intent_maps_to_courses() -> None:
    assert preferred_tables("专业实习在哪一学期开课") == frozenset({"courses"})
    assert KIND_TO_TABLE["term"] == "courses"


def test_module_intent() -> None:
    assert preferred_tables("实践教学环节包括哪些内容") == frozenset({"modules"})


def test_multi_intent() -> None:
    kinds = detect_kinds("专业核心课模块要求多少学分")
    assert set(kinds) >= {"credits", "module"}


def test_unknown_query_gets_no_boost() -> None:
    assert detect_kinds("天气怎么样") == ()
    assert preferred_tables("天气怎么样") == frozenset()


def test_boost_is_noop_without_intent() -> None:
    index = HybridIndex(CHUNKS)
    plain = index.search("编译原理", top_k=3, use_intent=False)
    with_intent = index.search("编译原理", top_k=3, use_intent=True)
    assert [hit.chunk.id for hit in plain] == [hit.chunk.id for hit in with_intent]


def test_boost_changes_order_when_intent_matches() -> None:
    """构造一个平局：同一门课出现在两张表里，问"学分"应优先课程设置表。"""
    from core.schema import Chunk

    chunks = [
        Chunk(
            id="prereq-row",
            content="先修要求表｜课程名称：编译原理；先修要求：数据结构与算法",
            title="先修要求表",
            metadata={"table_kind": "prereq", "course_code": "CS305"},
        ),
        Chunk(
            id="course-row",
            content="课程设置表｜课程名称：编译原理；学分：3",
            title="课程设置表",
            metadata={"table_kind": "courses", "course_code": "CS305"},
        ),
    ]
    index = HybridIndex(chunks)
    baseline = [hit.chunk.id for hit in index.search("编译原理 学分", top_k=2, use_intent=False)]
    boosted = [hit.chunk.id for hit in index.search("编译原理 学分", top_k=2, use_intent=True)]
    assert set(baseline) == {"prereq-row", "course-row"}
    assert boosted[0] == "course-row"
