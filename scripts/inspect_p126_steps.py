import json
import os

logs_dir = "new_agent_2_BE/logs"

def inspect_file(fname):
    p = os.path.join(logs_dir, fname)
    if not os.path.exists(p):
        print(f"File {fname} does not exist!")
        return
    with open(p, "r", encoding="utf-8") as f:
        data = json.load(f)
    print(f"\n--- {fname} ---")
    print(f"Keys: {list(data.keys())}")
    if isinstance(data, dict):
        for k in ["status", "count", "candidates_count", "total_candidates", "summary"]:
            if k in data:
                print(f"  {k}: {data[k]}")
    return data

s4 = inspect_file("step4_inspection_p126_d264.json")
s5 = inspect_file("step5_inspection_p126_d264.json")
s8 = inspect_file("step8_milestones_dag_p126_d264.json")
s9 = inspect_file("step9_recurring_p126_d264.json")

