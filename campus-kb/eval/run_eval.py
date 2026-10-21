"""可复核的评测脚本：检索、学分核算、先修判定三组指标。

用法（在 campus-kb 目录下）::

    python -m eval.run_eval                       # 全部三组
    python -m eval.run_eval --only retrieval      # 只跑检索
    python -m eval.run_eval --report eval/report.md

设计取向：
* 基线可复现——检索只用 BM25（``NullEmbedder``），不依赖任何模型与网络，
  因此任何人 clone 后一条命令即可得到同样的数字；
* 指标口径写死在代码里，报告直接引用本脚本输出，避免"手填一个好看的数字"；
* 不通过即非零退出，便于当成门禁使用。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Iterable, Sequence

from core.graph import EvalContext, check_prerequisites
from core.index import HybridIndex
from core.parser import build_chunks, load_tables
from core.scoring import audit_credits
from core.corpus import load_plan
from core.schema import CompletedCourse, CreditPlan

EVAL_DIR = Path(__file__).resolve().parent
KB_ROOT = EVAL_DIR.parent
DEFAULT_PLAN = KB_ROOT / "data" / "samples" / "plan.json"
DEFAULT_DOC = KB_ROOT / "data" / "samples" / "md" / "plan-demo.md"


# --------------------------------------------------------------------------- 指标
def recall_at_k(ranked: Sequence[str], relevant: Iterable[str], k: int) -> float:
    labels = set(relevant)
    if not labels:
        return 0.0
    return 1.0 if any(item in labels for item in ranked[:k]) else 0.0


def reciprocal_rank(ranked: Sequence[str], relevant: Iterable[str]) -> float:
    labels = set(relevant)
    for index, item in enumerate(ranked, start=1):
        if item in labels:
            return 1.0 / index
    return 0.0


# --------------------------------------------------------------------------- 数据集
def load_cases(name: str) -> list[dict]:
    path = EVAL_DIR / f"cases_{name}.jsonl"
    cases = []
    for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = line.strip()
        if not line:
            continue
        try:
            cases.append(json.loads(line))
        except json.JSONDecodeError as exc:  # pragma: no cover - 数据损坏
            raise SystemExit(f"{path}:{lineno} 不是合法 JSON：{exc}") from exc
    return cases


def completed_list(codes: Sequence[str] | Sequence[dict], plan: CreditPlan) -> list[CompletedCourse]:
    out: list[CompletedCourse] = []
    for item in codes:
        if isinstance(item, dict):
            out.append(CompletedCourse(**item))
            continue
        course = plan.resolve(item)
        out.append(CompletedCourse(code=item, credits=course.credits if course else 0.0))
    return out


# --------------------------------------------------------------------------- 三组评测
def eval_retrieval(plan_path: Path, doc_path: Path, top_k: int = 5) -> dict:
    """检索评测，同时给出"无意图加权"与"意图加权"两组数字（消融对照）。"""
    chunks = build_chunks(load_tables(doc_path), doc_id="plan-demo-2026")
    plan = load_plan(plan_path)
    names = {course.code: course.name for course in plan.courses} if plan else {}
    # 无 embedder：纯 BM25 基线，任何人可复现
    index = HybridIndex(chunks, course_names=names)
    cases = load_cases("retrieval")
    baseline = _score_retrieval(chunks, index, cases, top_k, use_intent=False, use_names=False)
    boosted = _score_retrieval(chunks, index, cases, top_k, use_intent=True, use_names=True)
    return {
        "group": "retrieval",
        "corpus_chunks": len(chunks),
        "top_k": top_k,
        "cases": len(cases),
        "recall@1": boosted["recall@1"],
        "recall@k": boosted["recall@k"],
        "mrr": boosted["mrr"],
        "baseline": {key: baseline[key] for key in ("recall@1", "recall@k", "mrr")},
        "rows": boosted["rows"],
    }


def _score_retrieval(
    chunks, index, cases, top_k: int, use_intent: bool, use_names: bool = True
) -> dict:
    rows = []
    hits_at_1 = hits_at_k = rr_total = 0.0
    for case in cases:
        expect = case["expect"]
        # 标注口径：只比对用例里显式给出的元数据字段（严格到"行级"）
        relevant = [
            chunk.id
            for chunk in chunks
            if all(chunk.metadata.get(key) == value for key, value in expect.items())
        ]
        ranked = [hit.chunk.id for hit in index.search(case["query"], top_k=top_k, use_intent=use_intent, use_names=use_names)]
        rr = reciprocal_rank(ranked, relevant)
        hit1 = recall_at_k(ranked, relevant, 1)
        hitk = recall_at_k(ranked, relevant, top_k)
        hits_at_1 += hit1
        hits_at_k += hitk
        rr_total += rr
        rows.append(
            {
                "id": case["id"],
                "query": case["query"],
                "relevant": relevant,
                "top1": ranked[0] if ranked else None,
                "hit@1": hit1,
                "hit@k": hitk,
                "rr": round(rr, 3),
            }
        )
    total = len(cases) or 1
    return {
        "cases": len(cases),
        "recall@1": round(hits_at_1 / total, 4),
        "recall@k": round(hits_at_k / total, 4),
        "mrr": round(rr_total / total, 4),
        "rows": rows,
    }


def eval_audit(plan_path: Path) -> dict:
    plan = load_plan(plan_path)
    assert plan is not None, f"培养方案加载失败：{plan_path}"
    cases = load_cases("audit")
    rows = []
    passed = 0
    source_ok = 0
    source_total = 0
    for case in cases:
        result = audit_credits(plan, completed_list(case["completed"], plan))
        expect = case["expect"]
        errors = []
        if "remaining_credits" in expect and abs(result.remaining_credits - expect["remaining_credits"]) > 1e-6:
            errors.append(f"remaining {result.remaining_credits} != {expect['remaining_credits']}")
        for module, want in (expect.get("gaps") or {}).items():
            row = next((item for item in result.modules if item["module"] == module), None)
            if row is None:
                errors.append(f"缺少模块 {module}")
            elif abs(row["missing"] - want) > 1e-6:
                errors.append(f"{module} 缺口 {row['missing']} != {want}")
        for code in expect.get("unrecognized") or []:
            if code not in {item["code"] for item in result.unrecognized}:
                errors.append(f"未报出方案外课程 {code}")
        for row in result.explain:
            if row["rule"] == "module":
                source_total += 1
                if row.get("source"):
                    source_ok += 1
        passed += 0 if errors else 1
        rows.append({"id": case["id"], "desc": case.get("desc", ""), "ok": not errors, "errors": errors})
    total = len(cases) or 1
    return {
        "group": "audit",
        "cases": len(cases),
        "exact_match": round(passed / total, 4),
        "explain_source_rate": round(source_ok / source_total, 4) if source_total else 0.0,
        "rows": rows,
    }


def eval_prereq(plan_path: Path) -> dict:
    plan = load_plan(plan_path)
    assert plan is not None, f"培养方案加载失败：{plan_path}"
    cases = load_cases("prereq")
    rows = []
    passed = 0
    for case in cases:
        completed = completed_list(case["completed"], plan)
        audit = audit_credits(plan, completed)
        context = EvalContext(
            passed=frozenset(item.code for item in completed if item.passed),
            earned_by_module=audit.earned_by_module,
        )
        report = check_prerequisites(plan, case["course"], context)
        expect = case["expect"]
        errors: list[str] = []
        if report.ok != expect["ok"]:
            errors.append(f"ok={report.ok} != {expect['ok']}")
        if "resolved" in expect and report.target.get("resolved") != expect["resolved"]:
            errors.append(f"resolved={report.target.get('resolved')} != {expect['resolved']}")
        for code in expect.get("missing") or []:
            got = {blocker.get("course_code") for blocker in report.direct}
            if code not in got:
                errors.append(f"未列出阻塞课程 {code}")
        for kind in expect.get("kinds") or []:
            got = {blocker.get("kind") for blocker in report.direct}
            if kind not in got:
                errors.append(f"未列出阻塞类型 {kind}")
        passed += 0 if errors else 1
        rows.append(
            {
                "id": case["id"],
                "course": case["course"],
                "ok": report.ok,
                "blockers": report.direct,
                "errors": errors,
            }
        )
    total = len(cases) or 1
    return {
        "group": "prereq",
        "cases": len(cases),
        "exact_match": round(passed / total, 4),
        "rows": rows,
    }


# --------------------------------------------------------------------------- 报告
def render_markdown(results: list[dict]) -> str:
    lines = ["# 评测结果", ""]
    lines.append("| 组 | 用例数 | 指标 | 结果 |")
    lines.append("| --- | --- | --- | --- |")
    for result in results:
        if result["group"] == "retrieval":
            base = result["baseline"]
            lines.append(
                f"| 检索 | {result['cases']} | Recall@1 / Recall@{result['top_k']} / MRR（基线 → 意图加权） "
                f"| {base['recall@1']:.3f}→{result['recall@1']:.3f} / "
                f"{base['recall@k']:.3f}→{result['recall@k']:.3f} / "
                f"{base['mrr']:.3f}→{result['mrr']:.3f} |"
            )
        elif result["group"] == "audit":
            lines.append(
                f"| 学分核算 | {result['cases']} | 完全一致率 / 结论可回溯率 "
                f"| {result['exact_match']:.3f} / {result['explain_source_rate']:.3f} |"
            )
        else:
            lines.append(f"| 先修判定 | {result['cases']} | 完全一致率 | {result['exact_match']:.3f} |")
    lines.append("")
    for result in results:
        failures = [row for row in result["rows"] if not row.get("ok", True)]
        lines.append(f"## {result['group']}")
        lines.append("")
        if failures:
            for row in failures:
                lines.append(f"- ❌ `{row['id']}` {row.get('errors') or row.get('desc', '')}")
        else:
            lines.append("- 全部用例通过")
        lines.append("")
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="campus-kb 评测")
    parser.add_argument("--plan", default=str(DEFAULT_PLAN))
    parser.add_argument("--doc", default=str(DEFAULT_DOC))
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--only", choices=["retrieval", "audit", "prereq"], default=None)
    parser.add_argument("--report", default=None, help="同时写出 Markdown 报告")
    parser.add_argument("--json", default=None, help="把原始结果写成 JSON")
    args = parser.parse_args(argv)

    plan_path, doc_path = Path(args.plan), Path(args.doc)
    results: list[dict] = []
    if args.only in (None, "retrieval"):
        results.append(eval_retrieval(plan_path, doc_path, top_k=args.top_k))
    if args.only in (None, "audit"):
        results.append(eval_audit(plan_path))
    if args.only in (None, "prereq"):
        results.append(eval_prereq(plan_path))

    summary = {result["group"]: {key: value for key, value in result.items() if key != "rows"} for result in results}
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if args.report:
        Path(args.report).write_text(render_markdown(results), encoding="utf-8")
    if args.json:
        Path(args.json).write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")

    for result in results:
        if result["group"] == "retrieval" and result["recall@1"] < 0.8:
            print(f"检索 Recall@1 低于 0.8：{result['recall@1']}", file=sys.stderr)
            return 1
        if result["group"] in ("audit", "prereq") and result["exact_match"] < 1.0:
            print(f"{result['group']} 存在未通过用例", file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
