"""语料装载：JSONL -> Chunk 列表。

JSONL 每行形如::

    {"id": "c1", "content": "...", "title": "培养方案 2026 · 专业核心课",
     "metadata": {"doc_id": "plan-2026", "page": 12, "course_code": "CS201", "module": "专业核心课"}}

``metadata`` 一律扁平化，保证 ``metadata_condition`` 可比较。
"""

from __future__ import annotations

import json
from pathlib import Path

from core.schema import Chunk, CreditPlan


def _flatten(metadata: dict) -> dict:
    flat: dict = {}
    for key, value in metadata.items():
        if isinstance(value, dict):
            for sub_key, sub_value in value.items():
                flat[f"{key}.{sub_key}"] = sub_value
        elif isinstance(value, (list, tuple)):
            flat[key] = ",".join(str(item) for item in value)
        else:
            flat[key] = value
    return flat


def load_chunks(path: str | Path) -> list[Chunk]:
    """读取 JSONL 语料；文件不存在时返回空列表（服务仍可启动）。"""
    file_path = Path(path)
    if not file_path.is_file():
        return []

    chunks: list[Chunk] = []
    with file_path.open("r", encoding="utf-8") as handle:
        for line_no, line in enumerate(handle, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError as exc:  # 报错要能定位到行
                raise ValueError(f"{file_path}:{line_no} JSON 解析失败: {exc}") from exc
            chunks.append(
                Chunk(
                    id=str(payload["id"]),
                    content=str(payload["content"]),
                    title=str(payload.get("title", "")),
                    metadata=_flatten(payload.get("metadata") or {}),
                )
            )
    return chunks


def load_plan(path: str | Path) -> CreditPlan | None:
    """读取培养方案 JSON；文件不存在返回 ``None``（服务仍可启动，核算接口报配置缺失）。"""
    file_path = Path(path)
    if not file_path.is_file():
        return None
    return CreditPlan.model_validate_json(file_path.read_text(encoding="utf-8"))
