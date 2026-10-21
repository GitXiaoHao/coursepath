from __future__ import annotations

from core.plan_catalog import build_plan_catalog_chunks


def test_catalog_covers_every_module_and_course_with_source(plan):
    chunks = build_plan_catalog_chunks(plan)

    assert len(chunks) == len(plan.modules) + len(plan.courses)
    ve205 = next(chunk for chunk in chunks if chunk.metadata.get("course_code") == "VE205")
    assert ve205.metadata["page"] == 8
    assert ve205.metadata["table_row"] == 6
    assert "电工电子技术（EE101）" in ve205.content
    assert "程序设计基础（CS101）" in ve205.content
