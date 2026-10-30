import os
import sys
import json
import pytest

# Ensure new_agent_2_BE is on sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from services.deep_scan_extractor import DeepScanExtractor
from services.step8_inspector import Step8Inspector
from services.step9_inspector import Step9Inspector
from services.grouped_artifact_service import GroupedArtifactService


SAMPLE_TABLE_CHUNK = """
**4. Progress reporting and issue resolution process**  
**4.1 Project status report**  
There will be a weekly status reports which will be circulated to all the key stakeholders of Google Limited & Google.  
**4.2 Escalation Matrix**  
|  |  |  |
| --- | --- | --- |
| **Level of escalation** | **Google Limited** | **Google** |
| Level 1 | Project Manager | Project Manager |
| Level 2 | Project Sponsor | Project Director |
| Level 3 | Project Sponsor | Project Partner |  
"""


def test_table_evidence_supported_claims():
    """
    Test 1: Supported table-derived claims pass verification and return structured table_provenance.
    """
    c_text = SAMPLE_TABLE_CHUNK
    claim1 = "Level 1 escalation involves Project Manager for Google Limited and Project Manager for Google"
    res1 = DeepScanExtractor.find_table_evidence_span(c_text, claim1)
    assert res1 is not None, "Claim 1 should be verified by Table 2"
    st, en, span_txt, prov = res1
    assert "Level 1" in span_txt
    assert prov["is_table_evidence"] is True
    assert prov["directional_bindings_verified"] is True
    assert prov["cell_mappings"]["Google Limited"] == "Project Manager"
    assert prov["cell_mappings"]["Google"] == "Project Manager"

    claim2 = "Level 2 escalation involves Project Sponsor for Google Limited and Project Director for Google"
    res2 = DeepScanExtractor.find_table_evidence_span(c_text, claim2)
    assert res2 is not None, "Claim 2 should be verified by Table 2"
    _, _, _, prov2 = res2
    assert prov2["cell_mappings"]["Google Limited"] == "Project Sponsor"
    assert prov2["cell_mappings"]["Google"] == "Project Director"

    claim3 = "Level 3 escalation involves Project Sponsor for Google Limited and Project Partner for Google"
    res3 = DeepScanExtractor.find_table_evidence_span(c_text, claim3)
    assert res3 is not None, "Claim 3 should be verified by Table 2"
    _, _, _, prov3 = res3
    assert prov3["cell_mappings"]["Google Limited"] == "Project Sponsor"
    assert prov3["cell_mappings"]["Google"] == "Project Partner"


def test_table_evidence_unsupported_and_swapped_roles_rejected():
    """
    Test 2: Hallucinated roles and directional role swaps are strictly rejected.
    """
    c_text = SAMPLE_TABLE_CHUNK

    # 1. Hallucinated role: Vice President not present in Level 1 row
    claim_hallucinated = "Level 1 escalation involves Vice President for Google Limited and Project Manager for Google"
    res_unsup = DeepScanExtractor.find_table_evidence_span(c_text, claim_hallucinated)
    assert res_unsup is None, "Claim asserting Vice President must be rejected as unsupported"

    # 2. Swapped roles: Google Limited assigned Project Director instead of Project Sponsor
    claim_swapped = "Level 2 escalation involves Project Director for Google Limited and Project Sponsor for Google"
    res_swapped = DeepScanExtractor.find_table_evidence_span(c_text, claim_swapped)
    assert res_swapped is None, "Directional role mismatch must be rejected"


