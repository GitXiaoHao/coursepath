"""混合检索索引：BM25 + 稠密向量 + 融合 + 元数据过滤。

这是"自研"部分的第二个证据位（第一个是表格解析）。Dify 内置知识库的同义能力
是"分段 + 单路向量检索"，无法表达跨页表格结构与先修关系字段，也无法在检索期
按 ``metadata_condition`` 做结构化过滤——因此这里不是重新造轮子，而是补足缺口。

融合策略（可通过 ``fusion`` 参数切换，用于实验对比）：
- ``weighted``（默认）：先各自 min-max 归一化再加权求和，得分直接可比 ``score_threshold``
- ``rrf``：Reciprocal Rank Fusion，只依赖名次，量纲不友好但抗分数量纲差异
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Mapping, Sequence

import jieba
from rank_bm25 import BM25Plus  # 用 Plus 变体：idf = log((N+1)/df) 恒非负，
# 避免小语料/高频词（如"学分"）出现负分导致相关分块被证据门槛误杀。

from core.embeddings import Embedder, NullEmbedder, cosine
from core.filters import matches
from core.intent import INTENT_BOOST, preferred_tables
from core.schema import Chunk

#: 问题里出现某门课的完整名称时，该课自己的行比"另一门课提到它"更有用
#: （例：问"汽车设计需要先修哪些课"，命中的可能是"汽车车身设计"那行的先修列）。
NAME_BOOST = 1.25

#: 极简停用词，避免"的/了/哪"等虚词主导 BM25。
_STOPWORDS = frozenset(
    "的 了 和 与 及 或 是 在 有 我 你 他 她 它 们 这 那 哪 哪些 什么 怎么 请问 是否 吗 呢 吧 啊 "
    "a an the is are of to and or in on for with".split()
)


def tokenize(text: str) -> list[str]:
    """中文按 jieba 分词，英文/数字按小写保留。"""
    tokens = []
    for raw in jieba.lcut(text):
        token = raw.strip().lower()
        if not token or token in _STOPWORDS:
            continue
        if all(not ch.isalnum() for ch in token):
            continue
        tokens.append(token)
    return tokens


@dataclass(frozen=True)
class Hit:
    """一条检索结果。"""

    chunk: Chunk
    score: float
    bm25: float = 0.0
    dense: float = 0.0


def _min_max(values: Sequence[float]) -> list[float]:
    if not values:
        return []
    low, high = min(values), max(values)
    if high - low < 1e-12:
        return [1.0 if value > 0 else 0.0 for value in values]
    return [(value - low) / (high - low) for value in values]


class HybridIndex:
    """内存版混合索引。当前语料规模（约 200 份文档）下无需外部向量库。"""

    def __init__(
        self,
        chunks: Sequence[Chunk],
        embedder: Embedder | None = None,
        fusion: str = "weighted",
        alpha: float = 0.5,
        rrf_k: int = 60,
        course_names: Mapping[str, str] | None = None,
    ) -> None:
        if fusion not in {"weighted", "rrf"}:
            raise ValueError(f"未知融合策略: {fusion}")
        self.chunks: list[Chunk] = list(chunks)
        self.embedder = embedder or NullEmbedder()
        self.fusion = fusion
        self.alpha = alpha
        self.rrf_k = rrf_k
        #: 课程代码 -> 课程名称，用于"问题里点名某门课"的排序加权
        self.course_names: dict[str, str] = dict(course_names or {})

        self._tokens: list[list[str]] = [tokenize(chunk.content) for chunk in self.chunks]
        # BM25Plus 在空语料上会除零，这里保留一个占位文档兜底
        self._bm25 = BM25Plus(self._tokens or [["__empty__"]])
        self._vectors: list[list[float]] = []
        if self.embedder.enabled and self.chunks:
            self._vectors = self.embedder.encode([chunk.content for chunk in self.chunks])

    # ---- 只读属性 ----
    @property
    def size(self) -> int:
        return len(self.chunks)

    def stats(self) -> dict:
        return {
            "chunks": len(self.chunks),
            "embedder": self.embedder.name,
            "dense_enabled": bool(self._vectors),
            "fusion": self.fusion,
            "alpha": self.alpha,
        }

    # ---- 检索 ----
    def name_in_query(self, query: str) -> str | None:
        """返回问题里出现的**最长**课程名对应的课程代码；没有则 None。

        只做"整名出现在问题里"的判断，不做模糊匹配——模糊匹配会把
        "汽车设计"和"汽车车身设计"混在一起，正是本函数要避免的情况。
        """
        if not self.course_names:
            return None
        compact = query.replace(" ", "")
        best_code: str | None = None
        best_len = 0
        for code, name in self.course_names.items():
            if len(name) < 2:
                continue
            if name.replace(" ", "") in compact and len(name) > best_len:
                best_code, best_len = code, len(name)
        return best_code

    def search(
        self,
        query: str,
        top_k: int = 5,
        score_threshold: float = 0.0,
        metadata_condition: dict | None = None,
        use_intent: bool = True,
        use_names: bool = True,
    ) -> list[Hit]:
        if not self.chunks or not query.strip():
            return []

        # 1) 结构化过滤先行：条件不满足的分块连打分资格都没有
        allowed = [
            index
            for index, chunk in enumerate(self.chunks)
            if matches(chunk.metadata, metadata_condition)
        ]
        if not allowed:
            return []

        # 2) 两路打分（对全语料打分后按 allow-list 取子集，避免重建 BM25）
        query_tokens = tokenize(query)
        bm25_scores = list(self._bm25.get_scores(query_tokens))[: len(self.chunks)]
        dense_scores: list[float] = []
        if self._vectors:
            query_vector = self.embedder.encode([query])[0]
            dense_scores = [cosine(query_vector, vector) for vector in self._vectors]

        bm25_subset = _min_max([bm25_scores[index] for index in allowed])
        dense_subset = (
            _min_max([dense_scores[index] for index in allowed]) if dense_scores else []
        )

        # 3) 融合
        hits: list[Hit] = []
        if self.fusion == "weighted" and dense_subset:
            for offset, index in enumerate(allowed):
                score = self.alpha * dense_subset[offset] + (1 - self.alpha) * bm25_subset[offset]
                hits.append(
                    Hit(
                        chunk=self.chunks[index],
                        score=score,
                        bm25=bm25_scores[index],
                        dense=dense_scores[index] if dense_scores else 0.0,
                    )
                )
        else:
            # 纯 BM25（无稠密通道）或 RRF 融合
            bm25_rank = _rank_map(bm25_subset)
            dense_rank = _rank_map(dense_subset) if dense_subset else {}
            for offset, index in enumerate(allowed):
                if self.fusion == "rrf":
                    score = 1.0 / (self.rrf_k + bm25_rank[offset] + 1)
                    if offset in dense_rank:
                        score += 1.0 / (self.rrf_k + dense_rank[offset] + 1)
                else:
                    score = bm25_subset[offset]
                hits.append(
                    Hit(
                        chunk=self.chunks[index],
                        score=score,
                        bm25=bm25_scores[index],
                        dense=dense_scores[index] if dense_scores else 0.0,
                    )
                )

        # 3.5) 意图加权：同一门课可能同时出现在「课程设置表」和「先修要求表」，
        # 纯词法分几乎打平；按问题类型给对应表型轻微加权，只改排序不改召回集合。
        preferred = preferred_tables(query) if use_intent else frozenset()
        if preferred:
            hits = [
                replace(hit, score=hit.score * INTENT_BOOST)
                if hit.chunk.metadata.get("table_kind") in preferred
                else hit
                for hit in hits
            ]

        # 3.6) 课程名加权：问题里出现完整课程名时，优先该课自己的行。
        named = self.name_in_query(query) if use_names else None
        if named:
            hits = [
                replace(hit, score=hit.score * NAME_BOOST)
                if hit.chunk.metadata.get("course_code") == named
                else hit
                for hit in hits
            ]

        # 4) 证据门槛 + 阈值 + 截断
        # 完全没有词法/语义重叠的分块必须剔除：否则归一化后得分为 0、而
        # Dify 默认 score_threshold=0.0 会让这些无关分块全部通过，直接污染答案。
        hits = [
            hit
            for hit in hits
            if (hit.bm25 > 0.0 or hit.dense > 0.0) and hit.score >= score_threshold
        ]
        hits.sort(key=lambda hit: (-hit.score, hit.chunk.id))
        top_k = max(0, int(top_k))
        return hits[:top_k] if top_k else []


def _rank_map(values: Sequence[float]) -> dict[int, int]:
    """下标 -> 名次（0 起），用于 RRF。"""
    order = sorted(range(len(values)), key=lambda index: (-values[index], index))
    return {index: rank for rank, index in enumerate(order)}
