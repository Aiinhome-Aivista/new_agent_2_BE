import os
import sys
import builtins

# Ensure backend root is on sys.path
backend_dir = os.path.dirname(os.path.abspath(__file__))
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

from fastapi.testclient import TestClient
from main import app
from api.dependencies.auth import get_current_user, require_roles, verify_project_access

# Restore unbuffered print
def log(msg=""):
    sys.stderr.write(str(msg) + "\n")
    sys.stderr.flush()

# Mock current user as ENGAGEMENT_MANAGER
mock_user = {
    "id": 1,
    "email": "admin@example.com",
    "name": "Admin User",
    "role": "ADMIN",
}

app.dependency_overrides[get_current_user] = lambda: mock_user
app.dependency_overrides[verify_project_access] = lambda project_id, current_user, db: True

# Create role-checking override
def override_require_roles(roles):
    return lambda: mock_user

# Apply role override
from fastapi import Depends
for route in app.routes:
    if hasattr(route, "dependant"):
        for dep in route.dependant.dependencies:
            if "require_roles" in str(dep.call):
                app.dependency_overrides[dep.call] = lambda: mock_user

client = TestClient(app)

from core.config import settings

def test_reconciliation():
    project_id = 126
    prefix = settings.API_PREFIX

    log("\n========================================================")
    log(f"[*] 1. Testing GET {prefix}/projects/{project_id}/baseline/")
    log("========================================================")
    resp1 = client.get(f"{prefix}/projects/{project_id}/baseline/")
    assert resp1.status_code == 200, f"Expected 200, got {resp1.status_code}: {resp1.text}"
    data1 = resp1.json()["data"]

    log("\n========================================================")
    log(f"[*] 2. Testing GET {prefix}/projects/{project_id}/baseline/final")
    log("========================================================")
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

    log("\n========================================================")
    log("[*] 3. Verifying All 14 Schema Registers & Count Reconciliation")
    log("========================================================")
    log(f"{'Register':<28} | {'GET /baseline/':<18} | {'GET /final':<18} | Match?")
    log("-" * 75)

    all_match = True
    for reg in registers:
        c1 = len(data1.get(reg, []))
        c2 = len(data2.get(reg, []))
        match = (c1 == c2)
        match_str = "[OK]" if match else f"[DIFF: {c1} vs {c2}]"
        log(f"{reg:<28} | {c1:<18} | {c2:<18} | {match_str}")
        if c1 != c2:
            all_match = False

    assert all_match, "Discrepancy detected between GET /baseline/ and GET /baseline/final registers!"

    # Specific expected counts
    assert len(data1["work_items"]) == 70, f"Expected 70 work items, got {len(data1['work_items'])}"
    assert len(data1["out_of_scope_records"]) == 55, f"Expected 55 out-of-scope, got {len(data1['out_of_scope_records'])}"
    assert len(data1["pending_review_records"]) == 3, f"Expected 3 pending-review, got {len(data1['pending_review_records'])}"
    assert len(data1["milestones"]) == 9, f"Expected 9 milestones, got {len(data1['milestones'])}"
    assert len(data1["payment_milestones"]) == 6, f"Expected 6 payment milestones, got {len(data1['payment_milestones'])}"
    assert len(data1["commitments"]) == 6, f"Expected 6 commitments, got {len(data1['commitments'])}"
    assert len(data1["customer_dependencies"]) == 7, f"Expected 7 dependencies, got {len(data1['customer_dependencies'])}"
    assert len(data1["contractual_risks"]) == 47, f"Expected 47 contractual risks, got {len(data1['contractual_risks'])}"
    assert len(data1["document_quality_findings"]) == 2, f"Expected 2 doc findings, got {len(data1['document_quality_findings'])}"
    assert len(data1["schedule_warnings"]) == 26, f"Expected 26 schedule warnings, got {len(data1['schedule_warnings'])}"

    log("\n[✓] All 14 registers perfectly reconciled across both endpoints!")

    log("\n========================================================")
    log("[*] 4. Testing Approval Gate Enforcement (No Override)")
    log("========================================================")
    resp_approve_blocked = client.post(f"{prefix}/projects/{project_id}/baseline/approve")
    log(f"Status Code: {resp_approve_blocked.status_code}")
    log(f"Response: {resp_approve_blocked.json()}")
    assert resp_approve_blocked.status_code == 422, f"Expected 422 Unprocessable Entity, got {resp_approve_blocked.status_code}"
    detail = resp_approve_blocked.json().get("detail", "")
    assert "Pending Review" in detail or "pending review" in detail or "scope items" in detail, f"Expected pending review explanation in 422 detail, got: {detail}"
    log("[✓] Approval correctly blocked due to unresolved pending review items!")

    log("\n========================================================")
    log("[*] 5. Database Persistence Block Verification")
    log("========================================================")
    db_pers = data2.get("database_persistence", {})
    log(f"Database Persistence: {db_pers}")
    assert db_pers.get("is_persisted") is True
    assert db_pers.get("total_scope_items") == 248
    assert db_pers.get("milestones_count") == 9
    assert db_pers.get("dag_dependencies_count") == 7
    log("[✓] Database persistence readback verified!")

    log("\n========================================================")
    log("[✓] ALL P126 API & RECONCILIATION CHECKS PASSED!")
    log("========================================================\n")

if __name__ == "__main__":
    test_reconciliation()