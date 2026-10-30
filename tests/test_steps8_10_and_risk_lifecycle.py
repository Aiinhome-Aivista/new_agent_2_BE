import os
import json
import tempfile
import pytest

from services.step8_inspector import Step8Inspector
from services.step9_inspector import Step9Inspector
from services.step10_inspector import Step10Inspector
from services.grouped_artifact_service import GroupedArtifactService
from services.risk_lifecycle_service import RiskLifecycleService


@pytest.fixture
def sample_pipeline_data():
    step4_artifact = {
        "final_verdict": "PASSED",
        "verified_scope_items": [
            {
                "id": 1,
                "title": "Cloud Infrastructure Setup",
                "milestone": "Phase 1 - Foundation",
                "deadline": "2026-03-31",
                "classification": "IN_SCOPE",
                "complexity": "HIGH",
            },
            {
                "id": 2,
                "title": "Data Migration Pipeline",
                "milestone": "Phase 2 - Migration",
                "deadline": None,
                "classification": "IN_SCOPE",
                "complexity": "MEDIUM",
            },
        ],
        "excluded_scope_items": [
            {
                "id": 101,
                "item": "Legacy Hardware Maintenance",
                "rationale": "Hardware support is excluded per section 4.2",
                "boundary_type": "EXCLUSION",
            }
        ],
        "structured_records": [
            {
                "candidate_id": "CD_001",
                "record_type": "CUSTOMER_PREREQUISITE",
                "name": "Client AWS Account Provisioning",
                "description": "Client must provide root AWS account credentials.",
                "source_locator": "section_3.1",
            }
        ],
        "atomic_claim_validations": [
            {
                "candidate_id": "C_001",
                "claim_text": "Vendor shall maintain 99.99% uptime during alpha.",
                "status": "UNSUPPORTED",
                "reason": "Not found in SLA appendix.",
            }
        ],
    }

    step5_artifact = {
        "validation_gate_passed": True,
        "persisted_scope_items_count": 2,
        "mode": "DRY_RUN",
    }

    step6_artifact = {
        "status": "PASSED",
        "milestones_count": 2,
    }

    step7_artifact = {
        "milestone_rows": [
            {
                "name": "Phase 1 - Foundation",
                "target_date": "2026-03-31",
                "date_provenance": "CONTRACT_CONFIRMED",
                "deliverables": ["Cloud Infrastructure Setup"],
            },
            {
                "name": "Phase 2 - Migration",
                "target_date": "2026-06-30",
                "date_provenance": "AI_ESTIMATED",
                "deliverables": ["Data Migration Pipeline"],
            },
        ],
        "findings": [
            {
                "finding_id": "F_001",
                "title": "Ambiguous Acceptance Period for Migration",
                "category": "CONTRACTUAL",
                "severity": "HIGH",
                "recommended_disposition": "Define explicit 14-day UAT review window.",
            }
        ],
    }

    step8_artifact = {
        "work_items_schedule": [
            {
                "item_id": 1,
                "title": "Cloud Infrastructure Setup",
                "estimated_start_date": "2026-03-01",
                "estimated_deadline": "2026-03-31",
                "date_provenance": "CONTRACT_CONFIRMED",
                "estimation_confidence": "HIGH",
                "duration_business_days": 20,
            },
            {
                "item_id": 2,
                "title": "Data Migration Pipeline",
                "estimated_start_date": "2026-04-01",
                "estimated_deadline": "2026-04-30",
                "date_provenance": "AI_ESTIMATED",
                "estimation_confidence": "MEDIUM",
                "duration_business_days": 10,
            },
        ],
        "schedule_conflicts": [],
    }

    step9_artifact = {
        "consolidated_commitments": [
            {
                "title": "Weekly Status Report",
                "frequency": "WEEKLY",
                "commitment_type": "RECURRING",
            },
            {
                "title": "Architecture Blueprint Sign-off",
                "commitment_type": "ONE_TIME",
            },
            {
                "title": "Critical Incident Escalation",
                "commitment_type": "TRIGGER_BASED",
            },
        ],
        "customer_dependencies": [
            {
                "id": 1,
                "dependency": "Client AWS Account Provisioning",
                "prerequisite_for": "Cloud Infrastructure Setup",
                "owner": "Customer IT Director",
            }
        ],
        "vendor_out_of_scope_exclusions": [
            {
                "item": "Legacy Hardware Maintenance",
                "rationale": "Hardware support is excluded per section 4.2",
            }
        ],
    }

    return {
        "step4": step4_artifact,
        "step5": step5_artifact,
        "step6": step6_artifact,
        "step7": step7_artifact,
        "step8": step8_artifact,
        "step9": step9_artifact,
    }


