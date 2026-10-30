import os
import sys
import json
import time
import hashlib
from datetime import datetime, timezone

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from services.step8_inspector import Step8Inspector
from services.step9_inspector import Step9Inspector
from services.step10_inspector import Step10Inspector
from services.risk_lifecycle_service import RiskLifecycleService
from services.grouped_artifact_service import GroupedArtifactService
from services.final_baseline_service import FinalBaselineService


def run_clean_pipeline_inspection():
    backend_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    logs_dir = os.path.join(backend_root, "logs")
    project_id = 123
    document_id = 261
    doc_name = "demo_EL.docx"
    prev_run_id = "inspection_run_20261029_165954"
    fresh_timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    fresh_run_id = f"inspection_run_{fresh_timestamp}"

    print(f"\n================================================================================")
    print(f"STARTING FULL FRESH PIPELINE INSPECTION RUN: {fresh_run_id}")
    print(f"Project ID: {project_id} | Document ID: {document_id} | File: {doc_name}")
    print(f"================================================================================\n")

    # Helper to load existing json
    def _load_step_art(filename: str):
        path = os.path.join(logs_dir, filename)
        if not os.path.exists(path):
            raise FileNotFoundError(f"Missing upstream artifact: {path}")
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)

    # 1. Upstream Steps 1-7
    step1_3_art = _load_step_art(f"pipeline_steps_1_3_{prev_run_id}.json")
    step4_art = _load_step_art(f"step4_inspection_p{project_id}_d{document_id}.json")
    try:
        step5_art = _load_step_art(f"step5_inspection_p{project_id}_d{document_id}.json")
    except FileNotFoundError:
        step5_art = _load_step_art(f"step5_preview_p{project_id}_d{document_id}.json")
    step6_art = _load_step_art(f"step6_traceability_p{project_id}_d{document_id}.json")
    step7_art = _load_step_art(f"step7_gap_analysis_p{project_id}_d{document_id}.json")

    print(f"[OK] Upstream Step 1-7 artifacts loaded successfully.")
    print(f"  Step 1-3 raw candidates: {step1_3_art['step3_candidate_extraction']['coverage_audit']['total_map_candidates']}")
    print(f"  Step 4 canonical records: {len(step4_art.get('structured_records', []))}")
    print(f"  Step 6 fully traced: {step6_art.get('total_canonical_candidates') or step6_art.get('canonical_candidates_count')}")
    print(f"  Step 7 findings count: {len(step7_art.get('findings', []))}")

    # 2. Execute Step 8 (Milestones & DAG Dependency Building with DeadlineInferenceService)
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
    print(f"\n[OK] Step 8 execution completed in {s8_duration:.2f}s:")
    print(f"  Total scheduled items: {step8_art['summary']['total_scheduled_items']}")
    print(f"  Estimated schedule overruns: {step8_art['summary']['estimated_schedule_overrun_count']}")
    print(f"  Contractual conflicts: {step8_art['summary']['source_confirmed_contractual_conflict_count']}")

    # 3. Execute Step 9 (Commitments, Dependencies, and Risk Analysis)
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
    print(f"\n[OK] Step 9 execution completed in {s9_duration:.2f}s:")
    print(f"  Total commitments: {step9_art['summary']['total_commitments']}")
    print(f"  Customer dependencies: {step9_art['summary']['customer_dependencies_count']}")
    print(f"  Client responsibilities: {step9_art['summary']['client_responsibilities_count']}")

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
    print(f"\n[OK] Initial Contractual Risk Assessment generated:")
    print(f"  Contractual risks: {initial_risk['metrics']['contractual_risks_count']}")
    print(f"  Customer dependencies registered: {initial_risk['metrics']['customer_dependency_register_count']}")

    # 5. Execute Step 10 (Final EL Analysis & Dashboard Synthesis)
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
    print(f"\n[OK] Step 10 execution completed in {s10_duration:.2f}s:")
    print(f"  Readiness Status: {step10_art['readiness_status']}")
    print(f"  In-Scope records: {step10_art['summary']['in_scope_records']}")
    print(f"  Work-item eligible: {step10_art['summary']['work_item_eligible_count']}")
    print(f"  Out-of-scope: {step10_art['summary']['out_of_scope_records']}")
    print(f"  Pending-review: {step10_art['summary']['pending_review_count']}")

    # 6. Grouped Artifacts Consolidation
    step_data_map = {
        1: {
            "name": "Step 1: Document Deep Scan & Text Ingestion",
            "status": "COMPLETED",
            "log_path": os.path.join(logs_dir, f"pipeline_steps_1_3_{prev_run_id}.log"),
            "json_data": step1_3_art.get("step_artifacts", {}).get("1", {}).get("artifact_content", {}),
            "source_artifact": f"pipeline_steps_1_3_{prev_run_id}.json",
            "duration": 0.5,
        },
        2: {
            "name": "Step 2: Scope Section Detection",
            "status": "COMPLETED",
            "log_path": os.path.join(logs_dir, f"pipeline_steps_1_3_{prev_run_id}.log"),
            "json_data": step1_3_art.get("step_artifacts", {}).get("2", {}).get("artifact_content", {}),
            "source_artifact": f"pipeline_steps_1_3_{prev_run_id}.json",
            "duration": 0.3,
        },
        3: {
            "name": "Step 3: Candidate Scope Extraction",
            "status": "COMPLETED",
            "log_path": os.path.join(logs_dir, f"pipeline_steps_1_3_{prev_run_id}.log"),
            "json_data": step1_3_art.get("step_artifacts", {}).get("3", {}).get("artifact_content", step1_3_art.get("step3_candidate_extraction", {})),
            "source_artifact": f"pipeline_steps_1_3_{prev_run_id}.json",
            "duration": 0.5,
        },
        4: {
            "name": "Step 4: Candidate Scope Classification & Structuring",
            "status": step4_art.get("final_verdict", "COMPLETED"),
            "log_path": os.path.join(logs_dir, f"step4_inspection_p{project_id}_d{document_id}.log"),
            "json_data": step4_art,
            "source_artifact": f"step4_inspection_p{project_id}_d{document_id}.json",
            "duration": 1.0,
        },
        5: {
            "name": "Step 5: Scope Baseline Preparation & Persistence Safety Gate",
            "status": "COMPLETED",
            "log_path": os.path.join(logs_dir, f"step5_inspection_p{project_id}_d{document_id}.log"),
            "json_data": step5_art,
            "source_artifact": f"step5_inspection_p{project_id}_d{document_id}.json",
            "duration": 0.8,
        },
        6: {
            "name": "Step 6: Traceability & Coverage Audit",
            "status": step6_art.get("baseline_status", "VALIDATED"),
            "log_path": os.path.join(logs_dir, f"step6_traceability_p{project_id}_d{document_id}.log"),
            "json_data": step6_art,
            "source_artifact": f"step6_traceability_p{project_id}_d{document_id}.json",
            "duration": 0.7,
        },
        7: {
            "name": "Step 7: Ambiguity & Gap Analysis",
            "status": step7_art.get("status", "COMPLETED"),
            "log_path": os.path.join(logs_dir, f"step7_gap_analysis_p{project_id}_d{document_id}.log"),
            "json_data": step7_art,
            "source_artifact": f"step7_gap_analysis_p{project_id}_d{document_id}.json",
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
    print(f"\n[OK] Grouped artifacts consolidated. Integrity verified: {grouped_res.get('all_files_exist_and_verified')}")

    # 7. Authoritative Consolidated Final EL Baseline
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
    print(f"\n[OK] Final baseline atomically saved and read-back verified:")
    print(f"  Artifact Path: {final_save_report['final_artifact_path']}")
    print(f"  Active Pointer: {final_save_report['active_artifact_path']}")
    print(f"  Artifact Status: {final_save_report['artifact_status']}")
    print(f"  Readiness Status: {final_save_report['readiness_status']}")
    print(f"  Work items count: {final_save_report['work_items_count']}")

    # 8. Test Loader Readback Verification
    loaded_res = FinalBaselineService.load_validated_final_baseline(
        project_id=project_id,
        document_id=document_id,
        run_id=fresh_run_id,
        base_dir=logs_dir,
    )
    assert loaded_res["status"] == "SUCCESS", f"Loader verification failed: {loaded_res}"
    loaded_art = loaded_res["artifact"]
    assert loaded_art["project_id"] == project_id
    assert loaded_art["document_id"] == document_id
    assert loaded_art["run_id"] == fresh_run_id
    print(f"[OK] Loader verification confirmed: project_id={project_id}, doc_id={document_id}, run_id={fresh_run_id}")

    # Return key facts for verification report
    return {
        "run_id": fresh_run_id,
        "timestamp": fresh_timestamp,
        "project_id": project_id,
        "document_id": document_id,
        "doc_name": doc_name,
        "summary": loaded_art["summary"],
        "final_save_report": final_save_report,
        "step8_summary": step8_art["summary"],
        "step9_summary": step9_art["summary"],
        "initial_risk_metrics": initial_risk["metrics"],
        "step10_summary": step10_art["summary"],
        "pending_review_items": loaded_art["pending_review_items"],
        "out_of_scope_items_count": len(loaded_art["out_of_scope_items"]),
        "customer_dependencies_count": len(loaded_art["customer_dependencies"]),
        "client_responsibilities_count": len(loaded_art["client_responsibilities"]),
    }

if __name__ == "__main__":
    res = run_clean_pipeline_inspection()
    print("\n================ SUMMARY OF FRESH INSPECTION RUN ================")
    print(json.dumps(res, indent=2, default=str))

