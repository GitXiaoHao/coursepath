"""外部知识库契约测试：这是"Dify 能不能接上"的回归网。

任何一条失败都意味着工作流的知识检索节点会报错或静默降权，
因此这些用例在 CI 中必须全绿才允许打包演示。
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import create_app
from core.index import HybridIndex

from tests.conftest import AUTH, CHUNKS, payload


def test_retrieval_returns_contract_shape(client: TestClient) -> None:
    response = client.post("/retrieval", json=payload(), headers=AUTH)
    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"records"}
    assert body["records"], "应有命中结果"
    record = body["records"][0]
    assert set(record) == {"content", "score", "title", "metadata"}
    assert isinstance(record["content"], str) and record["content"]
    assert isinstance(record["score"], float)
    assert record["metadata"]["doc_id"] == "plan-2026"


def test_metadata_is_never_null(client: TestClient) -> None:
    """``metadata: null`` 会让 Dify 检索管线报错，必须始终是对象。"""
    response = client.post("/retrieval", json=payload(), headers=AUTH)
    for record in response.json()["records"]:
        assert isinstance(record["metadata"], dict)


def test_metadata_defaults_to_empty_object(settings) -> None:
    """分块本身没有元数据时，响应里也必须是 ``{}`` 而不是 ``null``。"""
    index = HybridIndex([ChunkWithoutMeta()])  # type: ignore[list-item]
    client = TestClient(create_app(index=index, settings=settings))
    body = client.post("/retrieval", json=payload(query="元数据"), headers=AUTH).json()
    assert body["records"] and body["records"][0]["metadata"] == {}


def test_no_hit_returns_empty_records_not_404(client: TestClient) -> None:
    response = client.post("/retrieval", json=payload(query="量子色动力学 胶子囚禁"), headers=AUTH)
    assert response.status_code == 200
    assert response.json() == {"records": []}


def test_empty_query_returns_empty_records(client: TestClient) -> None:
    response = client.post("/retrieval", json=payload(query="   "), headers=AUTH)
    assert response.status_code == 200
    assert response.json()["records"] == []


def test_missing_auth_returns_error_code(client: TestClient) -> None:
    response = client.post("/retrieval", json=payload())
    assert response.status_code == 401
    body = response.json()
    assert body["error_code"] == 1001
    assert isinstance(body["error_msg"], str)


def test_wrong_key_returns_error_code(client: TestClient) -> None:
    response = client.post("/retrieval", json=payload(), headers={"Authorization": "Bearer nope"})
    assert response.status_code == 401
    assert response.json()["error_code"] == 1001


def test_malformed_auth_header(client: TestClient) -> None:
    response = client.post("/retrieval", json=payload(), headers={"Authorization": "test-key"})
    assert response.status_code == 401


def test_top_k_and_threshold_passthrough(client: TestClient) -> None:
    body = payload(retrieval_setting={"top_k": 1, "score_threshold": 0.0})
    records = client.post("/retrieval", json=body, headers=AUTH).json()["records"]
    assert len(records) == 1

    strict = payload(retrieval_setting={"top_k": 10, "score_threshold": 0.999})
    records = client.post("/retrieval", json=strict, headers=AUTH).json()["records"]
    assert all(record["score"] >= 0.999 for record in records)


def test_metadata_condition_passthrough(client: TestClient) -> None:
    body = payload(
        query="先修 学分",
        metadata_condition={
            "logical_operator": "and",
            "conditions": [{"name": "module", "comparison_operator": "is", "value": "通识教育"}],
        },
    )
    records = client.post("/retrieval", json=body, headers=AUTH).json()["records"]
    assert records and all(record["metadata"]["module"] == "通识教育" for record in records)


def test_unknown_knowledge_id_is_lenient_by_default(client: TestClient) -> None:
    """默认宽松：ID 写错只告警不报错，避免配置笔误在 Dify 侧变成 500。"""
    response = client.post("/retrieval", json=payload(knowledge_id="typo-id"), headers=AUTH)
    assert response.status_code == 200


def test_strict_knowledge_id_returns_1002(settings) -> None:
    settings.strict_knowledge_id = True
    client = TestClient(create_app(index=HybridIndex(CHUNKS), settings=settings))
    response = client.post("/retrieval", json=payload(knowledge_id="typo-id"), headers=AUTH)
    assert response.status_code == 404
    assert response.json()["error_code"] == 1002


def test_health_endpoint(client: TestClient) -> None:
    body = client.get("/health").json()
    assert body["status"] == "ok"
    assert body["index"]["chunks"] == len(CHUNKS)
    assert body["knowledge_id"] == "k-test"


class ChunkWithoutMeta:
    """模拟"忘记填 metadata"的分块，验证兜底。"""

    id = "nometa"
    content = "没有元数据的一段文本，用于验证 metadata 兜底为对象。"
    title = ""
    metadata: dict = {}