def test_step8_schedule_prediction_and_provenance(sample_pipeline_data):
    """Verify Step 8 predicts work-item schedules and tags provenance accurately."""
    with tempfile.TemporaryDirectory() as tmpdir:
        res = Step8Inspector.execute_inspection(
            project_id=1,
            document_id=10,
            doc_name="Test_EL.docx",
            step4_artifact=sample_pipeline_data["step4"],
            step5_artifact=sample_pipeline_data["step5"],
            step6_artifact=sample_pipeline_data["step6"],
            step7_artifact=sample_pipeline_data["step7"],
            run_id="run_test_step8",
            timestamp_str="20261029_120000",
            logs_dir=tmpdir,
            mode="DRY_RUN",
        )

        assert res["status"] in ["PASSED", "WARNING", "SUCCESS", "PASSED_WITH_WARNINGS"]
        step8_art = res.get("step8_artifact", {})
        schedule = step8_art.get("work_items_schedule", [])
        assert len(schedule) >= 1

        for item in schedule:
            assert item.get("date_provenance") in [
                "CONTRACT_CONFIRMED",
                "AI_ESTIMATED",
                "USER_PROPOSED",
                "UNRESOLVED",
            ]
            assert "estimated_deadline" in item
            assert "estimation_confidence" in item
            assert item.get("estimation_confidence") in ["HIGH", "MEDIUM", "LOW"]


def test_step9_commitments_and_customer_dependencies(sample_pipeline_data):
    """Verify Step 9 distinguishes customer responsibilities from exclusions and consolidates commitments."""
    with tempfile.TemporaryDirectory() as tmpdir:
        res = Step9Inspector.execute_inspection(
            project_id=1,
            document_id=10,
            doc_name="Test_EL.docx",
            step4_artifact=sample_pipeline_data["step4"],
            step5_artifact=sample_pipeline_data["step5"],
            step8_artifact=sample_pipeline_data["step8"],
            run_id="run_test_step9",
            timestamp_str="20261029_120000",
            logs_dir=tmpdir,
            mode="DRY_RUN",
        )

        step9_art = res.get("step9_artifact", {})
        assert "consolidated_commitments" in step9_art
        assert "customer_dependencies" in step9_art
        assert "vendor_out_of_scope_exclusions" in step9_art

        summary = step9_art.get("summary", {})
        assert summary.get("actual_writes") == 0


def test_step10_synthesis_and_readiness_status(sample_pipeline_data):
    """Verify Step 10 produces full 10-section synthesis and assigns a readiness status."""
    with tempfile.TemporaryDirectory() as tmpdir:
        initial_risk = RiskLifecycleService.generate_initial_contractual_assessment(
            project_id=1,
            document_id=10,
            doc_name="Test_EL.docx",
            step4_artifact=sample_pipeline_data["step4"],
            step7_artifact=sample_pipeline_data["step7"],
            step9_artifact=sample_pipeline_data["step9"],
            run_id="run_test_step10",
            logs_dir=tmpdir,
        )

        res = Step10Inspector.execute_synthesis(
            project_id=1,
            document_id=10,
            doc_name="Test_EL.docx",
            step4_artifact=sample_pipeline_data["step4"],
            step5_artifact=sample_pipeline_data["step5"],
            step6_artifact=sample_pipeline_data["step6"],
            step7_artifact=sample_pipeline_data["step7"],
            step8_artifact=sample_pipeline_data["step8"],
            step9_artifact=sample_pipeline_data["step9"],
            initial_risk_assessment=initial_risk,
            run_id="run_test_step10",
            timestamp_str="20261029_120000",
            logs_dir=tmpdir,
            mode="DRY_RUN",
        )

        step10_art = res.get("step10_artifact", {})
        payload = step10_art.get("dashboard_payload", {})
        assert "executive_summary" in payload
        assert "in_scope_items" in payload
        assert "out_of_scope_items" in payload
        assert "customer_dependencies" in payload
        assert "timeline" in payload
        assert "commitments" in payload
        assert "risks_and_ambiguities" in payload
        assert "assumptions_and_configuration" in payload
        assert "traceability_matrix" in payload
        assert "readiness_status" in payload
        assert payload["readiness_status"] in [
            "FULLY_VALIDATED",
            "PROVISIONAL",
            "BLOCKED",
            "PENDING_REVIEW",
        ]