def test_step8_gate_failure_causes_blocked_diagnostic(tmp_path):
    """
    Test 3: Step 8 produces BLOCKED diagnostic when Step 4 or Step 5 prerequisite gate fails.
    Zero operational writes are performed.
    """
    logs_dir = str(tmp_path / "logs")
    os.makedirs(logs_dir, exist_ok=True)

    failed_step4 = {
        "final_verdict": "STEP_4_VALIDATION_FAILED",
        "structured_records": [],
    }
    failed_step5 = {
        "validation_gate_passed": False,
        "proposed_scope_items_preview": [],
    }

    res = Step8Inspector.execute_inspection(
        project_id=999,
        document_id=888,
        doc_name="test_doc.docx",
        step4_artifact=failed_step4,
        step5_artifact=failed_step5,
        step6_artifact={},
        step7_artifact={},
        run_id="test_run_gate_fail",
        timestamp_str="20261029_000000",
        logs_dir=logs_dir,
        mode="DRY_RUN",
    )

    assert res["status"] == "BLOCKED"
    art = res["step8_artifact"]
    assert art["status"] == "BLOCKED"
    assert "Step 4 validation gate failed" in art["blocked_reason"]
    assert art["safety_confirmations"]["actual_writes_performed"] == 0
    assert art["safety_confirmations"]["step_10_disabled"] is False
    assert os.path.exists(res["step8_run_json_path"])
    assert os.path.exists(res["step8_log_path"])


def test_step8_dry_run_milestones_and_dag_success(tmp_path):
    """
    Test 4: When prerequisites pass, Step 8 identifies milestones and validates acyclic DAG.
    Zero writes performed.
    """
    logs_dir = str(tmp_path / "logs")
    os.makedirs(logs_dir, exist_ok=True)

    passed_step4 = {
        "final_verdict": "STEP_4_VALIDATION_PASSED",
        "structured_records": [
            {"candidate_id": "c1", "name": "Milestone 1: Kickoff", "record_type": "Milestone", "is_pure_milestone": True, "deadline": "2026-11-01"},
            {"candidate_id": "c2", "name": "Milestone 2: Design Approval", "record_type": "Milestone", "is_pure_milestone": True, "deadline": "2026-11-15"},
            {"candidate_id": "c3", "name": "Milestone 3: Go-Live", "record_type": "Milestone", "is_pure_milestone": True, "deadline": "2026-12-01"},
        ],
    }
    passed_step5 = {
        "validation_gate_passed": True,
        "proposed_scope_items_preview": passed_step4["structured_records"],
    }

    res = Step8Inspector.execute_inspection(
        project_id=999,
        document_id=888,
        doc_name="test_doc.docx",
        step4_artifact=passed_step4,
        step5_artifact=passed_step5,
        step6_artifact={},
        step7_artifact={},
        run_id="test_run_success",
        timestamp_str="20261029_000000",
        logs_dir=logs_dir,
        mode="DRY_RUN",
    )

    assert res["status"] in ("PASSED", "PASSED_WITH_WARNINGS")
    art = res["step8_artifact"]
    assert len(art["milestones_preview"]) == 3
    assert len(art["dependencies_preview"]) >= 2
    assert art["dag_acyclic"] is True
    assert art["safety_confirmations"]["actual_writes_performed"] == 0
    assert art["safety_confirmations"]["step_10_disabled"] is False


def test_step9_gate_failure_causes_blocked_diagnostic(tmp_path):
    """
    Test 5: Step 9 produces BLOCKED diagnostic when upstream gate is blocked.
    """
    logs_dir = str(tmp_path / "logs")
    os.makedirs(logs_dir, exist_ok=True)

    failed_step4 = {"final_verdict": "STEP_4_VALIDATION_FAILED"}
    failed_step5 = {"validation_gate_passed": False}
    blocked_step8 = {"status": "BLOCKED", "blocked_reason": "Prerequisite failed"}

    res = Step9Inspector.execute_inspection(
        project_id=999,
        document_id=888,
        doc_name="test_doc.docx",
        step4_artifact=failed_step4,
        step5_artifact=failed_step5,
        step8_artifact=blocked_step8,
        run_id="test_run_gate_fail",
        timestamp_str="20261029_000000",
        logs_dir=logs_dir,
        mode="DRY_RUN",
    )

    assert res["status"] == "BLOCKED"
    art = res["step9_artifact"]
    assert art["status"] == "BLOCKED"
    assert "Step 4 validation gate failed" in art["blocked_reason"]
    assert art["safety_confirmations"]["actual_writes_performed"] == 0
    assert art["safety_confirmations"]["step_10_disabled"] is False


