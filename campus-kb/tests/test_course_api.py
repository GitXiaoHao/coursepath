"""``/audit`` 与 ``/prereq`` 接口测试。"""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import create_app
from core.index import HybridIndex

from tests.conftest import AUTH, CHUNKS


def test_audit_returns_module_breakdown(client: TestClient) -> None:
    body = {
        "completed": [
            {"code": "GE101", "credits": 4},
            {"code": "GE102", "credits": 4},
            {"code": "ME101", "credits": 3},
            {"code": "VE201", "credits": 4},
        ]
    }
    data = client.post("/audit", json=body, headers=AUTH).json()
    rows = {row["module"]: row for row in data["modules"]}
    assert rows["通识教育"]["earned"] == 8
    assert rows["工程基础"]["earned"] == 3
    assert rows["专业核心课"]["earned"] == 4
    assert rows["专业选修课"]["earned"] == 0
    # 剩余 = (48-8)+(34-3)+(46-4)+16+16
    assert data["remaining_credits"] == 40 + 31 + 42 + 16 + 16
    assert data["earned_credits"] == 15
    assert data["total_credits"] == 160
    assert data["plan_id"] == "vehicle-engineering-demo-2025"


def test_audit_explain_is_traceable(client: TestClient) -> None:
    data = client.post("/audit", json={"completed": []}, headers=AUTH).json()
    rules = [row["rule"] for row in data["explain"]]
    assert "R1" in rules and "R4" in rules and "R5" in rules
    module_rows = [row for row in data["explain"] if row["rule"] == "module"]
    assert module_rows
    assert any(row["source"] for row in module_rows), "结论应能回指文档出处"


def test_audit_no_courses_left(client: TestClient) -> None:
    data = client.post("/audit", json={"completed": []}, headers=AUTH).json()
    assert data["remaining_credits"] == 160
    assert len(data["gaps"]) == 5


def test_audit_unknown_plan(client: TestClient) -> None:
    response = client.post("/audit", json={"plan_id": "nope", "completed": []}, headers=AUTH)
    assert response.status_code == 404
    assert response.json()["error_code"] == 1002


def test_audit_requires_auth(client: TestClient) -> None:
    assert client.post("/audit", json={"completed": []}).status_code == 401


def test_audit_without_plan_returns_503(settings) -> None:
    app = create_app(index=HybridIndex(CHUNKS), settings=settings)  # settings 指向不存在的方案
    client = TestClient(app)
    response = client.post("/audit", json={"completed": []}, headers=AUTH)
    assert response.status_code == 503
    assert response.json()["error_code"] == 2001


def test_prereq_by_code_ok(client: TestClient) -> None:
    body = {
        "course_code": "VE205",
        "completed": [
            {"code": "EE101", "credits": 4},
            {"code": "CS101", "credits": 3},
            {"code": "VE201", "credits": 4},
        ],
    }
    data = client.post("/prereq", json=body, headers=AUTH).json()
    assert data["ok"] is True
    assert data["target"]["course_code"] == "VE205"
    assert data["blockers"] == []


def test_prereq_by_name_blocked(client: TestClient) -> None:
    body = {"course_name": "汽车电子控制", "completed": [{"code": "EE101", "credits": 4}]}
    data = client.post("/prereq", json=body, headers=AUTH).json()
    assert data["ok"] is False
    assert {blocker["course_code"] for blocker in data["direct_blockers"]} == {"CS101", "VE201"}


def test_prereq_and_branch_partially_met_is_blocked(client: TestClient) -> None:
    """VE203 汽车设计：且（ME104、VE201），只满足一门仍判不满足。"""
    body = {"course_code": "VE203", "completed": [{"code": "ME104", "credits": 4}]}
    data = client.post("/prereq", json=body, headers=AUTH).json()
    assert data["ok"] is False
    assert {blocker["course_code"] for blocker in data["direct_blockers"]} == {"VE201"}


def test_prereq_credits_gate(client: TestClient) -> None:
    completed = [{"code": "VE201", "credits": 4}, {"code": "VE202", "credits": 4}]
    body = {"course_code": "PR104", "completed": completed}
    data = client.post("/prereq", json=body, headers=AUTH).json()
    assert data["ok"] is False
    assert any(blocker.get("kind") == "credits" for blocker in data["direct_blockers"])
    assert any(blocker.get("course_code") == "PR103" for blocker in data["direct_blockers"])


def test_prereq_requires_identifier(client: TestClient) -> None:
    response = client.post("/prereq", json={"completed": []}, headers=AUTH)
    assert response.status_code == 400
    assert response.json()["error_code"] == 1001


def test_prereq_requires_auth(client: TestClient) -> None:
    assert client.post("/prereq", json={"course_code": "VE205"}).status_code == 401


def test_health_reports_plan(client: TestClient) -> None:
    body = client.get("/health").json()
    assert body["plan"]["plan_id"] == "vehicle-engineering-demo-2025"
    assert body["plan"]["courses"] > 0


# --- 对话友好输入：用户在对话框直接粘课程串（工作流无法自己切分） ---


def test_audit_accepts_completed_text(client: TestClient) -> None:
    data = client.post("/audit", json={"completed_text": "GE101, GE102"}, headers=AUTH).json()
    rows = {row["module"]: row for row in data["modules"]}
    # 没报学分时按培养方案补全（R3），否则会被当成 0 学分
    assert rows["通识教育"]["earned"] == 8
    assert data["warnings"] == []


def test_audit_completed_text_accepts_cjk_separators(client: TestClient) -> None:
    data = client.post("/audit", json={"completed_text": "GE101、GE102；GE104"}, headers=AUTH).json()
    rows = {row["module"]: row for row in data["modules"]}
    assert rows["通识教育"]["earned"] == 10


def test_audit_completed_text_reports_credit_mismatch(client: TestClient) -> None:
    data = client.post("/audit", json={"completed_text": "GE101:3"}, headers=AUTH).json()
    rows = {row["module"]: row for row in data["modules"]}
    # R3：学分以培养方案登记的值为准（GE101 方案登记 4），学生上报 3 只触发告警
    assert rows["通识教育"]["earned"] == 4
    assert any("不一致" in warning for warning in data["warnings"])


def test_audit_dedupes_across_both_inputs(client: TestClient) -> None:
    body = {"completed": [{"code": "ge101", "credits": 4}], "completed_text": "GE101"}
    data = client.post("/audit", json=body, headers=AUTH).json()
    rows = {row["module"]: row for row in data["modules"]}
    assert rows["通识教育"]["earned"] == 4, "同一课程重复提交不应重复计分"


def test_audit_text_input_rejects_unknown_codes_gracefully(client: TestClient) -> None:
    data = client.post("/audit", json={"completed_text": "GE101, ???"}, headers=AUTH).json()
    assert data["remaining_credits"] < 160, "能识别的课程仍应计入"


def test_prereq_accepts_completed_text(client: TestClient) -> None:
    body = {"course_code": "VE205", "completed_text": "EE101 CS101 VE201"}
    data = client.post("/prereq", json=body, headers=AUTH).json()
    assert data["ok"] is True


def test_summaries_are_present_and_actionable(client: TestClient) -> None:
    audit = client.post("/audit", json={"completed_text": "GE101"}, headers=AUTH).json()
    assert audit["summary"].startswith("### 学分核算")
    assert "还差" in audit["summary"]

    prereq = client.post("/prereq", json={"course_code": "VE205"}, headers=AUTH).json()
    assert prereq["summary"].startswith("### 先修检查")
    assert "不满足" in prereq["summary"]