def test_grouped_artifacts_consolidation():
    """Verify 3 grouped logs and 3 grouped JSON files are generated with Group 3 covering Steps 7-10."""
    with tempfile.TemporaryDirectory() as tmpdir:
        run_id = "test_run_123"
        step_data = {}
        for s in range(1, 11):
            log_p = os.path.join(tmpdir, f"step_{s}.log")
            with open(log_p, "w") as f:
                f.write(f"Log content for step {s}\n")
            step_data[s] = {
                "name": f"Step {s}",
                "status": "COMPLETED",
                "log_path": log_p,
                "json_data": {"step": s, "status": "OK"},
                "duration": 0.5,
            }

        artifacts = GroupedArtifactService.consolidate(
            project_id=1,
            document_id=10,
            doc_name="Test_EL.docx",
            run_id=run_id,
            timestamp_str="20261029_120000",
            logs_dir=tmpdir,
            step_data=step_data,
        )

        grouped = artifacts.get("grouped_artifacts", artifacts)
        assert "group1" in grouped
        assert "group2" in grouped
        assert "group3" in grouped

        # Group 3 bundle check
        g3 = grouped["group3"]
        assert os.path.exists(g3["log_path"])
        assert os.path.exists(g3["json_path"])
        assert f"pipeline_steps_7_10_{run_id}" in g3["log_path"]
        assert f"pipeline_steps_7_10_{run_id}" in g3["json_path"]
        assert g3["json_sha256"] != ""
        assert g3["log_sha256"] != ""


def test_risk_lifecycle_phase1_el_upload_contractual_assessment(sample_pipeline_data):
    """Phase 1: EL Upload generates INITIAL_CONTRACTUAL_ASSESSMENT without speculative progress calculation."""
    with tempfile.TemporaryDirectory() as tmpdir:
        assessment = RiskLifecycleService.generate_initial_contractual_assessment(
            project_id=100,
            document_id=1,
            doc_name="Engagement_Letter.pdf",
            step4_artifact=sample_pipeline_data["step4"],
            step7_artifact=sample_pipeline_data["step7"],
            step9_artifact=sample_pipeline_data["step9"],
            run_id="run_el_upload",
            logs_dir=tmpdir,
        )

        assert assessment["assessment_type"] == "INITIAL_CONTRACTUAL_ASSESSMENT"
        assert assessment["document_type"] == "EL"
        assert len(assessment["risk_items"]) > 0

        # Verify zero speculative operational execution progress calculation
        assert assessment["operational_execution_risk_calculated"] is False
        for risk in assessment["risk_items"]:
            assert risk["lifecycle_status"] == "NEW"
            assert risk["category"] in [
                "UNCLEAR_DEADLINE",
                "MISSING_ACCEPTANCE_CRITERIA",
                "DEPENDENCY",
                "SCOPE",
                "CONTRACTUAL",
                "AMBIGUOUS_COMMITMENT",
            ]


