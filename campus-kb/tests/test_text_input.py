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
        course = plan.resolve_in_text("汽车电子控制的先修要求是什么？")
        assert course is not None and course.code == "VE205"

    def test_course_name_beats_completed_codes(self, plan: CreditPlan) -> None:
        """问句里的代码往往只是"我修过"的课，不能被当成被问的那门。"""
        course = plan.resolve_in_text("汽车电子控制的先修要求是什么？我修过 EE101 和 CS101")
        assert course is not None and course.code == "VE205"

    def test_longest_name_wins(self, plan: CreditPlan) -> None:
        """"高等数学 II" 与 "高等数学 I" 同时出现时取更长的那个（前缀关系）。"""
        course = plan.resolve_in_text("高等数学 II 和高等数学 I 有什么区别")
        assert course is not None
        assert course.code == "GE102"

    def test_code_only_question(self, plan: CreditPlan) -> None:
        course = plan.resolve_in_text("VE205 能选吗")
        assert course is not None and course.code == "VE205"

    def test_no_match_returns_none(self, plan: CreditPlan) -> None:
        assert plan.resolve_in_text("宿舍几点熄灯") is None


class TestTextFormat:
    def test_audit_text(self, client: TestClient) -> None:
        resp = client.post("/audit", json={"completed_text": "GE101,GE102", "format": "text"}, headers=AUTH)
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/plain")
        assert resp.text.startswith("### 学分核算")
        assert "还差" in resp.text

    def test_prereq_text_with_sentence(self, client: TestClient) -> None:
        resp = client.post(
            "/prereq",
            json={"course_name": "汽车电子控制的先修要求是什么？我修过 EE101", "completed_text": "EE101", "format": "text"},
            headers=AUTH,
        )
        assert resp.status_code == 200
        assert resp.text.startswith("### 先修检查")
        assert "汽车电子控制" in resp.text

    def test_prereq_infers_explicit_completed_codes_when_field_is_omitted(self, client: TestClient) -> None:
        """工作流漏传 completed_text 时，不能把用户明确说已修的课判为未修。"""
        resp = client.post(
            "/prereq",
            json={"course_name": "我已修 EE101、CS101、VE201，汽车电子控制能选吗？"},
            headers=AUTH,
        )
        assert resp.status_code == 200
        assert resp.json()["ok"] is True

    def test_profile_remembers_explicit_courses_for_later_question(self, client: TestClient) -> None:
        profile_id = "dify-user-001"
        first = client.post(
            "/prereq",
            json={"course_name": "我已修 EE101、CS101、VE201，汽车电子控制能选吗？", "profile_id": profile_id},
            headers=AUTH,
        )
        assert first.status_code == 200 and first.json()["ok"] is True
        later = client.post(
            "/prereq",
            json={"course_name": "汽车电子控制能选吗？", "profile_id": profile_id},
            headers=AUTH,
        )
        assert later.status_code == 200 and later.json()["ok"] is True

    def test_profile_management_endpoints(self, client: TestClient) -> None:
        profile_id = "dify-user-002"
        added = client.post(f"/profiles/{profile_id}/courses", json={"completed_text": "EE101,CS101"}, headers=AUTH)
        assert added.status_code == 200 and added.json()["course_count"] == 2
        removed = client.delete(f"/profiles/{profile_id}/courses/EE101", headers=AUTH)
        assert removed.status_code == 200
        assert [course["code"] for course in removed.json()["courses"]] == ["CS101"]
        cleared = client.delete(f"/profiles/{profile_id}", headers=AUTH)
        assert cleared.status_code == 200 and cleared.json()["course_count"] == 0

    def test_profile_summary_text(self, client: TestClient) -> None:
        profile_id = "dify-user-003"
        client.post(f"/profiles/{profile_id}/courses", json={"completed_text": "ME101"}, headers=AUTH)
        response = client.post("/profile-summary", json={"profile_id": profile_id, "format": "text"}, headers=AUTH)
        assert response.status_code == 200
        assert "我的课程档案" in response.text and "ME101" in response.text

    def test_study_plan_uses_saved_profile(self, client: TestClient) -> None:
        profile_id = "dify-user-004"
        client.post(f"/profiles/{profile_id}/courses", json={"completed_text": "EE101,CS101,VE201"}, headers=AUTH)
        response = client.post("/study-plan", json={"profile_id": profile_id, "format": "json", "limit": 12}, headers=AUTH)
        assert response.status_code == 200
        assert any(course["code"] == "VE205" for course in response.json()["candidates"]), "先修已满足的 VE205 应进候选"

    def test_profile_command_requires_confirmation_before_delete(self, client: TestClient) -> None:
        profile_id = "dify-user-005"
        client.post(f"/profiles/{profile_id}/courses", json={"completed_text": "EE101,CS101"}, headers=AUTH)
        proposed = client.post("/profile-command", json={"profile_id": profile_id, "query": "删除 EE101", "format": "text"}, headers=AUTH)
        assert "确认删除 EE101" in proposed.text
        before = client.get(f"/profiles/{profile_id}", headers=AUTH).json()
        assert before["course_count"] == 2
        confirmed = client.post("/profile-command", json={"profile_id": profile_id, "query": "确认删除 EE101", "format": "text"}, headers=AUTH)
        assert confirmed.status_code == 200 and "已删除 EE101" in confirmed.text
        after = client.get(f"/profiles/{profile_id}", headers=AUTH).json()
        assert [course["code"] for course in after["courses"]] == ["CS101"]

    def test_default_format_is_json(self, client: TestClient) -> None:
        resp = client.post("/audit", json={"completed_text": "GE101"}, headers=AUTH)
        assert resp.status_code == 200
        assert resp.json()["remaining_credits"] > 0

    def test_text_format_still_requires_bearer(self, client: TestClient) -> None:
        resp = client.post("/audit", json={"completed_text": "GE101", "format": "text"})
        assert resp.status_code == 401
        assert resp.json()["error_code"] == 1001

def test_resolve_in_text_prefers_the_course_asked_about(plan) -> None:
    """问句里点了两门课时，取**先出现**的那门——前面是被问的，后面是"我修过"的。"""
    course = plan.resolve_in_text("汽车电子控制能选吗？我修过电工电子技术、程序设计基础")
    assert course is not None
    assert course.code == "VE205"


def test_resolve_in_text_prefers_longer_name_at_same_position(plan) -> None:
    course = plan.resolve_in_text("汽车车身设计的先修要求")
    assert course is not None
    assert course.code == "VE214"
