"""把培养方案文档里的表格解析成 ``CreditPlan`` 与检索语料。

设计约束（见 docs/00-约束清单.md）：

* 只做**确定性**解析：同样的表格必然得到同样的计划，不引入 LLM 猜测；
* 每一个字段都保留可回溯出处（doc_id / page / 行号 / 原文），供答案引用；
* 解析不出来的内容进入 ``warnings`` / ``unresolved``，绝不静默丢弃。

支持两种输入：Markdown 表格（合成语料与单测用，page 记表格序号）与 PDF（真实
培养方案，需安装 pdfplumber）。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Sequence

from core.schema import Chunk, Course, CreditPlan, ModuleRequirement, PrereqExpr, SourceRef

# --------------------------------------------------------------------------- 表头识别
CODE_ALIASES = ("课程代码", "课程编号", "课程号", "编号", "代码")
NAME_ALIASES = ("课程名称", "课程名", "课程")
CREDIT_ALIASES = ("学分", "学分要求", "要求学分")
MODULE_ALIASES = ("模块", "课程模块", "模块名称", "课程类别", "类别", "课程性质")
TERM_ALIASES = ("开课学期", "学期", "开课时间", "建议修读学期")
PREREQ_ALIASES = ("先修要求", "先修课程", "先修", "预修课程", "预修要求", "选课条件")
TOTAL_KEYWORDS = ("合计", "总计", "毕业总学分", "总学分")

NO_PREREQ = {"", "无", "无要求", "暂无", "—", "-", "/", "／", "无先修要求", "无先修课程"}

_LEAD_VERBS = re.compile(
    r"^(?:并且|同时|还需|还须|需|须|应|要求|先修|先行修读|已修完|已修读|已修|修完|修读|完成|通过|学习过|学过|且)+"
)
_TRAIL_QUALIFIERS = re.compile(r"(?:考核合格|成绩合格|合格|通过|及格|修完)+$")
_ENUM_SEP = ("且", "并", "同时", "以及", "和", "、", "，", ",")
_OR_SEP = ("或", "/", "／")


# --------------------------------------------------------------------------- 数据结构
@dataclass(frozen=True)
class Table:
    """一张已抽出的表格；``page`` 对 Markdown 是表格序号，对 PDF 是真实页码。"""

    title: str
    page: int
    headers: tuple[str, ...]
    rows: tuple[tuple[str, ...], ...]
    lineno: int = 0

    def column(self, aliases: Sequence[str]) -> int | None:
        for index, header in enumerate(self.headers):
            cleaned = header.strip()
            if cleaned in aliases:
                return index
        return None

    def cell(self, row: Sequence[str], aliases: Sequence[str]) -> str:
        index = self.column(aliases)
        if index is None or index >= len(row):
            return ""
        return row[index].strip()


@dataclass
class PlanBuild:
    plan: CreditPlan
    warnings: list[str] = field(default_factory=list)
    unresolved: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "plan": self.plan.to_dict(),
            "warnings": list(self.warnings),
            "unresolved": list(self.unresolved),
        }


# --------------------------------------------------------------------------- 抽取表格
def split_markdown_tables(text: str) -> list[Table]:
    """把 Markdown 中的管道表格切成 ``Table``；标题取表格上方最近的标题行。"""
    tables: list[Table] = []
    block: list[str] = []
    block_start = 0
    title = ""

    def flush() -> None:
        nonlocal block, title
        if len(block) >= 2:
            headers = _split_row(block[0])
            body: list[tuple[str, ...]] = []
            for line in block[1:]:
                if re.fullmatch(r"\|?[\s:|-]+\|?", line.strip()):
                    continue
                cells = _split_row(line)
                if not any(cells):
                    continue
                body.append(cells)
            if headers and body:
                tables.append(
                    Table(
                        title=title or f"表 {len(tables) + 1}",
                        page=len(tables) + 1,
                        headers=headers,
                        rows=tuple(body),
                        lineno=block_start,
                    )
                )
        block = []

    for lineno, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
        if stripped.startswith("#"):
            flush()
            title = stripped.lstrip("#").strip()
            continue
        if stripped.startswith("|") and stripped.endswith("|"):
            if not block:
                block_start = lineno
            block.append(stripped)
            continue
        flush()
    flush()
    return tables


def parse_pdf_tables(path: str | Path) -> list[Table]:
    """PDF 表格抽取（真实培养方案）。缺失依赖时给出可执行的提示。"""
    try:
        import pdfplumber  # type: ignore[import-not-found]
    except ImportError as exc:  # pragma: no cover - 依赖缺失路径
        raise RuntimeError("需要 pdfplumber 才能解析 PDF：pip install pdfplumber") from exc

    tables: list[Table] = []
    with pdfplumber.open(str(path)) as pdf:
        for page_index, page in enumerate(pdf.pages, start=1):
            for table_index, raw in enumerate(page.extract_tables() or [], start=1):
                rows = [
                    tuple((cell or "").replace("\n", " ").strip() for cell in row)
                    for row in raw
                    if row and any((cell or "").strip() for cell in row)
                ]
                if len(rows) < 2:
                    continue
                tables.append(
                    Table(
                        title=f"第 {page_index} 页 表 {table_index}",
                        page=page_index,
                        headers=rows[0],
                        rows=tuple(rows[1:]),
                    )
                )
    return tables


def load_tables(path: str | Path) -> list[Table]:
    target = Path(path)
    if target.suffix.lower() == ".pdf":
        return parse_pdf_tables(target)
    return split_markdown_tables(target.read_text(encoding="utf-8"))


def _split_row(line: str) -> tuple[str, ...]:
    return tuple(cell.strip() for cell in line.strip().strip("|").split("|"))


# --------------------------------------------------------------------------- 表格分类
def classify(table: Table) -> str:
    has_prereq = table.column(PREREQ_ALIASES) is not None
    has_code = table.column(CODE_ALIASES) is not None
    has_module = table.column(MODULE_ALIASES) is not None
    has_credit = table.column(CREDIT_ALIASES) is not None
    has_name = table.column(NAME_ALIASES) is not None
    if has_prereq and (has_code or has_name):
        return "prereq"
    if has_credit and (has_code or has_name):
        return "courses"
    if has_module and has_credit:
        return "modules"
    return "unknown"


# --------------------------------------------------------------------------- 先修文本解析
@dataclass
class PrereqParse:
    expr: PrereqExpr | None
    unresolved: list[str] = field(default_factory=list)
    raw: str = ""

    @property
    def ok(self) -> bool:
        return not self.unresolved


def parse_prereq_text(
    text: str, name_to_code: dict[str, str], modules: Sequence[str] = ()
) -> PrereqParse:
    """把"且/或/学分门槛"的口语化先修描述转成 ``PrereqExpr``。

    优先级：``或`` 低于 ``且``（"A 且 B 或 C" 解析为 ``(A 且 B) or C``）。
    解析不出的原子不会伪造课程代码，而是记入 ``unresolved``；``modules`` 用于把
    "专业核心课不少于 40 学分"这类门槛绑定到已知模块名。
    """
    raw = (text or "").strip()
    if raw in NO_PREREQ:
        return PrereqParse(None, [], raw)

    normalized = _normalize(raw)
    unresolved: list[str] = []
    or_groups = _split_any(normalized, _OR_SEP)
    branches: list[PrereqExpr] = []
    for group in or_groups:
        atoms: list[PrereqExpr] = []
        for piece in _split_any(group, _ENUM_SEP):
            atom = _parse_atom(piece, name_to_code, modules, unresolved)
            if atom is not None:
                atoms.append(atom)
        if len(atoms) == 1:
            branches.append(atoms[0])
        elif len(atoms) > 1:
            branches.append(PrereqExpr(kind="and", children=atoms))
    if not branches:
        return PrereqParse(None, unresolved or [raw], raw)
    if len(branches) == 1:
        expr: PrereqExpr | None = branches[0]
    else:
        expr = PrereqExpr(kind="or", children=branches)
    return PrereqParse(expr if not unresolved else None, unresolved, raw)


def _normalize(text: str) -> str:
    for full, half in (("（", "("), ("）", ")"), ("；", "，"), ("：", " "), ("１", "1")):
        text = text.replace(full, half)
    text = re.sub(r"之一$|任选一?门?$|二者之一$|其中一门$", "", text.strip())
    return text.strip(" 。.;；")


def _split_any(text: str, separators: Sequence[str]) -> list[str]:
    pattern = "|".join(re.escape(sep) for sep in sorted(separators, key=len, reverse=True))
    return [part.strip() for part in re.split(pattern, text) if part.strip()]


def _parse_atom(
    piece: str,
    name_to_code: dict[str, str],
    modules: Sequence[str],
    unresolved: list[str],
) -> PrereqExpr | None:
    atom = piece.strip(" 。.;；,，、:：()（）")
    atom = _LEAD_VERBS.sub("", atom).strip(" 的了")
    if not atom or atom in NO_PREREQ:
        return None

    credit_match = re.search(r"(\d+(?:\.\d+)?)\s*学分", atom)
    if credit_match:
        min_credits = float(credit_match.group(1))
        module = _module_from_atom(atom, credit_match.group(1), modules)
        if module:
            return PrereqExpr(kind="credits", module=module, min_credits=min_credits)
        unresolved.append(atom)
        return None

    name = _TRAIL_QUALIFIERS.sub("", _LEAD_VERBS.sub("", atom)).strip(" 的了,，")
    if not name:
        return None
    code = _lookup_code(name, name_to_code)
    if code is not None:
        return PrereqExpr(kind="course", course_code=code)
    unresolved.append(name)
    return None


def _module_from_atom(atom: str, number: str, modules: Sequence[str] = ()) -> str:
    """优先绑定到学分结构表里的模块名，避免被"已完成/不少于"等修饰词带偏。"""
    for module in sorted(modules, key=len, reverse=True):
        if module and module in atom:
            return module
    cleaned = re.sub(rf"\s*{re.escape(number)}\s*学分", "", atom)
    cleaned = re.sub(r"\s+", "", cleaned)
    cleaned = _LEAD_VERBS.sub("", cleaned)
    cleaned = re.sub(r"^(?:已|已完成|已获得|累计|共)", "", cleaned)
    cleaned = re.sub(r"(?:已修|修满|修完|不少于|不低于|达到|至少|以上)+$", "", cleaned)
    cleaned = re.sub(r"^(?:已修|修满|修完|不少于|不低于|达到|至少|已获得|累计)", "", cleaned)
    cleaned = re.sub(r"(?:课程|模块|学分|课程学分|要求|的)+$", "", cleaned)
    return cleaned.strip(" 的")


def _lookup_code(name: str, name_to_code: dict[str, str]) -> str | None:
    if name in name_to_code:
        return name_to_code[name]
    candidates = {code for key, code in name_to_code.items() if name in key or key in name}
    if len(candidates) == 1:
        return candidates.pop()
    return None


# --------------------------------------------------------------------------- 组装计划
def build_plan(
    tables: Iterable[Table],
    doc_id: str,
    major: str,
    plan_id: str,
    grade: str = "",
    version: str = "1.0.0",
    total_credits: float | None = None,
    doc_title: str = "培养方案",
) -> PlanBuild:
    tables = list(tables)
    warnings: list[str] = []
    unresolved: list[dict] = []
    modules: list[ModuleRequirement] = []
    courses: list[Course] = []
    prereq_texts: list[tuple[str, str, SourceRef]] = []
    total = total_credits

    def source_of(table: Table, row_index: int, row: Sequence[str], quote: str) -> SourceRef:
        return SourceRef(
            doc_id=doc_id,
            title=table.title,
            page=table.page,
            table_row=row_index + 1,
            quote=quote or " ".join(row),
        )

    # 第一遍：学分结构表。先拿到模块名与总学分，先修里的"某模块不少于 N 学分"才能绑定。
    for table in tables:
        if classify(table) != "modules":
            continue
        for row_index, row in enumerate(table.rows):
            module_name = table.cell(row, MODULE_ALIASES)
            credits_text = table.cell(row, CREDIT_ALIASES)
            if not module_name or not credits_text:
                continue
            if any(keyword in module_name for keyword in TOTAL_KEYWORDS):
                parsed_total = _first_number(credits_text)
                if parsed_total is not None:
                    total = parsed_total
                continue
            credits = _first_number(credits_text)
            if credits is None:
                warnings.append(f"{table.title} 第 {row_index + 1} 行学分无法解析：{credits_text!r}")
                continue
            modules.append(
                ModuleRequirement(
                    module=module_name,
                    required_credits=credits,
                    source=source_of(table, row_index, row, f"{module_name} {credits:g} 学分"),
                )
            )
    known_modules = [module.module for module in modules]

    # 第二遍：课程表与先修要求表。
    for table in tables:
        kind = classify(table)
        if kind == "courses":
            for row_index, row in enumerate(table.rows):
                code = table.cell(row, CODE_ALIASES)
                name = table.cell(row, NAME_ALIASES)
                credits_text = table.cell(row, CREDIT_ALIASES)
                if not name or not credits_text:
                    continue
                credits = _first_number(credits_text)
                if credits is None:
                    warnings.append(f"{table.title} 第 {row_index + 1} 行学分无法解析：{credits_text!r}")
                    continue
                module = table.cell(row, MODULE_ALIASES)
                terms_text = table.cell(row, TERM_ALIASES)
                source = source_of(table, row_index, row, f"{name} {credits:g} 学分")
                courses.append(
                    Course(
                        code=code or _synthetic_code(name),
                        name=name,
                        credits=credits,
                        module=module or "未分类",
                        terms=tuple(_split_any(terms_text, ("、", "，", ",", " "))) if terms_text else (),
                        source=source,
                    )
                )
                prereq_cell = table.cell(row, PREREQ_ALIASES)
                if prereq_cell:
                    prereq_texts.append((code or name, prereq_cell, source))
        elif kind == "prereq":
            for row_index, row in enumerate(table.rows):
                code = table.cell(row, CODE_ALIASES)
                name = table.cell(row, NAME_ALIASES)
                prereq_cell = table.cell(row, PREREQ_ALIASES)
                if not prereq_cell or not (code or name):
                    continue
                prereq_texts.append((code or name, prereq_cell, source_of(table, row_index, row, prereq_cell)))

    name_to_code = {course.name: course.code for course in courses}
    for key, code in [(c.code, c.code) for c in courses]:
        name_to_code.setdefault(key, code)

    by_key: dict[str, Course] = {}
    for course in courses:
        by_key[course.code] = course
        by_key.setdefault(course.name, course)

    for identifier, text, source in prereq_texts:
        course = by_key.get(identifier)
        if course is None:
            match = _lookup_code(identifier, name_to_code)
            course = by_key.get(match) if match else None
        if course is None:
            warnings.append(f"先修要求找不到对应课程：{identifier}（{text}）")
            unresolved.append({"identifier": identifier, "text": text, "reason": "课程不存在"})
            continue
        parsed = parse_prereq_text(text, name_to_code, known_modules)
        if parsed.unresolved:
            warnings.append(f"{course.code} 先修要求未完全解析：{text}（未识别：{'、'.join(parsed.unresolved)}）")
            unresolved.append(
                {"identifier": course.code, "text": text, "reason": "未识别的先修条件", "items": parsed.unresolved}
            )
            continue
        course.prerequisites = parsed.expr

    if not modules:
        warnings.append("未找到学分结构表，模块要求为空")
    if total is None:
        total = sum(module.required_credits for module in modules)
        warnings.append(f"文档未写明毕业总学分，按模块合计取 {total:g}")
    module_names = {module.module for module in modules}
    for course in courses:
        if course.module not in module_names:
            warnings.append(f"{course.code} 的模块 {course.module!r} 不在学分结构表中")

    plan = CreditPlan(
        plan_id=plan_id,
        major=major,
        grade=grade,
        version=version,
        total_credits=float(total),
        modules=modules,
        courses=courses,
    )
    return PlanBuild(plan=plan, warnings=warnings, unresolved=unresolved)


def build_chunks(
    tables: Iterable[Table],
    doc_id: str,
    doc_title: str = "培养方案",
    doc_type: str = "培养方案",
) -> list[Chunk]:
    """把表格逐行转成自然语言分块，附 page/table_row/module/course_code 元数据。"""
    chunks: list[Chunk] = []
    for table_index, table in enumerate(tables, start=1):
        kind = classify(table)
        for row_index, row in enumerate(table.rows):
            cells = [
                f"{header}：{value}"
                for header, value in zip(table.headers, row)
                if header and value.strip()
            ]
            if not cells:
                continue
            content = f"{table.title}（第 {table.page} 页，{doc_type}）｜" + "；".join(cells)
            metadata = {
                "doc_id": doc_id,
                "doc_title": doc_title,
                "doc_type": doc_type,
                "page": table.page,
                "table_row": row_index + 1,
                "table_index": table_index,
                "table_kind": kind,
                "module": table.cell(row, MODULE_ALIASES),
                "course_code": table.cell(row, CODE_ALIASES),
            }
            chunks.append(
                Chunk(
                    id=f"{doc_id}-t{table_index}-r{row_index + 1}",
                    content=content,
                    title=table.title,
                    metadata=metadata,
                )
            )
    return chunks


def _first_number(text: str) -> float | None:
    match = re.search(r"\d+(?:\.\d+)?", (text or "").replace("．", "."))
    return float(match.group(0)) if match else None


def _synthetic_code(name: str) -> str:
    return "X" + "".join(f"{ord(char):x}" for char in name)[:6]


# --------------------------------------------------------------------------- CLI
def main(argv: Sequence[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="解析培养方案表格，输出 plan.json 与 chunks.jsonl")
    parser.add_argument("input", help="Markdown 或 PDF 培养方案")
    parser.add_argument("--doc-id", default="plan-local")
    parser.add_argument("--major", default="示范专业")
    parser.add_argument("--plan-id", default=None)
    parser.add_argument("--grade", default="")
    parser.add_argument("--version", default="1.0.0")
    parser.add_argument("--total-credits", type=float, default=None)
    parser.add_argument("--out-plan", default=None)
    parser.add_argument("--out-chunks", default=None)
    args = parser.parse_args(argv)

    tables = load_tables(args.input)
    build = build_plan(
        tables,
        doc_id=args.doc_id,
        major=args.major,
        plan_id=args.plan_id or args.doc_id,
        grade=args.grade,
        version=args.version,
        total_credits=args.total_credits,
    )
    if args.out_plan:
        Path(args.out_plan).write_text(
            json.dumps(build.plan.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8"
        )
    if args.out_chunks:
        chunks = build_chunks(tables, doc_id=args.doc_id)
        Path(args.out_chunks).write_text(
            "".join(json.dumps(chunk.to_dict(), ensure_ascii=False) + "\n" for chunk in chunks),
            encoding="utf-8",
        )
    print(
        json.dumps(
            {
                "tables": len(tables),
                "modules": len(build.plan.modules),
                "courses": len(build.plan.courses),
                "total_credits": build.plan.total_credits,
                "warnings": build.warnings,
                "unresolved": build.unresolved,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
