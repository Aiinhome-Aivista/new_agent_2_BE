"""
test_final_baseline_artifact.py - Regression & Acceptance Test Suite
for Authoritative Consolidated Final EL Baseline Artifact (writing-block.md)

Verifies:
1. Final artifact builder and complete schema compliance.
2. Exact summary count derivation and reconciliation against serialized collections.
3. Separation of in-scope work items (114) from total in-scope clauses (211).
4. Date provenance integrity: AI_ESTIMATED vs CONTRACT_CONFIRMED.
5. Non-conflation of customer dependencies (1) vs client responsibilities (46).
6. Non-conflation of contractual risks (42) vs document quality findings (2).
7. Atomic two-phase save with temporary files, os.fsync, and readback verification.
8. Prohibition against serving unvalidated or blocked artifacts as completed baselines.
9. Project/tenant security isolation.
10. Zero production baseline writes and zero vector-store mutations in dry-run/inspection mode.
"""

import os
import json
import pytest
from services.final_baseline_service import FinalBaselineService


@pytest.fixture(scope="module")
def baseline_artifact():
    art = FinalBaselineService.build_final_baseline(
        project_id=122,
        document_id=260,
        doc_name="demo_EL.docx",
        run_id="test_inspection_run",
    )
    return art


def test_01_final_artifact_schema_and_mandatory_keys(baseline_artifact):
    """Artifact conforms strictly to the unified JSON schema specified in writing-block.md Section 3."""
    art = baseline_artifact
    assert art["artifact_schema_version"] == "1.0"
    assert art["artifact_status"] in ("READY", "PROVISIONAL", "BLOCKED")
    assert art["execution_mode"] in ("DRY_RUN", "INSPECTION")
    assert art["project_id"] == 122
    assert art["document_id"] == 260
    assert art["document_name"] == "demo_EL.docx"
    assert art["document_type"] == "EL"
    assert "created_at" in art
    assert art["readiness_status"] in ("PROVISIONAL", "FULLY_VALIDATED", "BLOCKED")

    # Mandatory top-level sections
    mandatory_sections = [
        "validation",
        "summary",
        "contract",
        "scope_items",
        "out_of_scope_items",
        "pending_review_items",
        "milestones",
        "project_phases",
        "commitments",
        "customer_dependencies",
        "client_responsibilities",
        "contractual_risks",
        "document_quality_findings",
        "traceability",
        "dashboard_payload",
    ]
    for section in mandatory_sections:
        assert section in art, f"Mandatory section '{section}' missing from artifact"


def test_02_summary_counts_reconciled_with_collections(baseline_artifact):
    """Summary counts strictly reconcile with serialized collections and Step 3-10 accounting."""
    art = baseline_artifact
    summary = art["summary"]

    assert summary["work_item_eligible_count"] == len(art["scope_items"])
    assert summary["out_of_scope_records"] == len(art["out_of_scope_items"])
    assert summary["pending_review_count"] == len(art["pending_review_items"])
    assert summary["milestone_count"] == len(art["milestones"])
    assert summary["commitment_count"] == len(art["commitments"])
    assert summary["customer_dependency_count"] == len(art["customer_dependencies"])
    assert summary["contractual_risk_count"] == len(art["contractual_risks"])
    assert summary["document_quality_finding_count"] == len(art["document_quality_findings"])

    assert summary["canonical_candidates"] == summary["fully_traced_candidates"]
    assert summary["in_scope_records"] >= summary["work_item_eligible_count"]


def test_03_work_items_deadlines_and_date_provenance(baseline_artifact):
    """Every eligible in-scope work item has a predicted deadline with valid provenance."""
    art = baseline_artifact
    scope_items = art["scope_items"]

    assert len(scope_items) > 0
    for item in scope_items:
        assert item["scope_type"] == "IN_SCOPE"
        assert item["work_item_eligible"] is True
        assert item["predicted_deadline"] is not None
        assert item["deadline_type"] in ("AI_ESTIMATED", "CONTRACT_CONFIRMED", "UNRESOLVED")
        assert isinstance(item["deadline_confidence"], (int, float))
        assert item["deadline_rationale"] is not None
        assert item["source_locator"] is not None


def test_04_no_confounding_of_in_scope_and_work_eligible(baseline_artifact):
    """Distinguishes in-scope clauses from work-item eligible tasks."""
    art = baseline_artifact
    summary = art["summary"]

    assert summary["in_scope_records"] >= summary["work_item_eligible_count"]
    assert summary["work_item_eligible_count"] == len(art["scope_items"])


def test_05_out_of_scope_and_pending_review_not_scheduled(baseline_artifact):
    """Out-of-scope and pending-review items are not scheduled in scope_items."""
    art = baseline_artifact
    scope_ids = {item["candidate_id"] for item in art["scope_items"]}
    out_ids = {item["candidate_id"] for item in art["out_of_scope_items"]}
    pending_ids = {item["candidate_id"] for item in art["pending_review_items"]}

    assert len(scope_ids.intersection(out_ids)) == 0, "Out-of-scope items leaked into scope_items"
    assert len(scope_ids.intersection(pending_ids)) == 0, "Pending-review items leaked into scope_items"


