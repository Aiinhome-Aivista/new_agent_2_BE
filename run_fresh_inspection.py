import os
import json
import time
from datetime import datetime, timezone

from services.step8_inspector import Step8Inspector
from services.step9_inspector import Step9Inspector
from services.step10_inspector import Step10Inspector
from services.risk_lifecycle_service import RiskLifecycleService
from services.grouped_artifact_service import GroupedArtifactService


def run_fresh_inspection():
    backend_root = os.path.dirname(os.path.abspath(__file__))
    logs_dir = os.path.join(backend_root, "logs")
    project_id = 122
    document_id = 260
    doc_name = "demo_EL.docx"
    prev_run_id = "inspection_run_20261029_120950"
    fresh_timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    fresh_run_id = f"inspection_run_{fresh_timestamp}"

    print(f"Executing fresh dry-run inspection {fresh_run_id} for P{project_id}/D{document_id}...")

    # 1. Load upstream step artifacts from previous validated run
    with open(os.path.join(logs_dir, f"step4_inspection_p{project_id}_d{document_id}_{prev_run_id.replace('inspection_run_', '')}.json"), "r", encoding="utf-8") as f:
        step4_art = json.load(f)
    with open(os.path.join(logs_dir, f"step5_preview_p{project_id}_d{document_id}_{prev_run_id.replace('inspection_run_', '')}.json"), "r", encoding="utf-8") as f:
        step5_art = json.load(f)
    with open(os.path.join(logs_dir, f"step6_traceability_p{project_id}_d{document_id}_{prev_run_id.replace('inspection_run_', '')}.json"), "r", encoding="utf-8") as f:
        step6_art = json.load(f)
    with open(os.path.join(logs_dir, f"step7_gap_analysis_p{project_id}_d{document_id}_{prev_run_id.replace('inspection_run_', '')}.json"), "r", encoding="utf-8") as f:
        step7_art = json.load(f)

    # 2. Run Step 8
    t0 = time.time()
    s8_res = Step8Inspector.execute_inspection(
        project_id=project_id,
        document_id=document_id,
        doc_name=doc_name,
        step4_artifact=step4_art,
        step5_artifact=step5_art,
        step6_artifact=step6_art,
        step7_artifact=step7_art,
        run_id=fresh_run_id,
        timestamp_str=fresh_timestamp,
        logs_dir=logs_dir,
        mode="DRY_RUN",
    )
    s8_duration = time.time() - t0
    step8_art = s8_res["step8_artifact"]

    # 3. Run Step 9
    t0 = time.time()
    s9_res = Step9Inspector.execute_inspection(
        project_id=project_id,
        document_id=document_id,
        doc_name=doc_name,
        step4_artifact=step4_art,
        step5_artifact=step5_art,
        step8_artifact=step8_art,
        run_id=fresh_run_id,
        timestamp_str=fresh_timestamp,
        logs_dir=logs_dir,
        mode="DRY_RUN",
    )
    s9_duration = time.time() - t0
    step9_art = s9_res["step9_artifact"]

    # 4. Generate Initial Contractual Risk Assessment
    initial_risk = RiskLifecycleService.generate_initial_contractual_assessment(
        project_id=project_id,
        document_id=document_id,
        doc_name=doc_name,
        step4_artifact=step4_art,
        step7_artifact=step7_art,
        step9_artifact=step9_art,
        run_id=fresh_run_id,
        logs_dir=logs_dir,
    )

    # 5. Run Step 10
    t0 = time.time()
    s10_res = Step10Inspector.execute_synthesis(
        project_id=project_id,
        document_id=document_id,
        doc_name=doc_name,
        step4_artifact=step4_art,
        step5_artifact=step5_art,
        step6_artifact=step6_art,
        step7_artifact=step7_art,
        step8_artifact=step8_art,
        step9_artifact=step9_art,
        initial_risk_assessment=initial_risk,
        run_id=fresh_run_id,
        timestamp_str=fresh_timestamp,
        logs_dir=logs_dir,
        mode="DRY_RUN",
    )
    s10_duration = time.time() - t0
    step10_art = s10_res["step10_artifact"]

    # 6. Grouped Artifacts Consolidation
    step1_3_json_path = os.path.join(logs_dir, f"pipeline_steps_1_3_{prev_run_id}.json")
    step1_3_data = {}
    if os.path.exists(step1_3_json_path):
        with open(step1_3_json_path, "r", encoding="utf-8") as f:
            step1_3_data = json.load(f)

    step_data_map = {
        1: {
            "name": "Step 1: Document Deep Scan & Text Ingestion",
            "status": "COMPLETED",
            "log_path": os.path.join(logs_dir, f"pipeline_steps_1_3_{prev_run_id}.log"),
            "json_data": step1_3_data.get("step_artifacts", {}).get("1", {}).get("artifact_content", {}),
            "source_artifact": f"baseline_inspection_p{project_id}_d{document_id}.json",
            "duration": 0.5,
        },
        2: {
            "name": "Step 2: Scope Section Detection",
            "status": "COMPLETED",
            "log_path": os.path.join(logs_dir, f"pipeline_steps_1_3_{prev_run_id}.log"),
            "json_data": step1_3_data.get("step_artifacts", {}).get("2", {}).get("artifact_content", {}),
            "source_artifact": f"baseline_inspection_p{project_id}_d{document_id}.json",
            "duration": 0.3,
        },
        3: {
            "name": "Step 3: Candidate Scope Extraction",
            "status": "COMPLETED",
            "log_path": os.path.join(logs_dir, f"pipeline_steps_1_3_{prev_run_id}.log"),
            "json_data": step1_3_data.get("step_artifacts", {}).get("3", {}).get("artifact_content", {}),
            "source_artifact": f"baseline_inspection_p{project_id}_d{document_id}.json",
            "duration": 0.5,
        },
        4: {
            "name": "Step 4: Candidate Scope Classification & Structuring",
            "status": step4_art.get("final_verdict", "COMPLETED"),
            "log_path": os.path.join(logs_dir, f"step4_inspection_p{project_id}_d{document_id}_{prev_run_id.replace('inspection_run_', '')}.log"),
            "json_data": step4_art,
            "source_artifact": os.path.basename(os.path.join(logs_dir, f"step4_inspection_p{project_id}_d{document_id}_{prev_run_id.replace('inspection_run_', '')}.json")),
            "duration": 1.0,
        },
        5: {
            "name": "Step 5: Scope Baseline Preparation & Persistence Safety Gate",
            "status": "COMPLETED",
            "log_path": os.path.join(logs_dir, f"step5_inspection_p{project_id}_d{document_id}_{prev_run_id.replace('inspection_run_', '')}.log"),
            "json_data": step5_art,
            "source_artifact": os.path.basename(os.path.join(logs_dir, f"step5_preview_p{project_id}_d{document_id}_{prev_run_id.replace('inspection_run_', '')}.json")),
            "duration": 0.8,
        },
        6: {
            "name": "Step 6: Traceability & Coverage Audit",
            "status": step6_art.get("baseline_status", "VALIDATED"),
            "log_path": os.path.join(logs_dir, f"step6_traceability_p{project_id}_d{document_id}_{prev_run_id.replace('inspection_run_', '')}.log"),
            "json_data": step6_art,
            "source_artifact": os.path.basename(os.path.join(logs_dir, f"step6_traceability_p{project_id}_d{document_id}_{prev_run_id.replace('inspection_run_', '')}.json")),
            "duration": 0.7,
        },
        7: {
            "name": "Step 7: Ambiguity & Gap Analysis",
            "status": step7_art.get("status", "COMPLETED"),
            "log_path": os.path.join(logs_dir, f"step7_gap_analysis_p{project_id}_d{document_id}_{prev_run_id.replace('inspection_run_', '')}.log"),
            "json_data": step7_art,
            "source_artifact": os.path.basename(os.path.join(logs_dir, f"step7_gap_analysis_p{project_id}_d{document_id}_{prev_run_id.replace('inspection_run_', '')}.json")),
            "duration": 0.6,
        },
        8: {
            "name": "Step 8: Project Milestones & DAG Dependency Building",
            "status": step8_art.get("status", "COMPLETED"),
            "log_path": s8_res["step8_log_path"],
            "json_data": step8_art,
            "source_artifact": os.path.basename(s8_res["step8_run_json_path"]),
            "duration": s8_duration,
        },
        9: {
            "name": "Step 9: Recurring Commitments Detection & Expansion",
            "status": step9_art.get("status", "COMPLETED"),
            "log_path": s9_res["step9_log_path"],
            "json_data": step9_art,
            "source_artifact": os.path.basename(s9_res["step9_run_json_path"]),
            "duration": s9_duration,
        },
        10: {
            "name": "Step 10: Final EL Analysis and Dashboard Synthesis",
            "status": step10_art.get("status", "COMPLETED"),
            "log_path": s10_res["step10_log_path"],
            "json_data": step10_art,
            "source_artifact": os.path.basename(s10_res["step10_run_json_path"]),
            "duration": s10_duration,
        },
    }

    grouped_res = GroupedArtifactService.consolidate(
        project_id=project_id,
        document_id=document_id,
        doc_name=doc_name,
        run_id=fresh_run_id,
        timestamp_str=fresh_timestamp,
        logs_dir=logs_dir,
        step_data=step_data_map,
    )

    # 7. Authoritative Consolidated Final EL Baseline
    from services.final_baseline_service import FinalBaselineService
    final_art = FinalBaselineService.build_final_baseline(
        project_id=project_id,
        document_id=document_id,
        doc_name=doc_name,
        run_id=fresh_run_id,
        execution_mode="DRY_RUN",
        step_data=step_data_map,
        logs_dir=logs_dir,
    )
    final_save_report = FinalBaselineService.save_atomic_and_verify(final_art, base_dir=logs_dir)

    print("\n================ Fresh Inspection Run Results ================")
    print(f"Run ID: {fresh_run_id}")
    print(f"Grouped Artifacts Integrity: {grouped_res.get('all_files_exist_and_verified')}")
    print(f"Step 8 Scheduled Items: {step8_art['summary']['total_scheduled_items']} (Expected: 114)")
    print(f"Step 8 Contractual Conflicts: {step8_art['summary']['source_confirmed_contractual_conflict_count']} (Expected: 0)")
    print(f"Step 8 Estimated Overruns: {step8_art['summary']['estimated_schedule_overrun_count']}")
    print(f"Step 9 Customer Dependencies: {step9_art['summary']['customer_dependencies_count']}")
    print(f"Step 9 Client Responsibilities: {step9_art['summary']['client_responsibilities_count']}")
    print(f"Step 9 Total Commitments: {step9_art['summary']['total_commitments']}")
    print(f"Risk Snapshot Total Contractual Risks: {initial_risk['metrics']['contractual_risks_count']}")
    print(f"Risk Snapshot Customer Dependencies Register: {initial_risk['metrics']['customer_dependency_register_count']}")
    print(f"Step 10 Raw Candidates: {step10_art['summary']['raw_map_candidates']} (Expected: 279)")
    print(f"Step 10 Canonical Candidates: {step10_art['summary']['canonical_candidates']} (Expected: 246)")
    print(f"Step 10 In-Scope Records: {step10_art['summary']['in_scope_records']} (Expected: 211)")
    print(f"Step 10 Work-Item Eligible Count: {step10_art['summary']['work_item_eligible_count']} (Expected: 114)")
    print(f"Step 10 Out-of-Scope Records: {step10_art['summary']['out_of_scope_records']} (Expected: 33)")
    print(f"Step 10 Pending-Review Records: {step10_art['summary']['pending_review_count']} (Expected: 2)")
    print(f"Step 10 Actual Production Promotions: {step10_art['summary']['actual_production_promotions']} (Expected: 0)")
    print(f"Step 10 Actual DB Writes: {step10_art['summary']['actual_writes']} (Expected: 0)")
    print(f"Step 10 Readiness Status: {step10_art['readiness_status']}")
    print(f"Final Baseline Artifact Path: {final_save_report['final_artifact_path']}")
    print(f"Final Baseline Active Path: {final_save_report['active_artifact_path']}")
    print(f"Final Baseline Artifact Status: {final_save_report['artifact_status']}")
    print(f"Final Baseline Readiness Status: {final_save_report['readiness_status']}")
    print(f"Final Baseline Validation Errors: {final_save_report['validation_errors']}")
    print(f"Final Baseline Work Items: {final_save_report['work_items_count']}")
    print("==============================================================")


if __name__ == "__main__":
    run_fresh_inspection()

