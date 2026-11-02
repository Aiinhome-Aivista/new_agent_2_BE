import sys
import os

backend_dir = os.path.dirname(os.path.abspath(__file__))
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

from fastapi.testclient import TestClient
from main import app
from api.dependencies.auth import get_current_user, require_roles, verify_project_access

print("Initializing test...", flush=True)

test_user = {"id": 1, "username": "admin", "role": "ADMIN", "email": "admin@example.com"}

app.dependency_overrides[get_current_user] = lambda: test_user
app.dependency_overrides[verify_project_access] = lambda project_id, current_user, db: True
for route in app.routes:
    if hasattr(route, "dependant"):
        for dep in route.dependant.dependencies:
            if "require_roles" in str(dep.call):
                app.dependency_overrides[dep.call] = lambda: test_user

print("Creating TestClient...", flush=True)
client = TestClient(app)
print("Client created successfully.", flush=True)

for pid in [126, 125]:
    print(f"\n==================== TESTING API FOR PROJECT {pid} ====================")
    
    # 1. Baseline endpoints
    res_b = client.get(f"/api/projects/{pid}/baseline/")
    print(f"GET /projects/{pid}/baseline/ -> Status {res_b.status_code}")
    if res_b.status_code == 200:
        data = res_b.json().get("data", {})
        print("  Baseline ID:", data.get("id"))
        print("  Status:", data.get("status"))
        print("  all_records:", len(data.get("all_records", [])))
        print("  work_items:", len(data.get("work_items", [])))
        print("  out_of_scope_records:", len(data.get("out_of_scope_records", [])))
        print("  pending_review_records:", len(data.get("pending_review_records", [])))
        print("  milestones:", len(data.get("milestones", [])))
        print("  payment_milestones:", len(data.get("payment_milestones", [])))
        print("  commitments:", len(data.get("commitments", [])))
        print("  client_responsibilities:", len(data.get("client_responsibilities", [])))
        print("  contextual_records:", len(data.get("contextual_records", [])))
        
    res_f = client.get(f"/api/projects/{pid}/baseline/final")
    print(f"\nGET /projects/{pid}/baseline/final -> Status {res_f.status_code}")
    if res_f.status_code == 200:
        f_data = res_f.json().get("data", {})
        print("  Artifact Status:", f_data.get("artifact_status"))
        print("  Readiness:", f_data.get("readiness_status"))
        print("  Summary:", f_data.get("summary"))

    # 2. Documents
    res_docs = client.get(f"/api/projects/{pid}/documents/")
    print(f"\nGET /projects/{pid}/documents/ -> Status {res_docs.status_code}")
    if res_docs.status_code == 200:
        docs = res_docs.json().get("data", [])
        print(f"  Total documents: {len(docs)}")
        for d in docs[:3]:
            print(f"    Doc {d.get('id')}: {d.get('document_name')} ({d.get('document_type')})")

    # 3. Project detail
    res_p = client.get(f"/api/projects/{pid}")
    print(f"\nGET /projects/{pid} -> Status {res_p.status_code}")
    if res_p.status_code == 200:
        p_info = res_p.json().get("data", {})
        print(f"  Project Name: {p_info.get('project_name')}")
        print(f"  Start: {p_info.get('start_date')} | End: {p_info.get('end_date')}")
        print(f"  Monitoring Status: {p_info.get('monitoring_status')}")
