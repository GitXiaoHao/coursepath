"""共享夹具：测试全程不依赖磁盘语料、不依赖网络、不依赖 Dify。"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.settings import Settings
from core.index import HybridIndex
from core.schema import Chunk

#: 测试语料：覆盖"与/或"两种先修写法与三个不同模块，便于验证过滤行为。
CHUNKS = [
    Chunk(
        id="c1",
        content="编译原理课程先修要求：需修完数据结构与算法，且修完计算机组成原理，否则不得选课。",
        title="先修要求表 · 编译原理",
        metadata={"doc_id": "plan-2026", "page": 14, "module": "专业核心课", "course_code": "CS305"},
    ),
    Chunk(
        id="c2",
        content="操作系统课程先修要求：修完数据结构与算法或计算机组成原理之一即可选课。",
        title="先修要求表 · 操作系统",
        metadata={"doc_id": "plan-2026", "page": 14, "module": "专业核心课", "course_code": "CS303"},
    ),
    Chunk(
        id="c3",
        content="通识选修课共 4 学分，须跨学科门类选修，不得用本专业课程替代。",
        title="通识教育模块",
        metadata={"doc_id": "plan-2026", "page": 8, "module": "通识教育", "course_code": ""},
    ),
    Chunk(
        id="c4",
        content="毕业设计（论文）先修条件：专业核心课已修不少于 40 学分，且专业实习考核合格。",
        title="实践教学环节",
        metadata={"doc_id": "plan-2026", "page": 20, "module": "实践教学环节", "course_code": ""},
    ),
]

AUTH = {"Authorization": "Bearer test-key"}


@pytest.fixture()
def settings(tmp_path) -> Settings:
    return Settings(
        api_key="test-key",
        knowledge_id="k-test",
        corpus_path=tmp_path / "missing.jsonl",
    )


@pytest.fixture()
def index() -> HybridIndex:
    return HybridIndex(CHUNKS)


@pytest.fixture()
def client(settings: Settings, index: HybridIndex) -> TestClient:
    return TestClient(create_app(index=index, settings=settings))


def payload(query: str = "编译原理 先修要求", **overrides) -> dict:
    body = {
        "knowledge_id": "k-test",
        "query": query,
        "retrieval_setting": {"top_k": 5, "score_threshold": 0.0},
    }
    body.update(overrides)
    return body
