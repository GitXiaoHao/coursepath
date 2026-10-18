"""``metadata_condition`` 算子实现单测。"""

from __future__ import annotations

import pytest

from core.filters import SUPPORTED_OPERATORS, match_condition, matches

META = {"module": "专业核心课", "page": 14, "course_code": "CS305", "active": True}


@pytest.mark.parametrize(
    "condition,expected",
    [
        ({"name": "module", "comparison_operator": "is", "value": "专业核心课"}, True),
        ({"name": "module", "comparison_operator": "is", "value": "通识教育"}, False),
        ({"name": "module", "comparison_operator": "is not", "value": "通识教育"}, True),
        ({"name": "module", "comparison_operator": "contains", "value": "核心"}, True),
        ({"name": "course_code", "comparison_operator": "start with", "value": "CS"}, True),
        ({"name": "course_code", "comparison_operator": "end with", "value": "305"}, True),
        ({"name": "page", "comparison_operator": ">", "value": 10}, True),
        ({"name": "page", "comparison_operator": "<", "value": 10}, False),
        ({"name": "page", "comparison_operator": "≥", "value": 14}, True),
        ({"name": "page", "comparison_operator": "≤", "value": 13}, False),
        ({"name": "page", "comparison_operator": "=", "value": "14"}, True),
        ({"name": "page", "comparison_operator": "≠", "value": 14}, False),
        ({"name": "course_code", "comparison_operator": "in", "value": "CS305,CS201"}, True),
        ({"name": "course_code", "comparison_operator": "not in", "value": "CS305,CS201"}, False),
        ({"name": "course_code", "comparison_operator": "empty", "value": ""}, False),
        ({"name": "active", "comparison_operator": "not empty"}, True),
    ],
)
def test_single_condition(condition: dict, expected: bool) -> None:
    assert match_condition(META, condition) is expected


def test_missing_field_never_matches() -> None:
    """字段缺失一律不匹配，避免"条件写错却全量返回"的越界。"""
    for op in ("is", "is not", "contains", "not contains"):
        assert match_condition(META, {"name": "nope", "comparison_operator": op, "value": "x"}) is False


def test_unknown_operator_is_false() -> None:
    assert match_condition(META, {"name": "module", "comparison_operator": "regex", "value": ".*"}) is False


def test_malformed_condition_is_false() -> None:
    assert match_condition(META, {"comparison_operator": "is", "value": "x"}) is False
    assert match_condition(META, {"name": "module", "value": "x"}) is False


def test_group_and_or() -> None:
    and_group = {
        "logical_operator": "and",
        "conditions": [
            {"name": "module", "comparison_operator": "is", "value": "专业核心课"},
            {"name": "page", "comparison_operator": ">", "value": 10},
        ],
    }
    or_group = {
        "logical_operator": "or",
        "conditions": [
            {"name": "module", "comparison_operator": "is", "value": "通识教育"},
            {"name": "page", "comparison_operator": ">", "value": 10},
        ],
    }
    assert matches(META, and_group) is True
    assert matches(META, or_group) is True
    assert matches(META, {**and_group, "conditions": and_group["conditions"] + [
        {"name": "course_code", "comparison_operator": "is", "value": "CS999"}
    ]}) is False


def test_empty_group_passes_through() -> None:
    assert matches(META, None) is True
    assert matches(META, {}) is True
    assert matches(META, {"conditions": []}) is True


def test_supported_operators_cover_documented_set() -> None:
    documented = {
        "contains", "not contains", "start with", "end with", "is", "is not",
        "empty", "not empty", "=", "≠", ">", "<", "≥", "≤", "before", "after",
    }
    assert documented <= SUPPORTED_OPERATORS
