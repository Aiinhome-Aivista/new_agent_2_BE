import json

json_path = r"c:\Users\ADMIN\Desktop\Agent-2\new_agent_2_BE\logs\baseline_inspection_p112_d250.json"
with open(json_path, "r", encoding="utf-8") as f:
    data = json.load(f)

candidates = data.get("step3_candidate_extraction", {}).get("canonical_candidates", [])

for name in ["Steering Committee Constitution", "Remote work during travel restrictions"]:
    matches = [c for c in candidates if c.get("name") == name]
    print(f"=== {name} ===")
    for c in matches:
        for k, v in c.items():
            if k == "merged_sources":
                print(f"  {k}: {len(v)} items")
            else:
                print(f"  {k}: {repr(v)}")
        print("-" * 50)