def test_risk_lifecycle_phase2_first_report_operational_assessment(sample_pipeline_data):
    """Phase 2: First MoM/Report triggers FIRST_OPERATIONAL_ASSESSMENT combining baseline + reports."""
    with tempfile.TemporaryDirectory() as tmpdir:
        # Step 1: Baseline EL assessment
        RiskLifecycleService.generate_initial_contractual_assessment(
            project_id=101,
            document_id=1,
            doc_name="Engagement_Letter.pdf",
            step4_artifact=sample_pipeline_data["step4"],
            step7_artifact=sample_pipeline_data["step7"],
            step9_artifact=sample_pipeline_data["step9"],
            run_id="run_el_upload",
            logs_dir=tmpdir,
        )

        # Step 2: First report processed
        report_extractions = {
            "activities": [
                {
                    "activity_name": "Cloud Infrastructure Setup",
                    "status": "BLOCKED",
                }
            ],
            "blockers": [
                {
                    "text": "Waiting on client AWS account access credentials.",
                    "status": "BLOCKED",
                }
            ],
        }

        first_op_assessment = RiskLifecycleService.process_report_risk_assessment(
            project_id=101,
            document_id=2,
            doc_name="MoM_Sprint_1.docx",
            doc_type="MOM",
            report_text="Discussed cloud setup blocker: client credentials still pending.",
            extractions=report_extractions,
            logs_dir=tmpdir,
        )

        assert first_op_assessment["assessment_type"] == "FIRST_OPERATIONAL_ASSESSMENT"
        assert first_op_assessment["triggering_upload"]["document_id"] == 2
        assert first_op_assessment["operational_execution_risk_calculated"] is True

        metrics = first_op_assessment.get("metrics", {})
        assert metrics.get("total_risks", 0) > 0


def test_risk_lifecycle_phase3_subsequent_report_recalculation_and_omission_rule(sample_pipeline_data):
    """Phase 3: Subsequent report recalculates risks. Omitted risk is NOT marked resolved without explicit evidence."""
    with tempfile.TemporaryDirectory() as tmpdir:
        # Step 1: Initial EL
        RiskLifecycleService.generate_initial_contractual_assessment(
            project_id=102,
            document_id=1,
            doc_name="Engagement_Letter.pdf",
            step4_artifact=sample_pipeline_data["step4"],
            step7_artifact=sample_pipeline_data["step7"],
            step9_artifact=sample_pipeline_data["step9"],
            run_id="run_el",
            logs_dir=tmpdir,
        )

        # Step 2: First report creates operational risk
        report1_extractions = {
            "blockers": [
                {
                    "text": "Security Vulnerability Review pending third-party penetration tester sign-off.",
                    "status": "BLOCKED",
                }
            ]
        }
        RiskLifecycleService.process_report_risk_assessment(
            project_id=102,
            document_id=2,
            doc_name="MoM_Week_1.docx",
            doc_type="MOM",
            report_text="Identified security vulnerability blocker.",
            extractions=report1_extractions,
            logs_dir=tmpdir,
        )

        # Step 3: Second report does NOT mention Security Vulnerability Review
        report2_extractions = {
            "activities": [
                {
                    "activity_name": "UI Wireframe Review",
                    "status": "COMPLETED",
                }
            ]
        }
        report2_assessment = RiskLifecycleService.process_report_risk_assessment(
            project_id=102,
            document_id=3,
            doc_name="Status_Report_Week_2.docx",
            doc_type="STATUS_REPORT",
            report_text="Completed UI wireframes.",
            extractions=report2_extractions,
            logs_dir=tmpdir,
        )

        assert report2_assessment["assessment_type"] == "RECALCULATED_OPERATIONAL_ASSESSMENT"

        # Verify omission rule: unmentioned open risk must NOT be silently marked RESOLVED
        risk_items = report2_assessment.get("risk_items", [])
        matching = [r for r in risk_items if "Security Vulnerability" in r["title"]]
        assert len(matching) > 0
        assert matching[0]["lifecycle_status"] == "CONTINUING"


