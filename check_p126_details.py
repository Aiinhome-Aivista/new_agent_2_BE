import sys
sys.path.insert(0, 'new_agent_2_BE')
from core.database import get_db_connection

conn = get_db_connection()
cur = conn.cursor(dictionary=True)

cur.execute("""
    SELECT id, name, scope_type, record_category, work_item_eligible, responsibility_owner, delivery_owner, plan_assumptions
    FROM scope_items
    WHERE baseline_id = 101 AND record_category = 'PROJECT_WORK' AND work_item_eligible = 0
    LIMIT 15
""")
print("Sample of 120 items in Project 126 with record_category=PROJECT_WORK and work_item_eligible=0:")
for r in cur.fetchall():
    print(f"ID {r['id']}: {r['name'][:45]} | resp: {r['responsibility_owner']} | deliv: {r['delivery_owner']} | plan: {r['plan_assumptions']}")

# Also let's check the step 10 artifact for document 264 (Project 126)
import json, os
art_path = os.path.join('new_agent_2_BE', 'data', 'baseline_artifacts', 'final_baseline_p126_d264.json')
if os.path.exists(art_path):
    with open(art_path, 'r', encoding='utf-8') as f:
        art = json.load(f)
    print("\nStep 10 Artifact for P126 D264:")
    print("artifact_status:", art.get("artifact_status"))
    print("summary:", art.get("summary"))
    print("work_items count:", len(art.get("work_items") or []))
    print("contextual_records count:", len(art.get("contextual_records") or []))
    print("client_responsibilities count:", len(art.get("client_responsibilities") or []))
    print("milestones count:", len(art.get("milestones") or []))
    print("payment_milestones count:", len(art.get("payment_milestones") or []))
else:
    print(f"\nArtifact not found at {art_path}")

cur.close()
conn.close()

