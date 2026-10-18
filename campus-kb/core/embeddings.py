"""向量化后端：默认离线可用，联网时可切到 OpenAI 兼容接口。

对应 docs/02 的部署决策 D4：
- 主用云端 OpenAI 兼容 embedding（省内存，且能与 Dify 内置知识库基线用同一模型，保证对比公平）
- 备用本地 CPU 小模型
- 单元测试与离线开发使用 ``NullEmbedder``（关闭稠密通道，退化为 BM25），
  这样 ``pytest`` 无需任何 API Key 即可全绿。
"""

from __future__ import annotations

import math
from typing import Sequence

try:  # httpx 仅在真正走网络时才需要
    import httpx
except ImportError:  # pragma: no cover
    httpx = None  # type: ignore[assignment]


class Embedder:
    """向量化后端接口。"""

    name: str = "base"
    dim: int = 0
    enabled: bool = False

    def encode(self, texts: Sequence[str]) -> list[list[float]]:  # pragma: no cover
        raise NotImplementedError


class NullEmbedder(Embedder):
    """不做任何向量化，令索引退化为纯 BM25。"""

    name = "null"
    dim = 0
    enabled = False

    def encode(self, texts: Sequence[str]) -> list[list[float]]:
        return []


class OpenAICompatibleEmbedder(Embedder):
    """调用 OpenAI 兼容的 ``/v1/embeddings`` 接口。

    :param base_url: 例如 ``https://api.siliconflow.cn/v1``
    :param model: 例如 ``BAAI/bge-m3``
    """

    enabled = True

    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str,
        timeout: float = 30.0,
        batch_size: int = 32,
    ) -> None:
        if httpx is None:  # pragma: no cover
            raise RuntimeError("启用稠密检索需要 httpx：pip install httpx")
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.timeout = timeout
        self.batch_size = batch_size
        self.name = f"openai-compatible:{model}"
        self.dim = 0

    def encode(self, texts: Sequence[str]) -> list[list[float]]:
        out: list[list[float]] = []
        for start in range(0, len(texts), self.batch_size):
            batch = list(texts[start : start + self.batch_size])
            if not batch:
                continue
            response = httpx.post(
                f"{self.base_url}/embeddings",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json={"model": self.model, "input": batch},
                timeout=self.timeout,
            )
            response.raise_for_status()
            payload = response.json()
            vectors = [item["embedding"] for item in payload.get("data", [])]
            if len(vectors) != len(batch):
                raise RuntimeError("embedding 返回条数与输入不一致")
            self.dim = len(vectors[0]) if vectors else self.dim
            out.extend(vectors)
        return out


def cosine(left: Sequence[float], right: Sequence[float]) -> float:
    """余弦相似度，结果裁剪到 [0, 1]，便于直接与 ``score_threshold`` 比较。"""
    if not left or not right or len(left) != len(right):
        return 0.0
    dot = sum(a * b for a, b in zip(left, right))
    norm_l = math.sqrt(sum(a * a for a in left))
    norm_r = math.sqrt(sum(b * b for b in right))
    if norm_l == 0 or norm_r == 0:
        return 0.0
    return max(0.0, min(1.0, dot / (norm_l * norm_r)))