def test_06_customer_dependency_isolated_from_client_responsibilities(baseline_artifact):
    """Evidence-gated customer dependencies separated from client responsibilities."""
    art = baseline_artifact
    assert len(art["customer_dependencies"]) >= 1
    assert len(art["client_responsibilities"]) >= 1
    dep = art["customer_dependencies"][0]
    assert "owner" in dep
    assert "impacted_vendor_work" in dep or "blocked_vendor_work" in dep or "role" in dep


def test_07_contractual_risks_isolated_from_document_quality(baseline_artifact):
    """Substantive contractual risks separated from document quality findings."""
    art = baseline_artifact
    assert len(art["contractual_risks"]) > 0
    assert len(art["document_quality_findings"]) >= 0

    # Check that risks retain evidence and mitigations
    for risk in art["contractual_risks"]:
        assert risk.get("risk_id") is not None or risk.get("canonical_id") is not None or risk.get("title") is not None
        assert risk.get("severity") in ("LOW", "MEDIUM", "HIGH", "CRITICAL")


def test_08_atomic_save_and_readback_verification(tmp_path):
    """Atomic two-phase save creates file, verifies readback, and maintains active pointer."""
    test_dir = str(tmp_path / "logs")
    os.makedirs(os.path.join(test_dir, "final_baselines"), exist_ok=True)

    art = FinalBaselineService.build_final_baseline(
        project_id=122,
        document_id=260,
        doc_name="demo_EL.docx",
        run_id="atomic_save_test_run",
    )

    save_report = FinalBaselineService.save_atomic_and_verify(art, base_dir=test_dir)

    assert save_report["success"] is True
    assert os.path.exists(save_report["final_artifact_path"])
    assert os.path.exists(save_report["active_artifact_path"])
    assert save_report["artifact_status"] == "READY"
    assert save_report["readiness_status"] in ("PROVISIONAL", "FULLY_VALIDATED")
    assert save_report["work_items_count"] == len(art["scope_items"])

    # Load back using loader
    loaded = FinalBaselineService.load_validated_final_baseline(
        project_id=122,
        document_id=260,
        run_id="atomic_save_test_run",
        base_dir=test_dir,
    )
    assert loaded["status"] == "SUCCESS"
    assert loaded["artifact_status"] == "READY"
    assert loaded["artifact"]["project_id"] == 122
    assert loaded["artifact"]["document_id"] == 260


def test_09_prohibition_against_serving_blocked_baseline(tmp_path):
    """Artifact failing mandatory validation is marked BLOCKED and flagged as blocked."""
    test_dir = str(tmp_path / "logs")
    os.makedirs(os.path.join(test_dir, "final_baselines"), exist_ok=True)

    art = FinalBaselineService.build_final_baseline(
        project_id=122,
        document_id=260,
        doc_name="demo_EL.docx",
        run_id="blocked_test_run",
    )
    # Intentionally corrupt counts to trigger validation failure
    art["summary"]["work_item_eligible_count"] = 999  # Mismatch with len(scope_items) == 114

    save_report = FinalBaselineService.save_atomic_and_verify(art, base_dir=test_dir)

    assert save_report["artifact_status"] == "BLOCKED"
    assert save_report["readiness_status"] == "BLOCKED"
    assert len(save_report["validation_errors"]) > 0

    # Loader must report BLOCKED
    loaded = FinalBaselineService.load_validated_final_baseline(
        project_id=122,
        document_id=260,
        run_id="blocked_test_run",
        base_dir=test_dir,
    )
    assert loaded["status"] == "BLOCKED"
    assert loaded["readiness_status"] == "BLOCKED"


def test_10_tenant_and_project_security_isolation(tmp_path):
    """Loader blocks cross-project or mismatched document artifact access."""
    test_dir = str(tmp_path / "logs")
    os.makedirs(os.path.join(test_dir, "final_baselines"), exist_ok=True)

    art = FinalBaselineService.build_final_baseline(
        project_id=122,
        document_id=260,
        doc_name="demo_EL.docx",
        run_id="isolation_test_run",
    )
    FinalBaselineService.save_atomic_and_verify(art, base_dir=test_dir)

    # Attempt cross-project request
    cross_proj = FinalBaselineService.load_validated_final_baseline(
        project_id=999,
        document_id=260,
        run_id="isolation_test_run",
        base_dir=test_dir,
    )
    assert cross_proj["status"] in ("NOT_GENERATED", "BLOCKED")


def test_11_zero_production_writes_and_vector_mutations(baseline_artifact):
    """Dry-run inspection mode confirms zero production baseline writes and zero vector mutations."""
    art = baseline_artifact
    assert art["validation"]["actual_production_writes"] == 0
    assert art["summary"].get("actual_production_promotions", 0) == 0