def test_risk_lifecycle_duplicate_upload_prevention(sample_pipeline_data):
    """Verify processing the same document upload twice does not create duplicate snapshots."""
    with tempfile.TemporaryDirectory() as tmpdir:
        RiskLifecycleService.generate_initial_contractual_assessment(
            project_id=103,
            document_id=1,
            doc_name="EL.pdf",
            step4_artifact=sample_pipeline_data["step4"],
            step7_artifact=sample_pipeline_data["step7"],
            step9_artifact=sample_pipeline_data["step9"],
            run_id="run_el",
            logs_dir=tmpdir,
        )

        report_extractions = {"activities": [{"activity_name": "API Gateway Deploy", "status": "IN_PROGRESS"}]}

        snap1 = RiskLifecycleService.process_report_risk_assessment(
            project_id=103,
            document_id=5,
            doc_name="MoM.docx",
            doc_type="MOM",
            report_text="API Gateway Deploy in progress.",
            extractions=report_extractions,
            logs_dir=tmpdir,
        )

        # Repeat processing for the exact same document ID
        snap2 = RiskLifecycleService.process_report_risk_assessment(
            project_id=103,
            document_id=5,
            doc_name="MoM.docx",
            doc_type="MOM",
            report_text="API Gateway Deploy in progress.",
            extractions=report_extractions,
            logs_dir=tmpdir,
        )

        # Should recognize duplicate
        assert snap2.get("is_duplicate") is True
        snap1_id = snap1.get("snapshot_id")
        snap2_id = snap2.get("snapshot", {}).get("snapshot_id")
        assert snap1_id == snap2_id


def test_risk_lifecycle_phase4_project_completion_final_summary(sample_pipeline_data):
    """Phase 4: Formal project completion generates PROJECT_COMPLETION_SUMMARY closing verified items."""
    with tempfile.TemporaryDirectory() as tmpdir:
        RiskLifecycleService.generate_initial_contractual_assessment(
            project_id=104,
            document_id=1,
            doc_name="EL.pdf",
            step4_artifact=sample_pipeline_data["step4"],
            step7_artifact=sample_pipeline_data["step7"],
            step9_artifact=sample_pipeline_data["step9"],
            run_id="run_el",
            logs_dir=tmpdir,
        )

        summary = RiskLifecycleService.finalize_project_risks(
            project_id=104,
            completion_evidence="Formal client acceptance sign-off document ACSE-2026-FINAL signed by Client Sponsor.",
            authorized_by="Lead PM Emma",
            logs_dir=tmpdir,
        )

        assert summary["assessment_type"] == "PROJECT_COMPLETION_SUMMARY"
        completion = summary.get("completion_details", {})
        assert completion.get("authorized_by") == "Lead PM Emma"
        assert "Formal client acceptance sign-off" in completion.get("evidence", "")
        metrics = summary.get("metrics", {})
        assert metrics.get("resolved_count", 0) >= 1


