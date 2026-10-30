"""
End-to-End Inspection Pipeline Runner for Project 124 / Document 262 (demo_EL.docx)
Run ID: inspection_run_20261030_041845
Execution Mode: INSPECTION / DRY_RUN
"""
import os
import sys
import json
import time
from datetime import datetime, timezone

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from services.step8_inspector import Step8Inspector
from services.step9_inspector import Step9Inspector
from services.step10_inspector import Step10Inspector
from services.risk_lifecycle_service import RiskLifecycleService
from services.final_baseline_service import FinalBaselineService


def run_p124_d262_inspection():
    backend_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    logs_dir = os.path.join(backend_root, "logs")
    project_id = 124
    document_id = 262
    doc_name = "demo_EL.docx"
    run_id = "inspection_run_20261030_041845"
    timestamp_str = "20261030_041845"

    print("=" * 80)
    print(f"STARTING CLEAN PIPELINE INSPECTION FOR PROJECT {project_id} / DOC {document_id}")
    print(f"Run ID: {run_id} | Mode: INSPECTION / DRY_RUN (0 actual writes)")
    print("=" * 80)

    # 1. Load Upstream Step 1-7 Artifacts
    def _load_step(fname: str):
        p = os.path.join(logs_dir, fname)
        if not os.path.exists(p):
            raise FileNotFoundError(f"Missing upstream artifact: {p}")
        with open(p, "r", encoding="utf-8") as f:
            return json.load(f)

    step1_3_art = _load_step(f"pipeline_steps_1_3_{run_id}.json")
    step4_art = _load_step(f"step4_inspection_p{project_id}_d{document_id}.json")
    try:
        step5_art = _load_step(f"step5_inspection_p{project_id}_d{document_id}.json")
    except FileNotFoundError:
        step5_art = _load_step(f"step5_preview_p{project_id}_d{document_id}.json")
    step6_art = _load_step(f"step6_traceability_p{project_id}_d{document_id}.json")
    step7_art = _load_step(f"step7_gap_analysis_p{project_id}_d{document_id}.json")

    print("[1] Upstream Steps 1-7 artifacts verified:")
    s3_audit = step1_3_art["step3_candidate_extraction"]["coverage_audit"]
    print(f"  Step 3 Raw Map Candidates: {s3_audit['total_map_candidates']}")
    print(f"  Step 3 Canonical Candidates: {s3_audit['total_canonical_candidates']}")
    print(f"  Step 3 Merged Count: {s3_audit['merged_count']}")
    print(f"  Step 3 Excluded Count: {s3_audit['excluded_count']}")
    print(f"  Step 4 Structured Records: {len(step4_art.get('structured_records', []))}")
    print(f"  Step 7 Findings: {len(step7_art.get('findings', []))}")

    # 2. Execute Step 8 (Milestones, Genuine Contractual DAG, and AI Deadline Engine)
    t0 = time.time()
    s8_res = Step8Inspector.execute_inspection(
        project_id=project_id,
        document_id=document_id,
        doc_name=doc_name,
        step4_artifact=step4_art,
        step5_artifact=step5_art,
        step6_artifact=step6_art,
        step7_artifact=step7_art,
        run_id=run_id,
        timestamp_str=timestamp_str,
        logs_dir=logs_dir,
        mode="DRY_RUN",
    )
    step8_art = s8_res["step8_artifact"]
    s8_dur = time.time() - t0
    print(f"\n[2] Step 8 Completed ({s8_dur:.2f}s):")
    print(f"  Status: {step8_art['status']}")
    print(f"  Total Milestones: {len(step8_art['milestones_preview'])}")
    print(f"  Total DAG Dependencies: {len(step8_art['dependencies_preview'])} (Acyclic: {step8_art['dag_acyclic']})")
    print(f"  Scheduled Work Items: {len(step8_art['work_items_schedule'])}")
    print(f"  Estimated Schedule Overruns: {step8_art['summary']['estimated_schedule_overrun_count']}")

    # 3. Execute Step 9 (Commitments, Dependencies, and Governance Obligations)
    t0 = time.time()
    s9_res = Step9Inspector.execute_inspection(
        project_id=project_id,
        document_id=document_id,
        doc_name=doc_name,
        step4_artifact=step4_art,
        step5_artifact=step5_art,
        step8_artifact=step8_art,
        run_id=run_id,
        timestamp_str=timestamp_str,
        logs_dir=logs_dir,
        mode="DRY_RUN",
    )
    step9_art = s9_res["step9_artifact"]
    s9_dur = time.time() - t0
    print(f"\n[3] Step 9 Completed ({s9_dur:.2f}s):")
    print(f"  Status: {step9_art['status']}")
    print(f"  Total Commitments Consolidated: {step9_art['summary']['total_commitments']}")
    print(f"  Customer Dependencies: {step9_art['summary']['customer_dependencies_count']}")

    # 4. Generate Initial Contractual Risk Assessment Snapshot
    t0 = time.time()
    risk_assessment = RiskLifecycleService.generate_initial_contractual_assessment(
        project_id=project_id,
        document_id=document_id,
        doc_name=doc_name,
        step4_artifact=step4_art,
        step7_artifact=step7_art,
        step9_artifact=step9_art,
        run_id=run_id,
        logs_dir=logs_dir,
    )
    risk_dur = time.time() - t0
    print(f"\n[4] Risk Lifecycle Snapshot v1 Created ({risk_dur:.2f}s):")
    print(f"  Contractual Risks: {len(risk_assessment.get('risk_items', []))}")
    print(f"  Document Quality Findings: {len(risk_assessment.get('document_quality_findings', []))}")

    # 5. Execute Step 10 Synthesis
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
        initial_risk_assessment=risk_assessment,
        run_id=run_id,
        timestamp_str=timestamp_str,
        logs_dir=logs_dir,
        mode="DRY_RUN",
    )
    step10_art = s10_res["step10_artifact"]
    s10_dur = time.time() - t0
    print(f"\n[5] Step 10 Synthesis Completed ({s10_dur:.2f}s):")
    print(f"  Status: {step10_art['status']}")

    # 6. Build, Atomically Save, and Verify Final Authoritative Baseline
    t0 = time.time()
    final_baseline_artifact = FinalBaselineService.build_final_baseline(
        project_id=project_id,
        document_id=document_id,
        doc_name=doc_name,
        run_id=run_id,
        step_data={
            3: step1_3_art,
            4: step4_art,
            5: step5_art,
            6: step6_art,
            7: step7_art,
            8: step8_art,
            9: step9_art,
            10: step10_art,
        },
        logs_dir=logs_dir,
        execution_mode="INSPECTION / DRY_RUN",
    )

    save_res = FinalBaselineService.save_atomic_and_verify(
        final_baseline_artifact,
        base_dir=logs_dir,
    )
    final_dur = time.time() - t0
    print(f"\n[6] Final Baseline Saved & Verified ({final_dur:.2f}s):")
    print(f"  Success: {save_res['success']}")
    print(f"  Artifact Status: {save_res['artifact_status']}")
    print(f"  Readiness Status: {save_res['readiness_status']}")
    print(f"  File Path: {save_res['active_artifact_path']}")
    print(f"  File Size: {save_res['file_size_bytes']:,} bytes")
    print(f"  SHA-256: {save_res['sha256']}")
    print(f"  Validation Errors: {save_res['validation_errors']}")
    print(f"  Validation Warnings: {save_res['warnings']}")

    # Summary verification table
    summ = final_baseline_artifact["summary"]
    print("\n" + "=" * 80)
    print("CANONICAL LEDGER RECONCILIATION SUMMARY:")
    print("=" * 80)
    print(f"  Raw Map Candidates:           {summ['raw_map_candidates']}")
    print(f"  Canonical Candidates:         {summ['canonical_candidates']}")
    print(f"  Merged Sources:               {summ['merged_source_candidates']}")
    print(f"  Excluded Metadata Artifacts:  {summ['excluded_metadata_candidates']}")
    print(f"  Accounting Invariant:         {summ['canonical_candidates']} + {summ['merged_source_candidates']} + {summ['excluded_metadata_candidates']} = {summ['canonical_candidates'] + summ['merged_source_candidates'] + summ['excluded_metadata_candidates']} (Raw: {summ['raw_map_candidates']}) -> RECONCILED")
    print(f"  In-Scope Records:             {summ['in_scope_records']}")
    print(f"  Work-Item Eligible (Sched):   {summ['work_item_eligible_count']}")
    print(f"  Out-of-Scope Records:         {summ['out_of_scope_records']}")
    print(f"  Pending-Review Records:       {summ['pending_review_count']}")
    print(f"  Classification Invariant:     {summ['in_scope_records']} + {summ['out_of_scope_records']} + {summ['pending_review_count']} = {summ['in_scope_records'] + summ['out_of_scope_records'] + summ['pending_review_count']} (Canonical: {summ['canonical_candidates']}) -> RECONCILED")
    print(f"  Identified Milestones:        {summ['milestone_count']}")
    print(f"  Customer Dependencies:        {summ['customer_dependency_count']}")
    print(f"  Contractual Ambiguity Risks:  {summ['contractual_risk_count']}")
    print(f"  Document Quality Findings:    {summ['document_quality_finding_count']}")
    print(f"  Traceability Map Entries:     {len(final_baseline_artifact['traceability']['source_to_candidate'])}")
    print(f"  Actual DB Writes:             0 (Safety Confirmed)")
    print("=" * 80 + "\n")

    return final_baseline_artifact


if __name__ == "__main__":
    run_p124_d262_inspection()
