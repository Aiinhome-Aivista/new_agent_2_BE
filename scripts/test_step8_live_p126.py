import os
import sys
import json

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.step8_inspector import Step8Inspector
from services.step9_inspector import Step9Inspector

logs_dir = "new_agent_2_BE/logs"
with open(os.path.join(logs_dir, "step4_inspection_p126_d264.json"), "r", encoding="utf-8") as f:
    s4_art = json.load(f)

with open(os.path.join(logs_dir, "step5_inspection_p126_d264.json"), "r", encoding="utf-8") as f:
    s5_art = json.load(f)

with open(os.path.join(logs_dir, "step6_traceability_p126_d264.json"), "r", encoding="utf-8") as f:
    s6_art = json.load(f)

with open(os.path.join(logs_dir, "step7_gap_analysis_p126_d264.json"), "r", encoding="utf-8") as f:
    s7_art = json.load(f)

# Override verdict to PASSED_WITH_WARNINGS to test unblocked Step 8
s4_copy = dict(s4_art)
s4_copy["final_verdict"] = "STEP_4_VALIDATION_PASSED_WITH_WARNINGS"

s5_copy = dict(s5_art)
s5_copy["validation_gate_passed"] = True

res8 = Step8Inspector.execute_inspection(
    project_id=126,
    document_id=264,
    doc_name="demo_EL.docx",
    step4_artifact=s4_copy,
    step5_artifact=s5_copy,
    step6_artifact=s6_art,
    step7_artifact=s7_art,
    run_id="test_run_unblocked",
    timestamp_str="test_ts",
    logs_dir=logs_dir,
    mode="DRY_RUN",
)

s8_art = res8["step8_artifact"]
print("--- Unblocked Step 8 Test Results ---")
print("Status:", s8_art.get("status"))
print("Summary:", s8_art.get("summary"))
print("Milestones count:", len(s8_art.get("milestones_preview", [])))
print("Dependencies count:", len(s8_art.get("dependencies_preview", [])))
print("Work items schedule count:", len(s8_art.get("work_items_schedule", [])))
print("Schedule conflicts count:", len(s8_art.get("schedule_conflicts", [])))
if s8_art.get("work_items_schedule"):
    print("Sample work item:", s8_art["work_items_schedule"][0])

