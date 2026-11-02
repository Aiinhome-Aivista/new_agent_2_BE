import sys
import os
import json

backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

from core.database import get_db_connection

conn = get_db_connection()
cursor = conn.cursor(dictionary=True)

# 1. Fetch from scope_items for Baseline 101
cursor.execute("""
    SELECT id, name, description, evidence_text, source_section, source_page, scope_type, record_category, work_item_eligible, plan_assumptions, is_recurring, recurrence_frequency, responsibility_owner, delivery_owner
    FROM scope_items
    WHERE baseline_id = 101
    ORDER BY id ASC
""")
all_items = cursor.fetchall()
print(f"Total scope_items for Baseline 101: {len(all_items)}")

gov_items = [it for it in all_items if it.get("record_category") == "GOVERNANCE_OBLIGATION" or it.get("category") == "GOVERNANCE"]
print(f"Total GOVERNANCE_OBLIGATION items: {len(gov_items)}")

for it in gov_items:
    pa = json.loads(it["plan_assumptions"]) if it["plan_assumptions"] else {}
    cid = pa.get("candidate_id") or pa.get("canonical_id") or f"c_{it['id']}"
    print(f"DB ID {it['id']} | Candidate: {cid} | Name: {it['name']}")
    print(f"  Category: {it['record_category']} | WorkItemEligible: {it['work_item_eligible']} | Owner: {it['responsibility_owner']}")
    print(f"  Recurrence: is_recurring={it['is_recurring']}, freq={it['recurrence_frequency']}")
    print(f"  Section: {it['source_section']} | Page: {it['source_page']}")
    print(f"  Evidence: {it['evidence_text'][:120] if it['evidence_text'] else 'None'}")
    print(f"  Plan Assumptions: {pa}")
    print("-" * 60)

# 2. Check Step 9 artifact for Document 264 / Project 126
logs_dir = os.path.join(backend_dir, "logs")
s9_path = os.path.join(logs_dir, "step9_inspection_p126_d264.json")
if not os.path.exists(s9_path):
    for f in os.listdir(logs_dir):
        if f.startswith("step9_inspection_p126_") and f.endswith(".json"):
            s9_path = os.path.join(logs_dir, f)
            break

print(f"\nStep 9 path: {s9_path} (exists: {os.path.exists(s9_path)})")
if os.path.exists(s9_path):
    with open(s9_path, "r", encoding="utf-8") as f:
        s9_data = json.load(f)
        comms = s9_data.get("commitments", [])
        print(f"Total commitments in Step 9: {len(comms)}")
        for idx, c in enumerate(comms, 1):
            print(f"  {idx}. ID: {c.get('commitment_id')} | Name: {c.get('name')}")
            print(f"     Type: {c.get('commitment_type')} | Recurrence: {c.get('recurrence')} | Owner: {c.get('owner')}")
            print(f"     Due Date: {c.get('due_date')} | Next Due Date: {c.get('next_due_date')}")
            print(f"     Locator: {c.get('source_locator')}")
            print(f"     Quote: {str(c.get('evidence_quotation'))[:120]}")

# 3. Check final baseline artifact
fb_path = os.path.join(logs_dir, "final_baseline_p126_d264.json")
print(f"\nFinal Baseline path: {fb_path} (exists: {os.path.exists(fb_path)})")
if os.path.exists(fb_path):
    with open(fb_path, "r", encoding="utf-8") as f:
        fb_data = json.load(f)
        fb_comms = fb_data.get("commitments", [])
        fb_gov = fb_data.get("governance_obligations", [])
        print(f"Final Baseline: commitments={len(fb_comms)}, governance_obligations={len(fb_gov)}")
        for idx, c in enumerate(fb_comms, 1):
            print(f"  Commitment {idx}: {c.get('name')} | Recurrence: {c.get('recurrence')} | Owner: {c.get('owner')}")

cursor.close()
conn.close()
