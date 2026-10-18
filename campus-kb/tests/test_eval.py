"""评测脚本自身的测试：指标算法 + 真实样例数据全通过（否则不许提交）。"""

from __future__ import annotations

from pathlib import Path

import pytest

from eval.run_eval import (
    DEFAULT_DOC,
    DEFAULT_PLAN,
    eval_audit,
    eval_prereq,
    eval_retrieval,
    load_cases,
    recall_at_k,
    reciprocal_rank,
)


def test_recall_at_k() -> None:
    assert recall_at_k(["a", "b"], {"b"}, k=1) == 0.0
    assert recall_at_k(["a", "b"], {"b"}, k=2) == 1.0
    assert recall_at_k([], {"a"}, k=5) == 0.0
    assert recall_at_k(["a"], set(), k=5) == 0.0


def test_reciprocal_rank() -> None:
    assert reciprocal_rank(["a", "b"], {"b"}) == 0.5
    assert reciprocal_rank(["b", "a"], {"b"}) == 1.0
    assert reciprocal_rank(["a"], {"b"}) == 0.0


def test_cases_are_well_formed() -> None:
    for name in ("retrieval", "audit", "prereq"):
        cases = load_cases(name)
        assert cases, f"{name} 用例为空"
        assert len({case["id"] for case in cases}) == len(cases), f"{name} 用例 id 重复"


def test_retrieval_beats_baseline() -> None:
    result = eval_retrieval(DEFAULT_PLAN, DEFAULT_DOC, top_k=5)
    assert result["recall@k"] == 1.0
    assert result["recall@1"] >= result["baseline"]["recall@1"]
    assert result["mrr"] >= result["baseline"]["mrr"]


def test_audit_golden_cases_all_pass() -> None:
    result = eval_audit(DEFAULT_PLAN)
    assert result["exact_match"] == 1.0
    assert result["explain_source_rate"] == 1.0


def test_prereq_golden_cases_all_pass() -> None:
    result = eval_prereq(DEFAULT_PLAN)
    assert result["exact_match"] == 1.0


def test_eval_cli_exit_code_is_a_gate() -> None:
    from eval.run_eval import main

    assert main(["--only", "audit"]) == 0
    assert main(["--only", "prereq"]) == 0


def test_eval_reports_missing_doc(tmp_path: Path) -> None:
    with pytest.raises(Exception):
        eval_retrieval(DEFAULT_PLAN, tmp_path / "nope.md")
