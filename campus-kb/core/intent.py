"""查询意图的确定性识别。

同一份培养方案里，"学分""先修""学期"三类问题应命中不同表格。向量检索对
"汽车设计几个学分"这种问法会把「先修要求表」里同名课程的行排到前面——
它确实含这个名字，但答不了学分。这里用可枚举的关键词规则判断意图，只对
**表类型**做轻微加权（1.15 倍），既提升准确率又不改变召回集合。

规则是显式的、可单测的；没有模型、没有网络，任何时候跑都是同一个结果。
"""

from __future__ import annotations

#: 意图 -> 触发词。刻意保持窄口径：宁可判不出意图（不加权），也不要误伤排序。
KIND_KEYWORDS: dict[str, tuple[str, ...]] = {
    "prereq": ("先修", "预修", "前置", "选课条件", "能不能选", "能否选", "可以选", "选课要求"),
    "credits": ("学分", "多少分", "几分", "分值"),
    "term": ("学期", "什么时候", "何时", "开课时间", "几年级", "大几", "修读时间"),
    "module": ("模块", "环节", "包括", "包含", "构成", "组成", "有哪些"),
    "clause": ("转专业", "学分认定", "免修", "重修", "替代", "抵", "条例", "规定", "政策"),
}

#: 意图 -> 对应的表格类型（解析器写在 ``metadata["table_kind"]`` 里的取值）。
KIND_TO_TABLE: dict[str, str] = {
    "prereq": "prereq",
    "credits": "courses",
    "term": "courses",
    "module": "modules",
    "clause": "clause",
}

#: 加权系数。大于 1 但接近 1：只用来打破"同名课程出现在两张表"的近似平局。
INTENT_BOOST = 1.15


def detect_kinds(query: str) -> tuple[str, ...]:
    """返回查询命中的意图标签（可能多个，例如"专业核心课模块多少学分"）。"""
    text = query or ""
    return tuple(kind for kind, words in KIND_KEYWORDS.items() if any(word in text for word in words))


def preferred_tables(query: str) -> frozenset[str]:
    """查询应优先的表类型集合；空集表示不做加权。"""
    return frozenset(KIND_TO_TABLE[kind] for kind in detect_kinds(query))
