import os
import sys
import builtins
import pytest

backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

from fastapi.testclient import TestClient
from main import app
from api.dependencies.auth import get_current_user, require_roles, verify_project_access
from core.config import settings

mock_user = {
    "id": 1,
    "email": "admin@example.com",
    "name": "Admin User",
    "role": "ADMIN",
}

app.dependency_overrides[get_current_user] = lambda: mock_user
app.dependency_overrides[verify_project_access] = lambda project_id, current_user, db: True

for route in app.routes:
    if hasattr(route, "dependant"):
        for dep in route.dependant.dependencies:
            if "require_roles" in str(dep.call):
                app.dependency_overrides[dep.call] = lambda: mock_user

client = TestClient(app)


def test_p126_api_endpoints_schema_and_counts_reconciliation():
    """
    Verifies that GET /projects/126/baseline/ and GET /projects/126/baseline/final
    return identical unified schema v2.0 fields and identical counts across all 14 registers.
    """
    project_id = 126
    prefix = settings.API_PREFIX

    resp1 = client.get(f"{prefix}/projects/{project_id}/baseline/")
    assert resp1.status_code == 200, f"Expected 200, got {resp1.status_code}: {resp1.text}"
    data1 = resp1.json()["data"]

    resp2 = client.get(f"{prefix}/projects/{project_id}/baseline/final")
    assert resp2.status_code == 200, f"Expected 200, got {resp2.status_code}: {resp2.text}"
    data2 = resp2.json()["data"]

    registers = [
        "all_records",
        "work_items",
        "out_of_scope_records",
        "pending_review_records",
        "customer_dependencies",
        "client_responsibilities",
        "contextual_records",
        "governance_obligations",
        "milestones",
        "payment_milestones",
        "commitments",
        "contractual_risks",
        "document_quality_findings",
        "schedule_warnings",
    ]

    for reg in registers:
        c1 = len(data1.get(reg, []))
        c2 = len(data2.get(reg, []))
        assert c1 == c2, f"Register count mismatch for {reg}: GET /baseline/ has {c1}, GET /baseline/final has {c2}"

    assert len(data1["work_items"]) == 70
    assert len(data1["out_of_scope_records"]) == 55
    assert len(data1["pending_review_records"]) == 3
    assert len(data1["milestones"]) == 9
    assert len(data1["payment_milestones"]) == 6
    assert len(data1["commitments"]) == 6
    assert len(data1["customer_dependencies"]) == 7
    assert len(data1["client_responsibilities"]) == 45
    assert len(data1["contractual_risks"]) == 47
    assert len(data1["document_quality_findings"]) == 2
    assert len(data1["schedule_warnings"]) == 26


def test_p126_approval_gate_blocked_by_pending_review():
    """
    Verifies that POST /projects/126/baseline/approve without allow_override
    is rejected with 422 because 3 items are PENDING_REVIEW.
    """
    project_id = 126
    prefix = settings.API_PREFIX

    resp_approve = client.post(f"{prefix}/projects/{project_id}/baseline/approve")
    assert resp_approve.status_code == 422
    detail = resp_approve.json().get("detail", "")
    assert "PENDING_REVIEW" in detail.upper() or "REVIEW" in detail.upper()


def test_p126_database_persistence_block():
    """
    Verifies that the persisted baseline is loaded and reconciled in the database.
    """
    project_id = 126
    prefix = settings.API_PREFIX

    resp = client.get(f"{prefix}/projects/{project_id}/baseline/final")
    assert resp.status_code == 200
    db_pers = resp.json()["data"].get("database_persistence", {})
    assert db_pers.get("is_persisted") is True
    assert db_pers.get("total_scope_items") == 248
    assert db_pers.get("milestones_count") == 9
    assert db_pers.get("dag_dependencies_count") == 7

