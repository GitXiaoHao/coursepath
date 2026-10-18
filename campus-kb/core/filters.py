"""Dify ``metadata_condition`` 的本地实现。

Dify 的外部知识库请求里可选携带 ``metadata_condition``，形态为::

    {"logical_operator": "and" | "or",
     "conditions": [{"name": "module", "comparison_operator": "is", "value": "专业核心课"}]}

该能力在 Dify UI 中不可配置（只能由 API 编排触发），因此必须由知识库服务端实现。
实现口径以 Dify 官方文档列出的算子为准，未列出的算子一律视为不匹配并记录告警，
避免"静默放行"导致答案引用越界。
"""

from __future__ import annotations

from typing import Any, Callable, Iterable

Condition = dict[str, Any]
ConditionGroup = dict[str, Any]

_TRUE_TOKENS = {"true", "1", "yes", "y"}


def _as_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (list, tuple, set)):
        return ",".join(_as_text(item) for item in value)
    return str(value)


def _as_number(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _cmp_text(actual: str, expected: str, op: str) -> bool:
    left, right = actual.strip().lower(), expected.strip().lower()
    if op == "is":
        return left == right
    if op == "is not":
        return left != right
    if op == "contains":
        return right in left
    if op == "not contains":
        return right not in left
    if op == "start with":
        return left.startswith(right)
    if op == "end with":
        return left.endswith(right)
    return False


def _cmp_number(actual: Any, expected: Any, op: str) -> bool:
    left, right = _as_number(actual), _as_number(expected)
    if left is None or right is None:
        return False
    if op in ("=", "is"):
        return left == right
    if op in ("≠", "!=", "is not"):
        return left != right
    if op == ">":
        return left > right
    if op == "<":
        return left < right
    if op == "≥":
        return left >= right
    if op == "≤":
        return left <= right
    return False


def _cmp_date(actual: Any, expected: Any, op: str) -> bool:
    if actual is None or expected is None:
        return False
    left, right = _as_text(actual), _as_text(expected)
    if op == "before":
        return left < right
    if op == "after":
        return left > right
    return False


#: 算子 -> 具体比较函数。键为 Dify 文档中的字面量。
_OPERATORS: dict[str, Callable[[Any, Any], bool]] = {
    "is": lambda a, e: _cmp_text(_as_text(a), _as_text(e), "is"),
    "is not": lambda a, e: _cmp_text(_as_text(a), _as_text(e), "is not"),
    "contains": lambda a, e: _cmp_text(_as_text(a), _as_text(e), "contains"),
    "not contains": lambda a, e: _cmp_text(_as_text(a), _as_text(e), "not contains"),
    "start with": lambda a, e: _cmp_text(_as_text(a), _as_text(e), "start with"),
    "end with": lambda a, e: _cmp_text(_as_text(a), _as_text(e), "end with"),
    "=": lambda a, e: _cmp_number(a, e, "="),
    "≠": lambda a, e: _cmp_number(a, e, "≠"),
    "!=": lambda a, e: _cmp_number(a, e, "!="),
    ">": lambda a, e: _cmp_number(a, e, ">"),
    "<": lambda a, e: _cmp_number(a, e, "<"),
    "≥": lambda a, e: _cmp_number(a, e, "≥"),
    "≤": lambda a, e: _cmp_number(a, e, "≤"),
    "before": lambda a, e: _cmp_date(a, e, "before"),
    "after": lambda a, e: _cmp_date(a, e, "after"),
    "in": lambda a, e: _as_text(a).strip().lower()
    in {part.strip().lower() for part in _as_text(e).split(",") if part.strip()},
    "not in": lambda a, e: _as_text(a).strip().lower()
    not in {part.strip().lower() for part in _as_text(e).split(",") if part.strip()},
}

#: 不需要比较值的算子。
_UNARY_OPERATORS: dict[str, Callable[[Any], bool]] = {
    "empty": lambda a: not _as_text(a).strip(),
    "not empty": lambda a: bool(_as_text(a).strip()),
}

SUPPORTED_OPERATORS: frozenset[str] = frozenset(_OPERATORS) | frozenset(_UNARY_OPERATORS)


def match_condition(metadata: dict[str, Any], condition: Condition) -> bool:
    """单条条件与元数据比较。未知算子/缺少字段一律返回 ``False``。"""
    name = condition.get("name")
    op = condition.get("comparison_operator") or condition.get("operator")
    if not isinstance(name, str) or not isinstance(op, str):
        return False

    present = name in metadata
    actual = metadata.get(name)

    if op in _UNARY_OPERATORS:
        return _UNARY_OPERATORS[op](actual)

    # 字段不存在时视为不匹配（Dify 语义：条件未满足即过滤掉），
    # 但 "is not" / "not contains" 这类否定算子在字段缺失时不放行，
    # 以免出现"过滤条件写错却全量返回"的越界。
    if not present:
        return False

    handler = _OPERATORS.get(op)
    if handler is None:
        return False
    return handler(actual, condition.get("value"))


def matches(metadata: dict[str, Any], group: ConditionGroup | None) -> bool:
    """按 ``logical_operator`` 组合多条条件。``None``/空组视为不过滤。"""
    if not group:
        return True
    conditions: Iterable[Condition] = group.get("conditions") or []
    conditions = list(conditions)
    if not conditions:
        return True
    operator = str(group.get("logical_operator") or "and").strip().lower()
    if operator == "or":
        return any(match_condition(metadata, cond) for cond in conditions)
    return all(match_condition(metadata, cond) for cond in conditions)
