"""混合检索单测。"""

from __future__ import annotations

from core.index import HybridIndex, tokenize
from core.schema import Chunk

from tests.conftest import CHUNKS


def test_tokenize_drops_stopwords_and_punctuation() -> None:
    tokens = tokenize("编译原理的先修要求是什么？")
    assert "编译" in tokens or "编译原理" in tokens
    assert "的" not in tokens
    assert all(token.strip() for token in tokens)


def test_bm25_ranks_relevant_chunk_first(index: HybridIndex) -> None:
    hits = index.search("编译原理 先修 计算机组成原理", top_k=3)
    assert hits
    assert hits[0].chunk.id == "c1"


def test_top_k_respected(index: HybridIndex) -> None:
    assert len(index.search("先修", top_k=1)) == 1
    assert len(index.search("先修", top_k=10)) <= len(CHUNKS)


def test_score_threshold_filters(index: HybridIndex) -> None:
    all_hits = index.search("编译原理 先修", top_k=10, score_threshold=0.0)
    strict = index.search("编译原理 先修", top_k=10, score_threshold=0.99)
    assert len(strict) <= len(all_hits)
    assert all(hit.score >= 0.99 for hit in strict)


def test_metadata_condition_filters(index: HybridIndex) -> None:
    condition = {"logical_operator": "and", "conditions": [
        {"name": "module", "comparison_operator": "is", "value": "通识教育"}
    ]}
    hits = index.search("先修 学分", top_k=10, metadata_condition=condition)
    assert [hit.chunk.id for hit in hits] == ["c3"]


def test_metadata_condition_with_no_match_returns_empty(index: HybridIndex) -> None:
    condition = {"logical_operator": "and", "conditions": [
        {"name": "module", "comparison_operator": "is", "value": "不存在的模块"}
    ]}
    assert index.search("先修", top_k=10, metadata_condition=condition) == []


def test_empty_index_and_empty_query() -> None:
    empty = HybridIndex([])
    assert empty.search("任何问题") == []
    assert HybridIndex(CHUNKS).search("   ") == []


def test_rrf_fusion_still_ranks_relevant_first() -> None:
    rrf = HybridIndex(CHUNKS, fusion="rrf")
    hits = rrf.search("毕业设计 先修 专业核心课 40 学分", top_k=3)
    assert hits
    assert hits[0].chunk.id == "c4"


def test_scores_are_deterministic() -> None:
    a = HybridIndex(CHUNKS).search("先修要求", top_k=4)
    b = HybridIndex(CHUNKS).search("先修要求", top_k=4)
    assert [(hit.chunk.id, hit.score) for hit in a] == [(hit.chunk.id, hit.score) for hit in b]


def test_stats_reports_dense_disabled_by_default(index: HybridIndex) -> None:
    stats = index.stats()
    assert stats["chunks"] == len(CHUNKS)
    assert stats["dense_enabled"] is False
    assert stats["embedder"] == "null"

# --------------------------------------------------------------------- 课程名加权
def _named_index() -> HybridIndex:
    """同一门课出现在"课程设置"和"先修要求"两张表里，名字互相包含。"""
    from core.schema import Chunk

    chunks = [
        Chunk(id="seq", content="课程设置｜汽车设计；学分：4", title="汽车设计", metadata={"course_code": "VE203", "table_kind": "courses"}),
        Chunk(id="pre", content="先修要求｜汽车设计；先修：机械设计", title="汽车设计", metadata={"course_code": "VE203", "table_kind": "prereq"}),
        Chunk(id="other", content="先修要求｜汽车车身设计；先修：汽车设计", title="汽车车身设计", metadata={"course_code": "VE214", "table_kind": "prereq"}),
    ]
    return HybridIndex(chunks, course_names={"VE203": "汽车设计", "VE214": "汽车车身设计"})


def test_name_in_query_prefers_longest_course_name() -> None:
    index = _named_index()
    assert index.name_in_query("汽车车身设计要修什么") == "VE214"
    assert index.name_in_query("汽车设计需要先修哪些课") == "VE203"
    assert index.name_in_query("毕业还差多少学分") is None


def test_name_boost_prefers_the_named_course_rows() -> None:
    index = _named_index()
    hits = index.search("汽车车身设计要修什么先修课", top_k=3)
    assert hits[0].chunk.id == "other"  # 点名的课自己那行排第一
    hits = index.search("汽车设计需要先修哪些课", top_k=3)
    assert hits[0].chunk.metadata["course_code"] == "VE203"


def test_name_boost_can_be_disabled_for_ablation() -> None:
    index = _named_index()
    with_boost = index.search("汽车车身设计要修什么先修课", top_k=1)[0]
    without = index.search("汽车车身设计要修什么先修课", top_k=1, use_names=False)[0]
    assert with_boost.score >= without.score
