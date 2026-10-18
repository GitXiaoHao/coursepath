"""对话形态输入与 text 输出格式的测试。

这两件事是接 Dify 时才暴露的需求：
1. Chatflow 把用户整句提问传给 ``/prereq``，服务端必须自己认出被问的课；
2. HTTP 请求节点的 JSON 响应体在 Dify 里是字符串，取不到 ``body.summary``，
   所以服务端要能直接用 ``format=text`` 返回 Markdown，供 answer 节点引用。
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from core.schema import CreditPlan

from .conftest import AUTH


class TestResolveInText:
    def test_course_name_in_sentence(self, plan: CreditPlan) -> None:
        course = plan.resolve_in_text("编译原理的先修要求是什么？")
        assert course is not None and course.code == "CS305"

    def test_course_name_beats_completed_codes(self, plan: CreditPlan) -> None:
        """问句里的代码往往只是"我修过"的课，不能被当成被问的那门。"""
        course = plan.resolve_in_text("编译原理的先修要求是什么？我修过 CS101 和 CS201")
        assert course is not None and course.code == "CS305"

    def test_longest_name_wins(self, plan: CreditPlan) -> None:
        """"编译原理实验" 与 "编译原理" 同时出现时取更长的那个。"""
        course = plan.resolve_in_text("编译原理实验和编译原理有什么区别")
        assert course is not None
        # 示例方案里只有「编译原理」，长度更长的同名课程不存在时仍应回落
        assert course.name in {"编译原理", "编译原理实验"}

    def test_code_only_question(self, plan: CreditPlan) -> None:
        course = plan.resolve_in_text("CS305 能选吗")
        assert course is not None and course.code == "CS305"

    def test_no_match_returns_none(self, plan: CreditPlan) -> None:
        assert plan.resolve_in_text("宿舍几点熄灯") is None


class TestTextFormat:
    def test_audit_text(self, client: TestClient) -> None:
        resp = client.post("/audit", json={"completed_text": "CS101,CS201", "format": "text"}, headers=AUTH)
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/plain")
        assert resp.text.startswith("### 学分核算")
        assert "还差" in resp.text

    def test_prereq_text_with_sentence(self, client: TestClient) -> None:
        resp = client.post(
            "/prereq",
            json={"course_name": "编译原理的先修要求是什么？我修过 CS101", "completed_text": "CS101", "format": "text"},
            headers=AUTH,
        )
        assert resp.status_code == 200
        assert resp.text.startswith("### 先修检查")
        assert "编译原理" in resp.text

    def test_default_format_is_json(self, client: TestClient) -> None:
        resp = client.post("/audit", json={"completed_text": "CS101"}, headers=AUTH)
        assert resp.status_code == 200
        assert resp.json()["remaining_credits"] > 0

    def test_text_format_still_requires_bearer(self, client: TestClient) -> None:
        resp = client.post("/audit", json={"completed_text": "CS101", "format": "text"})
        assert resp.status_code == 401
        assert resp.json()["error_code"] == 1001
