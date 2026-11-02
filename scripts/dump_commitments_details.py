import json

with open('new_agent_2_BE/logs/final_baselines/baseline_final_p126_d264.json', 'r', encoding='utf-8') as f:
    data = json.load(f)

print("=" * 80)
print("=== 6 COMMITMENTS DETAILS ===")
print("=" * 80)
for idx, c in enumerate(data.get('commitments', []), 1):
    cid = c.get("commitment_id") or c.get("id")
    print(f"Commitment {idx}: {c.get('name')}")
    print(f"  ID: {cid}")
    print(f"  Owner: {c.get('owner')} | Type: {c.get('commitment_type')} | Recurrence: {c.get('recurrence')}")
    print(f"  Due Date: {c.get('due_date')} | Next Due: {c.get('next_due_date')}")
    print(f"  Locator: {c.get('source_locator')}")
    print(f"  Evidence: {str(c.get('evidence_quotation') or c.get('evidence'))[:160]}")
    print("-" * 60)

print("\n" + "=" * 80)
print("=== 19 GOVERNANCE OBLIGATIONS DETAILS ===")
print("=" * 80)
for idx, g in enumerate(data.get('governance_obligations', []), 1):
    print(f"Gov {idx}: {g.get('name')}")
    print(f"  Category: {g.get('category')} | Recurrence: {g.get('recurrence')}")
    print(f"  Locator: {g.get('source_locator')}")
    print(f"  Evidence: {str(g.get('evidence_text') or g.get('evidence'))[:160]}")
    print("-" * 60)

