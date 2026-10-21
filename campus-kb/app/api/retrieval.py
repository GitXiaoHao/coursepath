"""外部知识库检索接口：严格对齐 Dify External Knowledge API 契约。

契约要点（已核实，见 docs/01 §2.3）：
- Dify 在登记的 endpoint 后自动追加 ``/retrieval``，POST + ``Authorization: Bearer {API_KEY}``
- 请求必填 ``knowledge_id`` / ``query`` / ``retrieval_setting``；``metadata_condition`` 可选
- 响应 ``{"records": [{"content","score","title","metadata"}]}``
- **``metadata`` 必须是对象**，``null`` 会让 Dify 检索管线报错 —— 这里用
  ``default_factory=dict`` + 显式 ``or {}`` 双重兜底
- 无结果返回 ``{"records": []}``，**不是** 404
"""

from __future__ import annotations

import logging
import secrets
from typing import Any, Literal

from fastapi import APIRouter, Header, Request
from fastapi.exceptions import HTTPException
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

router = APIRouter(tags=["external-knowledge"])


class RetrievalSetting(BaseModel):
    top_k: int = Field(default=5, ge=1, le=100)
    score_threshold: float = Field(default=0.0)


class MetadataConditionItem(BaseModel):
    name: str
    comparison_operator: str
    value: Any = None


class MetadataCondition(BaseModel):
    logical_operator: Literal["and", "or"] = "and"
    conditions: list[MetadataConditionItem] = Field(default_factory=list)


class RetrievalRequest(BaseModel):
    knowledge_id: str
    query: str
    retrieval_setting: RetrievalSetting
    metadata_condition: MetadataCondition | None = None


class Record(BaseModel):
    content: str
    score: float
    title: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)


class RetrievalResponse(BaseModel):
    records: list[Record] = Field(default_factory=list)


def api_error(status_code: int, error_code: int, error_msg: str) -> HTTPException:
    """统一错误体：``{"error_code": 1001, "error_msg": "..."}``。"""
    return HTTPException(
        status_code=status_code,
        detail={"error_code": error_code, "error_msg": error_msg},
    )


def check_bearer(settings: Any, authorization: str | None) -> None:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise api_error(401, 1001, "缺少 Bearer 授权头")
    token = authorization.split(" ", 1)[1].strip()
    if not secrets.compare_digest(token, settings.api_key):
        raise api_error(401, 1001, "API Key 无效")


@router.post("/retrieval", response_model=RetrievalResponse, summary="外部知识库检索")
def retrieval(
    payload: RetrievalRequest,
    request: Request,
    authorization: str | None = Header(default=None),
) -> RetrievalResponse:
    settings = request.app.state.settings
    check_bearer(settings, authorization)

    if settings.strict_knowledge_id and payload.knowledge_id != settings.knowledge_id:
        raise api_error(404, 1002, f"knowledge_id 不存在: {payload.knowledge_id}")
    if payload.knowledge_id != settings.knowledge_id:
        logger.warning(
            "knowledge_id 不匹配（收到 %s，配置 %s），按宽松模式继续",
            payload.knowledge_id,
            settings.knowledge_id,
        )

    if not payload.query.strip():
        # 空 query 在 Dify 侧不会发生，但直接返回空结果比抛错更安全
        return RetrievalResponse(records=[])

    condition = payload.metadata_condition.model_dump() if payload.metadata_condition else None
    hits = request.app.state.index.search(
        query=payload.query,
        top_k=payload.retrieval_setting.top_k,
        score_threshold=payload.retrieval_setting.score_threshold,
        metadata_condition=condition,
    )

    records = [
        Record(
            content=hit.chunk.content,
            # 保留 6 位小数，便于评测脚本复现同一排序；
            # 对外截到 1.0：分数会作为「匹配度」展示给用户，超过 1 的相似度不合理
            # （内部加权仍用未截断的分数排序，故不影响名次）
            score=round(min(1.0, float(hit.score)), 6),
            title=hit.chunk.title,
            metadata=hit.chunk.metadata or {},
        )
        for hit in hits
    ]
    logger.info("retrieval query=%r top_k=%s hits=%s", payload.query, payload.retrieval_setting.top_k, len(records))
    return RetrievalResponse(records=records)
