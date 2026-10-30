import os
import sys
import json

with open("new_agent_2_BE/logs/step4_inspection_p126_d264.json", "r", encoding="utf-8") as f:
    s4_art = json.load(f)

records = s4_art.get("structured_records", [])

milestones = [r for r in records if r.get("record_type") == "Milestone"]
vendor_resps = [r for r in records if r.get("record_type") == "Vendor Responsibility"]
scope_items = [r for r in records if r.get("record_type") == "Scope Item"]
deliverables = [r for r in records if r.get("record_type") == "Deliverable"]

print(f"Total Milestone records ({len(milestones)}):")
for m in milestones:
    print(f"  [{m['candidate_id']}] {m['name']} | scope_type={m.get('scope_status') or m.get('scope_type')} | eligible={m.get('work_item_eligible')} | locator={m.get('source_locator')}")

print(f"\nTotal Vendor Responsibility records ({len(vendor_resps)}):")
for v in vendor_resps:
    print(f"  [{v['candidate_id']}] {v['name']} | scope_type={v.get('scope_status') or v.get('scope_type')} | eligible={v.get('work_item_eligible')} | desc={v.get('description', '')[:80]}")

print(f"\nOther Record Types Summary:")
for rtype in ["Scope Item", "Deliverable", "Acceptance Criteria", "Client Responsibility", "Governance Obligation", "Assumption", "Dependency", "Contextual Contract Record", "General Obligation"]:
    subset = [r for r in records if r.get("record_type") == rtype]
    el_cnt = sum(1 for r in subset if r.get("work_item_eligible"))
    print(f"  {rtype}: total={len(subset)}, work_item_eligible={el_cnt}")

