"""``/audit`` 与 ``/prereq`` 接口测试。"""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import create_app
from core.index import HybridIndex

from tests.conftest import AUTH, CHUNKS


def test_audit_returns_module_breakdown(client: TestClient) -> None:
    body = {
        "completed": [
            {"code": "GE101", "credits": 6},
            {"code": "GE102", "credits": 3},
            {"code": "CS101", "credits": 4},
            {"code": "CS201", "credits": 4},
        ]
    }
    data = client.post("/audit", json=body, headers=AUTH).json()
    rows = {row["module"]: row for row in data["modules"]}
    assert rows["通识教育"]["earned"] == 9
    assert rows["专业核心课"]["earned"] == 8
    assert rows["专业选修课"]["earned"] == 0
    # 剩余 = (48-9)+(56-8)+24+32
    assert data["remaining_credits"] == 39 + 48 + 24 + 32
    assert data["earned_credits"] == 17
    assert data["total_credits"] == 160
    assert data["plan_id"] == "plan-demo-2026"


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
    assert len(data["gaps"]) == 4


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
        "course_code": "CS305",
        "completed": [{"code": "CS101", "credits": 4}, {"code": "CS201", "credits": 4}, {"code": "CS202", "credits": 4}],
    }
    data = client.post("/prereq", json=body, headers=AUTH).json()
    assert data["ok"] is True
    assert data["target"]["course_code"] == "CS305"
    assert data["blockers"] == []


def test_prereq_by_name_blocked(client: TestClient) -> None:
    body = {"course_name": "编译原理", "completed": [{"code": "CS101", "credits": 4}]}
    data = client.post("/prereq", json=body, headers=AUTH).json()
    assert data["ok"] is False
    assert {blocker["course_code"] for blocker in data["direct_blockers"]} == {"CS201", "CS202"}


def test_prereq_or_branch_satisfied(client: TestClient) -> None:
    body = {"course_code": "CS303", "completed": [{"code": "CS101", "credits": 4}, {"code": "CS202", "credits": 4}]}
    data = client.post("/prereq", json=body, headers=AUTH).json()
    assert data["ok"] is True


def test_prereq_credits_gate(client: TestClient) -> None:
    completed = [{"code": "CS101", "credits": 4}, {"code": "CS201", "credits": 4}]
    body = {"course_code": "CS401", "completed": completed}
    data = client.post("/prereq", json=body, headers=AUTH).json()
    assert data["ok"] is False
    assert any(blocker.get("kind") == "credits" for blocker in data["direct_blockers"])
    assert any(blocker.get("course_code") == "CS350" for blocker in data["direct_blockers"])


def test_prereq_requires_identifier(client: TestClient) -> None:
    response = client.post("/prereq", json={"completed": []}, headers=AUTH)
    assert response.status_code == 400
    assert response.json()["error_code"] == 1001


def test_prereq_requires_auth(client: TestClient) -> None:
    assert client.post("/prereq", json={"course_code": "CS305"}).status_code == 401


def test_health_reports_plan(client: TestClient) -> None:
    body = client.get("/health").json()
    assert body["plan"]["plan_id"] == "plan-demo-2026"
    assert body["plan"]["courses"] > 0
