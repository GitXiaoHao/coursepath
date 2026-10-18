"""服务配置。刻意用环境变量 + dataclass，不引入 pydantic-settings（少一个依赖少一次失败）。"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


def _env_flag(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass
class Settings:
    #: 外部知识库 API Key。**演示环境务必显式设置**，默认值只用于本地自测。
    api_key: str = field(default_factory=lambda: os.environ.get("KB_API_KEY", "dev-key"))
    #: 登记到 Dify 的 knowledge_id
    knowledge_id: str = field(
        default_factory=lambda: os.environ.get("KB_KNOWLEDGE_ID", "campus-kb")
    )
    #: 是否严格校验 knowledge_id。默认宽松：ID 不匹配只告警，避免配置笔误
    #: 在 Dify 侧直接变成 500。设 KB_STRICT_KNOWLEDGE_ID=1 可收紧。
    strict_knowledge_id: bool = field(default_factory=lambda: _env_flag("KB_STRICT_KNOWLEDGE_ID"))
    #: 语料路径（JSONL）
    corpus_path: Path = field(
        default_factory=lambda: Path(
            os.environ.get("KB_CORPUS", str(BASE_DIR / "data" / "samples" / "chunks.jsonl"))
        )
    )
    #: 培养方案路径（JSON，结构化后的）
    plan_path: Path = field(
        default_factory=lambda: Path(
            os.environ.get("KB_PLAN", str(BASE_DIR / "data" / "samples" / "plan.json"))
        )
    )
    #: 融合策略：weighted | rrf
    fusion: str = field(default_factory=lambda: os.environ.get("KB_FUSION", "weighted"))
    #: 稠密通道权重（fusion=weighted 时生效）
    alpha: float = field(default_factory=lambda: float(os.environ.get("KB_ALPHA", "0.5")))
    #: 稠密通道：留空则关闭，退化为纯 BM25
    embed_base_url: str = field(default_factory=lambda: os.environ.get("KB_EMBED_BASE_URL", ""))
    embed_api_key: str = field(default_factory=lambda: os.environ.get("KB_EMBED_API_KEY", ""))
    embed_model: str = field(default_factory=lambda: os.environ.get("KB_EMBED_MODEL", "BAAI/bge-m3"))
    #: 服务版本，进入 /health 与报告
    version: str = field(default_factory=lambda: os.environ.get("KB_VERSION", "0.1.0"))

    @classmethod
    def from_env(cls) -> "Settings":
        return cls()

    def build_embedder(self):
        """按配置构造向量化后端；未配置时返回关闭的 NullEmbedder。"""
        from core.embeddings import NullEmbedder, OpenAICompatibleEmbedder

        if self.embed_base_url and self.embed_api_key:
            return OpenAICompatibleEmbedder(
                base_url=self.embed_base_url,
                api_key=self.embed_api_key,
                model=self.embed_model,
            )
        return NullEmbedder()