def test_step9_dry_run_recurrence_expansion_success(tmp_path):
    """
    Test 6: When prerequisites pass, Step 9 detects recurrence cadence and expands schedule occurrences.
    Zero writes performed.
    """
    logs_dir = str(tmp_path / "logs")
    os.makedirs(logs_dir, exist_ok=True)

    passed_step4 = {
        "final_verdict": "STEP_4_VALIDATION_PASSED",
        "structured_records": [
            {"candidate_id": "c10", "name": "Weekly project status report", "description": "Circulate weekly status report", "record_type": "Vendor Responsibility"},
            {"candidate_id": "c11", "name": "Monthly steering committee meeting", "description": "Conduct monthly review", "record_type": "Governance Obligation"},
        ],
    }
    passed_step5 = {
        "validation_gate_passed": True,
        "proposed_scope_items_preview": passed_step4["structured_records"],
    }
    passed_step8 = {
        "status": "PASSED",
        "milestones_preview": [],
        "dependencies_preview": [],
    }

    res = Step9Inspector.execute_inspection(
        project_id=999,
        document_id=888,
        doc_name="test_doc.docx",
        step4_artifact=passed_step4,
        step5_artifact=passed_step5,
        step8_artifact=passed_step8,
        run_id="test_run_success",
        timestamp_str="20261029_000000",
        logs_dir=logs_dir,
        mode="DRY_RUN",
    )

    assert res["status"] in ("PASSED", "PASSED_WITH_WARNINGS")
    art = res["step9_artifact"]
    assert len(art["recurring_commitments_preview"]) == 2
    assert len(art["simulated_occurrences_preview"]) > 0
    assert art["safety_confirmations"]["actual_writes_performed"] == 0
    assert art["safety_confirmations"]["step_10_disabled"] is False


def test_grouped_artifact_consolidation_and_integrity(tmp_path):
    """
    Test 7: GroupedArtifactService consolidates exactly 3 log files and 3 JSON bundles,
    verifying existence, valid JSON parsing, and computing SHA-256 hashes.
    """
    logs_dir = str(tmp_path / "logs")
    os.makedirs(logs_dir, exist_ok=True)
    run_id = "test_grouped_run_20261029"

    # Create dummy step logs & JSON data
    step_data = {}
    for i in range(1, 11):
        log_f = os.path.join(logs_dir, f"step_{i}.log")
        with open(log_f, "w", encoding="utf-8") as f:
            f.write(f"Sample log line for Step {i}\n")
        step_data[i] = {
            "name": f"Step {i} Name",
            "status": "COMPLETED",
            "log_path": log_f,
            "json_data": {"step_num": i, "sample_key": f"value_{i}"},
            "source_artifact": f"step_{i}.json",
            "duration": 0.5,
        }

    res = GroupedArtifactService.consolidate(
        project_id=120,
        document_id=258,
        doc_name="demo_EL.docx",
        run_id=run_id,
        timestamp_str="20261029_120000",
        logs_dir=logs_dir,
        step_data=step_data,
    )

    assert res["all_files_exist_and_verified"] is True
    assert res["actual_writes_performed"] == 0
    assert res["step_10_disabled"] is False

    grouped = res["grouped_artifacts"]
    assert "group1" in grouped and "group2" in grouped and "group3" in grouped

    for gk in ["group1", "group2", "group3"]:
        g = grouped[gk]
        assert os.path.exists(g["log_path"]), f"{g['log_file']} must exist"
        assert os.path.exists(g["json_path"]), f"{g['json_file']} must exist"
        assert len(g["log_sha256"]) == 64
        assert len(g["json_sha256"]) == 64

        # Validate JSON parses properly and has run manifest
        with open(g["json_path"], "r", encoding="utf-8") as jf:
            parsed = json.load(jf)
            assert "manifest" in parsed
            assert parsed["manifest"]["run_id"] == run_id
            assert parsed["manifest"]["project_id"] == 120
            assert parsed["manifest"]["document_id"] == 258

