"""campus-kb：Dify 外部知识库服务 + 课程核算服务。

运行：``uvicorn app.main:app --port 8088``（在 ``campus-kb/`` 目录下）
"""

from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import HTTPException
from fastapi.responses import JSONResponse

from app.api import course as course_api
from app.api import retrieval as retrieval_api
from app.settings import Settings
from core.corpus import load_chunks, load_plan
from core.index import HybridIndex
from core.schema import CreditPlan

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
logger = logging.getLogger("campus-kb")


def build_index(settings: Settings) -> HybridIndex:
    chunks = load_chunks(settings.corpus_path)
    logger.info("装载语料 %s 条（%s）", len(chunks), settings.corpus_path)
    return HybridIndex(
        chunks=chunks,
        embedder=settings.build_embedder(),
        fusion=settings.fusion,
        alpha=settings.alpha,
    )


def create_app(
    index: HybridIndex | None = None,
    settings: Settings | None = None,
    plan: CreditPlan | None = None,
) -> FastAPI:
    """应用工厂：测试时注入内存索引与培养方案，避免依赖磁盘语料与网络。"""
    settings = settings or Settings.from_env()
    if settings.api_key == "dev-key":
        logger.warning("正在使用默认 API Key（dev-key），仅限本地自测；演示环境请设置 KB_API_KEY")
    index = index if index is not None else build_index(settings)
    if plan is None:
        plan = load_plan(settings.plan_path)
    if plan is None:
        logger.warning("未加载到培养方案（%s），/audit 与 /prereq 将返回配置缺失错误", settings.plan_path)

    app = FastAPI(
        title="campus-kb",
        version=settings.version,
        description=(
            "课径 · 可核算的培养方案问答 —— 外部知识库检索服务。"
            "不修改 Dify 源码，通过 External Knowledge API 契约接入。"
        ),
    )
    app.state.settings = settings
    app.state.index = index
    app.state.plan = plan

    app.include_router(retrieval_api.router)
    app.include_router(course_api.router)

    @app.get("/health", tags=["ops"], summary="健康检查")
    def health() -> dict:
        return {
            "status": "ok",
            "version": settings.version,
            "knowledge_id": settings.knowledge_id,
            "corpus": str(settings.corpus_path),
            "index": app.state.index.stats(),
            "plan": (
                {
                    "plan_id": app.state.plan.plan_id,
                    "major": app.state.plan.major,
                    "courses": len(app.state.plan.courses),
                    "version": app.state.plan.version,
                }
                if app.state.plan is not None
                else None
            ),
        }

    @app.exception_handler(HTTPException)
    async def _http_error(_: Request, exc: HTTPException) -> JSONResponse:
        """把错误体规整成 Dify 期望的 ``error_code`` / ``error_msg`` 结构。"""
        detail = exc.detail
        if isinstance(detail, dict) and "error_code" in detail:
            body = detail
        else:
            body = {"error_code": 2001 if exc.status_code >= 500 else 1001, "error_msg": str(detail)}
        return JSONResponse(status_code=exc.status_code, content=body)

    @app.exception_handler(Exception)
    async def _unhandled(_: Request, exc: Exception) -> JSONResponse:  # pragma: no cover
        logger.exception("未处理异常: %s", exc)
        return JSONResponse(
            status_code=500, content={"error_code": 2001, "error_msg": "RAG 内部错误"}
        )

    return app


app = create_app()