def test_writing_block_1_exact_reconciliation():
    """
    Test exact requirements from writing-block (1).md:
    1. Step 10 scope breakdown: IN_SCOPE: 203, OUT_OF_SCOPE: 36, PENDING_REVIEW: 3, sum: 242.
    2. Step 8 milestone explicit dates and provenance: 2027-01-22, 2027-04-30, 2027-07-31.
    3. Step 9 distinct collections and realistic ownership.
    4. Step 10 dynamic traceability metrics matching Step 4 and Step 6.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        # Build mock Step 4 & 5 data matching writing-block (1).md counts (203 IN_SCOPE, 36 OUT_OF_SCOPE, 3 UNCERTAIN)
        proposed_preview = []
        # 203 in-scope items
        for i in range(1, 204):
            proposed_preview.append({
                "candidate_id": f"c_in_{i:04d}",
                "name": f"In-Scope Feature {i}",
                "scope_type": "IN_SCOPE",
                "record_type": "Deliverable",
                "baseline_approval_status": "APPROVED_IN_DRAFT",
                "complexity": "MEDIUM",
                "source_locator": f"para_{i}",
            })
        # 36 out-of-scope items
        for i in range(1, 37):
            proposed_preview.append({
                "candidate_id": f"c_out_{i:04d}",
                "name": f"Out-of-Scope Item {i}",
                "scope_type": "OUT_OF_SCOPE",
                "record_type": "Vendor Exclusion",
                "baseline_approval_status": "APPROVED_IN_DRAFT",
                "exclusion_rationale": "Explicit contractual boundary exclusion.",
                "source_locator": f"para_out_{i}",
            })
        # 3 uncertain items
        for i in range(1, 4):
            proposed_preview.append({
                "candidate_id": f"c_unc_{i:04d}",
                "name": f"Uncertain Item {i}",
                "scope_type": "UNCERTAIN",
                "record_type": "Ambiguous Term",
                "baseline_approval_status": "PENDING_REVIEW",
                "review_required": True,
                "source_locator": f"para_unc_{i}",
            })

        assert len(proposed_preview) == 242

        # Step 4 structured records with explicit milestone candidates and contractual anchors
        s4_structured = [
            {
                "candidate_id": "c_anchor_001",
                "name": "Engagement Period",
                "raw_text": "This engagement letter is made effective from this day of 22nd day of January 2027 till 30th April 2027",
            },
            {
                "candidate_id": "c_anchor_002",
                "name": "Production Support Duration",
                "raw_text": "Production support till 31st July 2027",
            },
            {
                "candidate_id": "c_ms_001",
                "name": "Employee Collaboration - Implement & UAT Deployment",
                "raw_text": "Implement & UAT Deployment (60% of milestone fees) on 12th February 2027",
                "record_type": "Milestone",
            },
            {
                "candidate_id": "c_ms_002",
                "name": "Employee Collaboration - UAT Sign-Off & Go-Live",
                "raw_text": "UAT Sign-Off & Go-Live (30% of milestone fees) on 18th March 2027",
                "record_type": "Milestone",
            },
            {
                "candidate_id": "c_ms_003",
                "name": "Employee Collaboration - Post Go-live Hyper care support",
                "raw_text": "Post Go-live Hyper care support (10% of milestone fees) on 01st April 2027",
                "record_type": "Milestone",
            },
            {
                "candidate_id": "c_ms_004",
                "name": "Internal Job Posting - Implement & UAT Deployment",
                "raw_text": "Implement & UAT Deployment (60% of milestone fees) on 11th February 2027",
                "record_type": "Milestone",
            },
        ]

        step4_mock = {
            "final_verdict": "STEP_4_VALIDATION_PASSED",
            "step4_execution_summary": {
                "total_input_candidates": 242,
                "verified_atomic_claims": 269,
                "total_raw_candidates": 269,
            },
            "structured_records": s4_structured,
            "atomic_claim_validations": [{"id": i} for i in range(269)],
        }

        step5_mock = {
            "validation_gate_passed": True,
            "proposed_scope_items_preview": proposed_preview,
            "metrics": {
                "proposed_scope_item_creates": 242,
                "total_retained_preview_records": 242,
            },
        }

        step6_mock = {
            "status": "PASSED",
            "total_raw_candidates": 269,
            "metrics": {
                "raw_map_candidates_count": 269,
                "canonical_candidates_count": 242,
            },
        }

        step7_mock = {
            "status": "PASSED",
            "findings": [
                {"finding_id": f"F_{i:03d}", "title": f"Finding {i}", "severity": "MEDIUM", "category": "CONTRACTUAL"}
                for i in range(1, 47)
            ],
        }

        # 1. Execute Step 8
        s8_res = Step8Inspector.execute_inspection(
            project_id=121,
            document_id=259,
            doc_name="demo_EL.docx",
            step4_artifact=step4_mock,
            step5_artifact=step5_mock,
            step6_artifact=step6_mock,
            step7_artifact=step7_mock,
            run_id="run_wb1_test",
            timestamp_str="20261029_163000",
            logs_dir=tmpdir,
            mode="DRY_RUN",
        )
        assert s8_res["status"] in ["PASSED", "PASSED_WITH_WARNINGS"]
        s8_art = s8_res["step8_artifact"]
        assert len(s8_art["work_items_schedule"]) == 203  # 203 in-scope work items scheduled!

        # Check explicit milestone dates extracted from source contract text
        milestones = s8_art["milestones_preview"]
        ms_dates = {m["name"]: m.get("planned_date") for m in milestones}
        assert ms_dates.get("Employee Collaboration - Implement & UAT Deployment") == "2027-02-12"
        assert ms_dates.get("Employee Collaboration - UAT Sign-Off & Go-Live") == "2027-03-18"
        assert ms_dates.get("Employee Collaboration - Post Go-live Hyper care support") == "2027-04-01"
        assert ms_dates.get("Internal Job Posting - Implement & UAT Deployment") == "2027-02-11"

        # 2. Execute Step 9
        s9_res = Step9Inspector.execute_inspection(
            project_id=121,
            document_id=259,
            doc_name="demo_EL.docx",
            step4_artifact=step4_mock,
            step5_artifact=step5_mock,
            step8_artifact=s8_art,
            run_id="run_wb1_test",
            timestamp_str="20261029_163000",
            logs_dir=tmpdir,
            mode="DRY_RUN",
        )
        assert s9_res["status"] in ["PASSED", "PASSED_WITH_WARNINGS"]
        s9_art = s9_res["step9_artifact"]
        assert "scope_delivery_obligations" in s9_art
        assert "customer_dependencies" in s9_art
        assert "vendor_out_of_scope_exclusions" in s9_art

        # 3. Execute Step 10
        initial_risk = RiskLifecycleService.generate_initial_contractual_assessment(
            project_id=121,
            document_id=259,
            doc_name="demo_EL.docx",
            step4_artifact=step4_mock,
            step7_artifact=step7_mock,
            step9_artifact=s9_art,
            run_id="run_wb1_test",
            logs_dir=tmpdir,
        )

        s10_res = Step10Inspector.execute_synthesis(
            project_id=121,
            document_id=259,
            doc_name="demo_EL.docx",
            step4_artifact=step4_mock,
            step5_artifact=step5_mock,
            step6_artifact=step6_mock,
            step7_artifact=step7_mock,
            step8_artifact=s8_art,
            step9_artifact=s9_art,
            initial_risk_assessment=initial_risk,
            run_id="run_wb1_test",
            timestamp_str="20261029_163000",
            logs_dir=tmpdir,
            mode="DRY_RUN",
        )

        s10_art = s10_res["step10_artifact"]
        payload = s10_art["dashboard_payload"]

        # Exact writing-block (1).md assertions
        in_scope = payload["in_scope_items"]
        out_scope = payload["out_of_scope_items"]
        pending_rev = payload["pending_review_items"]

        assert len(in_scope) == 203, f"Expected 203 in-scope items, got {len(in_scope)}"
        assert len(out_scope) == 36, f"Expected 36 out-of-scope items, got {len(out_scope)}"
        assert len(pending_rev) == 3, f"Expected 3 pending-review items, got {len(pending_rev)}"
        assert len(in_scope) + len(out_scope) + len(pending_rev) == 242

        # Check disposition review_status
        for item in in_scope:
            assert item["review_status"] == "APPROVED_IN_DRAFT"
        for item in pending_rev:
            assert item["review_status"] == "PENDING_REVIEW"

        # Check explicit contract timeline
        timeline = payload["timeline"]
        assert timeline["project_start"] == "2027-01-22"
        assert timeline["project_end"] == "2027-04-30"
        assert timeline["production_support_end"] == "2027-07-31"

        # Check traceability reconciliation
        tm = payload["traceability_matrix"]
        assert tm["step3_raw_candidates"] == 269
        assert tm["step4_canonical_candidates"] == 242
        assert tm["step5_retained_preview_records"] == 242
        assert tm["atomic_claim_verifications_count"] == 269
        assert tm["step6_traceability_rate"] == "100%"

        # Zero production writes confirmed
        assert s10_art["safety_confirmations"]["actual_writes_performed"] == 0
