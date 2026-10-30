import os
import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import json
from services.step9_inspector import Step9Inspector

def main():
    s4 = json.load(open("logs/step4_inspection_p123_d261.json", encoding="utf-8"))
    # In step5_preview_p123_d261.json if it was empty, construct valid step5 dict or check
    try:
        s5 = json.load(open("logs/step5_inspection_p123_d261.json", encoding="utf-8"))
    except Exception:
        s5 = {"validation_gate_passed": True, "proposed_scope_items_preview": s4.get("structured_records", [])}
    s8 = json.load(open("logs/step8_milestones_dag_p123_d261.json", encoding="utf-8"))

    res = Step9Inspector.execute_inspection(
        project_id=123,
        document_id=261,
        doc_name="demo_EL.docx",
        step4_artifact=s4,
        step5_artifact=s5,
        step8_artifact=s8,
        run_id="test_run_step9_eval",
        timestamp_str="20261030_023000",
        logs_dir="logs",
        mode="DRY_RUN",
    )
    art = res["step9_artifact"]
    print("Step 9 summary:", art["summary"])
    deps = art["customer_dependencies"]
    crs = art["client_responsibilities"]
    print(f"Customer dependencies count: {len(deps)}")
    print(f"Client responsibilities count: {len(crs)}")
    print("\n--- Extracted Customer Dependencies ---")
    for d in deps:
        print(f"{d['candidate_id']} | {d['name']}")
    print("\n--- Extracted Client Responsibilities (sample first 10) ---")
    for c in crs[:10]:
        print(f"{c['candidate_id']} | {c['name']}")

if __name__ == "__main__":
    main()
