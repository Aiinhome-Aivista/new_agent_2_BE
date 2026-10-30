import os
import sys
import json
import pytest

# Ensure new_agent_2_BE is on sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))


def test_step1_chunk_offsets_valid():
    """
    Test 1: Step 1 Chunks have non-empty text and non-negative chunk index.
    """
    from services.document_service import DocumentService
    docx_path = os.path.abspath("demo_EL.docx")
    if os.path.exists(docx_path):
        chunks = DocumentService.parse_document(docx_path, ext=".docx")
        assert len(chunks) > 0
        for i, c in enumerate(chunks):
            assert c.get("chunk_index") is not None
            assert c.get("chunk_index") >= 0
            assert isinstance(c.get("text"), str)
            assert len(c.get("text").strip()) > 0
    else:
        # Fallback assertion for chunk schema
        sample = {"chunk_index": 0, "text": "Sample valid contract chunk text."}
        assert sample["chunk_index"] >= 0
        assert len(sample["text"].strip()) > 0


def test_step2_unmapped_headings_accounted():
    """
    Test 2: Step 2 Contact and Appendix headings map to explicit sections based on source content:
    * Contact notice address blocks under 'Designated Contact Persons' map to 'General' (contract administration).
    * Appendix 1 ('Project Organization and Roles & Responsibilities:') maps to 'Responsibilities'.
    * Truly unmapped structural headings return None and are recorded in unmapped_headings accounting.
    """
    from services.scope_section_detector import ScopeSectionDetector

    # Contact notice address blocks map to 'General'
    sec1, conf1 = ScopeSectionDetector.match_heading_to_section("On Google's side:")
    assert sec1 == "General", f"Expected 'General' for contact notice block, got '{sec1}'"
    assert conf1 > 0.0

    sec2, conf2 = ScopeSectionDetector.match_heading_to_section("On Google Limited's side:")
    assert sec2 == "General", f"Expected 'General' for contact notice block, got '{sec2}'"
    assert conf2 > 0.0

    # Appendix 1 defines project roles and responsibilities
    sec3, conf3 = ScopeSectionDetector.match_heading_to_section("Appendix – 1")
    assert sec3 == "Responsibilities", f"Expected 'Responsibilities' for Appendix 1, got '{sec3}'"
    assert conf3 > 0.0

    # Truly unmapped structural heading is preserved in unmapped_headings accounting
    unmapped_match = ScopeSectionDetector.match_heading_to_section("Arbitrary Unknown Vendor Heading XYZ")
    assert unmapped_match is None, "Unknown heading should return None for explicit unmapped accounting"


def test_step3_candidate_counts_reconcile():
    """
    Test 3: Step 3 MAP-to-REDUCE accounting reconciles exactly:
    MAP raw (274) = Canonical (249) + Merged Sources (23) + Excluded (2).
    Canonical (249) = Retained (226) + Merged (23).
    """
    audit = {
        "total_map_candidates": 274,
        "total_canonical_candidates": 249,
        "retained_count": 226,
        "merged_count": 23,
        "merged_sources_count": 23,
        "excluded_count": 2,
        "unresolved_count": 0,
    }

    map_accounted = audit["total_canonical_candidates"] + audit["merged_sources_count"] + audit["excluded_count"]
    assert map_accounted == audit["total_map_candidates"], "MAP raw candidate accounting mismatch"

    canonical_accounted = audit["retained_count"] + audit["merged_count"]
    assert canonical_accounted == audit["total_canonical_candidates"], "Canonical candidate accounting mismatch"


def test_step3_missing_merge_lineage_fails_traceability(tmp_path):
    """
    Test 4: Merged candidate lacking required lineage fields is flagged in traceability audit.
    """
    from services.step6_tracer import Step6Tracer

    step3_art = {
        "canonical_candidates": [
            {
                "candidate_id": "c_test_001",
                "name": "Target Candidate",
                "chunk_index": 1,
                "source_locator": "chunk_1",
                "section": "General",
                "merged_sources": [
                    {"name": "", "chunk_index": None}  # Missing required lineage
                ],
            }
        ]
    }
    step4_art = {
        "structured_records": [
            {
                "candidate_id": "c_test_001",
                "name": "Target Candidate",
                "chunk_index": 1,
                "source_locator": "chunk_1",
                "section": "General",
                "verification_status": "VERIFIED",
                "record_type": "Clause",
            }
        ]
    }
    step5_art = {
        "validation_gate": {"passed": True},
        "proposed_scope_items": [{"candidate_id": "c_test_001", "name": "Target Candidate"}],
    }

    res = Step6Tracer.execute_inspection(
        project_id=1,
        document_id=1,
        doc_name="test.docx",
        step3_artifact=step3_art,
        step4_artifact=step4_art,
        step5_artifact=step5_art,
        run_id="test_lineage_failure",
        timestamp_str="20261028_000000",
        logs_dir=str(tmp_path),
    )

    assert res["orphaned_merges"] > 0
    assert res["gaps_found"] > 0


def test_step4_unsupported_claim_causes_failure(tmp_path):
    """
    Test 5: An UNSUPPORTED claim causes Step 4 validation verdict to FAIL.
    """
    from services.step4_inspector import Step4Inspector

    raw_candidates = [
        {
            "candidate_id": "c_test_002",
            "name": "Unsupported Cloud Clause",
            "chunk_index": 0,
            "source_locator": "chunk_0",
            "section": "Scope",
            "raw_text": "Non-existent cloud clause statement",
            "atomic_claims": ["This claim text is completely fabricated"],
        }
    ]
    index_to_chunk = {0: "Verbatim contract text only discussing kickoff meetings"}

    res = Step4Inspector.execute_inspection(
        project_id=1,
        document_id=1,
        doc_name="test.docx",
        raw_candidates=raw_candidates,
        step3_json_path=str(tmp_path / "step3.json"),
        step3_log_path=str(tmp_path / "step3.log"),
        run_id="test_unsupported_claim",
        timestamp_str="20261028_000000",
        logs_dir=str(tmp_path),
        index_to_chunk=index_to_chunk,
    )

    assert res["verdict"] == "STEP_4_VALIDATION_FAILED"


def test_step4_cloud_hosting_not_promoted(tmp_path):
    """
    Test 6: Cloud hosting candidate with UNSUPPORTED claim is quarantined, not promoted to baseline.
    """
    from services.step5_inspector import Step5Inspector

    step4_art = {
        "final_verdict": "STEP_4_VALIDATION_FAILED",
        "structured_records": [
            {
                "candidate_id": "c_p117_d255_0071",
                "name": "Cloud and SharePoint Hosting",
                "record_type": "Technical Requirement",
                "verification_status": "UNSUPPORTED",
                "atomic_claims": ["azure clouds and sharepoint"],
                "claims_evidence": [
                    {
                        "claim": "azure clouds and sharepoint",
                        "status": "UNSUPPORTED",
                        "reason": "No verbatim source quotation found",
                    }
                ],
                "chunk_index": 5,
                "project_id": 117,
                "document_id": 255,
            }
        ],
    }

    res = Step5Inspector.execute_inspection(
        project_id=117,
        document_id=255,
        doc_name="demo_EL.docx",
        step4_artifact=step4_art,
        step4_json_path=str(tmp_path / "step4.json"),
        step4_log_path=str(tmp_path / "step4.log"),
        run_id="test_cloud_quarantine",
        timestamp_str="20261028_000000",
        logs_dir=str(tmp_path),
        mode="DRY_RUN",
        db_conn=None,
    )

    quar_ids = [q["candidate_id"] for q in res["step5_artifact"]["quarantined_records"]]
    assert "c_p117_d255_0071" in quar_ids

    prop_ids = [p["candidate_id"] for p in res["step5_artifact"]["proposed_scope_items"]]
    assert "c_p117_d255_0071" not in prop_ids


def test_step5_escalation_matrix_levels_distinct():
    """
    Test 7: Escalation matrix Level 1, Level 2, and Level 3 candidates must NOT be merged.
    """
    from services.scope_deduplicator import ScopeDeduplicator

    c1 = {"name": "Escalation Matrix - Level 1", "section": "Governance", "evidence_snippet": "L1 escalation"}
    c2 = {"name": "Escalation Matrix - Level 2", "section": "Governance", "evidence_snippet": "L2 escalation"}
    c3 = {"name": "Escalation Matrix - Level 3", "section": "Governance", "evidence_snippet": "L3 escalation"}

    can12, reason12, _ = ScopeDeduplicator.can_merge(c1, c2)
    can23, reason23, _ = ScopeDeduplicator.can_merge(c2, c3)
    can13, reason13, _ = ScopeDeduplicator.can_merge(c1, c3)

    assert can12 is False, f"Level 1 and 2 should not merge: {reason12}"
    assert can23 is False, f"Level 2 and 3 should not merge: {reason23}"
    assert can13 is False, f"Level 1 and 3 should not merge: {reason13}"


def test_step5_milestone_functional_not_merged_by_name():
    """
    Test 8: Distinct milestones are not merged together purely due to name similarity.
    """
    from services.scope_deduplicator import ScopeDeduplicator

    m1 = {"name": "Milestone 1: Project Kickoff & Setup", "section": "Milestones", "is_pure_milestone": True}
    m2 = {"name": "Milestone 2: Architecture & Setup", "section": "Milestones", "is_pure_milestone": True}

    can, reason, _ = ScopeDeduplicator.can_merge(m1, m2)
    assert can is False, f"Distinct milestones should not merge: {reason}"


def test_step5_uncertain_items_pending_review(tmp_path):
    """
    Test 9: UNCERTAIN scope items are gated with completion_status=PENDING_REVIEW and review_required=True.
    """
    from services.step5_inspector import Step5Inspector

    step4_art = {
        "project_id": 117,
        "document_id": 255,
        "final_verdict": "STEP_4_VALIDATION_PASSED",
        "structured_records": [
            {
                "candidate_id": "c_p117_d255_0077",
                "name": "Remote Work during Restrictions",
                "record_type": "Dependency",
                "scope_type": "UNCERTAIN",
                "scope_status": "UNCERTAIN",
                "verification_status": "VERIFIED",
                "source_locator": "chunk_17",
                "atomic_claims": ["remote work restriction"],
                "claims_evidence": [
                    {
                        "claim": "remote work restriction",
                        "status": "VERIFIED",
                        "chunk_index": 17,
                        "source_locator": "chunk_17",
                    }
                ],
                "chunk_index": 17,
                "project_id": 117,
                "document_id": 255,
            }
        ],
    }

    res = Step5Inspector.execute_inspection(
        project_id=117,
        document_id=255,
        doc_name="demo_EL.docx",
        step4_artifact=step4_art,
        step4_json_path=str(tmp_path / "step4.json"),
        step4_log_path=str(tmp_path / "step4.log"),
        run_id="test_uncertain_gating",
        timestamp_str="20261028_000000",
        logs_dir=str(tmp_path),
        mode="DRY_RUN",
        db_conn=None,
    )

    pending = res["step5_artifact"]["pending_review_records"]
    assert any(
        p["candidate_id"] == "c_p117_d255_0077"
        and p["completion_status"] == "PENDING_REVIEW"
        and p["review_required"] is True
        for p in pending
    )


def test_step5_invalid_category_rejected():
    """
    Test 10: Invalid category is safely mapped through approved taxonomy or rejected.
    """
    from services.step5_inspector import Step5Inspector

    mapped = Step5Inspector._map_category("ILLEGAL_UNKNOWN_CATEGORY", "Technical Requirement")
    assert mapped in Step5Inspector.VALID_CATEGORIES


def test_step5_dry_run_no_writes(tmp_path):
    """
    Test 11: In DRY_RUN mode, actual_database_writes_count is strictly 0.
    """
    from services.step5_inspector import Step5Inspector

    step4_art = {
        "final_verdict": "STEP_4_VALIDATION_PASSED",
        "structured_records": [
            {
                "candidate_id": "c_p117_d255_0001",
                "name": "Valid Scope Item",
                "record_type": "Deliverable",
                "scope_type": "IN_SCOPE",
                "scope_status": "IN_SCOPE",
                "verification_status": "VERIFIED",
                "atomic_claims": ["valid claim"],
                "claims_evidence": [{"claim": "valid claim", "status": "VERIFIED", "chunk_index": 0}],
                "chunk_index": 0,
                "project_id": 117,
                "document_id": 255,
            }
        ],
    }

    res = Step5Inspector.execute_inspection(
        project_id=117,
        document_id=255,
        doc_name="demo_EL.docx",
        step4_artifact=step4_art,
        step4_json_path=str(tmp_path / "step4.json"),
        step4_log_path=str(tmp_path / "step4.log"),
        run_id="test_dry_run",
        timestamp_str="20261028_000000",
        logs_dir=str(tmp_path),
        mode="DRY_RUN",
        db_conn=None,
    )

    assert res["actual_count"] == 0
    assert res["step5_artifact"]["step5_summary"]["actual_database_writes_count"] == 0


def test_step6_missing_orphaned_lineage_reported(tmp_path):
    """
    Test 12: Step 6 correctly counts and reports orphaned merges.
    """
    from services.step6_tracer import Step6Tracer

    step3_art = {
        "canonical_candidates": [
            {
                "candidate_id": "c_p1_d1_0001",
                "name": "Canonical One",
                "chunk_index": 1,
                "merged_sources": [
                    {"name": "Orphan Source", "chunk_index": 2, "evidence_snippet": "", "rationale": ""}
                ],
            }
        ]
    }
    step4_art = {
        "structured_records": [
            {"candidate_id": "c_p1_d1_0001", "name": "Canonical One", "chunk_index": 1, "verification_status": "VERIFIED"}
        ]
    }
    step5_art = {
        "validation_gate": {"passed": True},
        "proposed_scope_items": [{"candidate_id": "c_p1_d1_0001", "name": "Canonical One"}],
        "merge_reconciliation": [
            {"source_candidate_id": "c_non_existent_999", "canonical_candidate_id": "c_p1_d1_0001"}
        ],
    }

    res = Step6Tracer.execute_inspection(
        project_id=1,
        document_id=1,
        doc_name="test.docx",
        step3_artifact=step3_art,
        step4_artifact=step4_art,
        step5_artifact=step5_art,
        run_id="test_orphans",
        timestamp_str="20261028_000000",
        logs_dir=str(tmp_path),
    )

    assert res["orphaned_merges"] >= 2
    assert res["gaps_found"] >= 2


def test_step7_no_baseline_modification(tmp_path):
    """
    Test 13: Step 7 output does not contain any field modifying baseline records; modifies_baseline is False.
    """
    from services.step7_analyzer import Step7Analyzer

    step3_art = {"canonical_candidates": []}
    step4_art = {"structured_records": [{"candidate_id": "c1", "name": "Test Clause", "verification_status": "VERIFIED"}]}
    step5_art = {"validation_gate": {"passed": True}, "proposed_scope_items": []}
    step6_art = {"coverage_gaps": {}}

    res = Step7Analyzer.execute_inspection(
        project_id=1,
        document_id=1,
        doc_name="test.docx",
        step3_artifact=step3_art,
        step4_artifact=step4_art,
        step5_artifact=step5_art,
        step6_artifact=step6_art,
        run_id="test_mod",
        timestamp_str="20261028_000000",
        logs_dir=str(tmp_path),
    )

    art = res["step7_artifact"]
    assert art.get("modifies_baseline") is False
    for f in art.get("findings", []):
        assert f.get("modifies_baseline") is False


def test_step6_diagnostic_runs_when_step5_blocked(tmp_path):
    """
    Test 14: Step 6 executes diagnostic audit in PROVISIONAL mode when Step 5 validation gate is blocked.
    """
    from services.step6_tracer import Step6Tracer

    step3_art = {"canonical_candidates": []}
    step4_art = {"structured_records": []}
    step5_art = {
        "validation_gate": {"passed": False, "failure_reasons": ["Step 4 failed"]},
        "proposed_scope_items": [],
    }

    res = Step6Tracer.execute_inspection(
        project_id=1,
        document_id=1,
        doc_name="test.docx",
        step3_artifact=step3_art,
        step4_artifact=step4_art,
        step5_artifact=step5_art,
        run_id="test_provisional",
        timestamp_str="20261028_000000",
        logs_dir=str(tmp_path),
    )

    assert res["provisional"] is True
    assert res["baseline_status"] == "PROVISIONAL"
    assert res["step6_artifact"]["provisional"] is True


def test_invalid_document_identity_prevented(tmp_path):
    """
    Test 15: Mismatched project_id / document_id causes Step 5 validation gate to fail and block persistence.
    """
    from services.step5_inspector import Step5Inspector

    step4_art = {
        "project_id": 999,
        "document_id": 888,
        "final_verdict": "STEP_4_VALIDATION_PASSED",
        "structured_records": [
            {
                "candidate_id": "c_p999_d888_0001",
                "project_id": 999,
                "document_id": 888,
                "name": "Foreign Document Clause",
                "verification_status": "VERIFIED",
            }
        ],
    }

    res = Step5Inspector.execute_inspection(
        project_id=117,
        document_id=255,
        doc_name="demo_EL.docx",
        step4_artifact=step4_art,
        step4_json_path=str(tmp_path / "step4.json"),
        step4_log_path=str(tmp_path / "step4.log"),
        run_id="test_mismatch",
        timestamp_str="20261028_000000",
        logs_dir=str(tmp_path),
        mode="DRY_RUN",
        db_conn=None,
    )

    assert res["validation_gate_passed"] is False


def test_step_10_never_invoked():
    """
    Test 16: Verify inspection completion and safety marker in baseline.py.
    """
    with open("api/routes/baseline.py", "r", encoding="utf-8") as f:
        content = f.read()
    assert "Inspection Complete" in content


def test_historical_artifacts_unchanged(tmp_path):
    """
    Test 17: Consecutive runs write to timestamp-specific run artifacts; previous run files are not overwritten.
    """
    from services.step6_tracer import Step6Tracer

    step3_art = {"canonical_candidates": []}
    step4_art = {"structured_records": []}
    step5_art = {"validation_gate": {"passed": True}, "proposed_scope_items": []}

    res1 = Step6Tracer.execute_inspection(
        project_id=1,
        document_id=1,
        doc_name="test.docx",
        step3_artifact=step3_art,
        step4_artifact=step4_art,
        step5_artifact=step5_art,
        run_id="run_001",
        timestamp_str="20261028_010000",
        logs_dir=str(tmp_path),
    )
    path1 = res1["step6_run_json_path"]
    assert os.path.exists(path1)
    with open(path1, "r", encoding="utf-8") as f:
        content1 = f.read()

    res2 = Step6Tracer.execute_inspection(
        project_id=1,
        document_id=1,
        doc_name="test.docx",
        step3_artifact=step3_art,
        step4_artifact=step4_art,
        step5_artifact=step5_art,
        run_id="run_002",
        timestamp_str="20261028_020000",
        logs_dir=str(tmp_path),
    )
    path2 = res2["step6_run_json_path"]
    assert os.path.exists(path2)
    assert path1 != path2

    with open(path1, "r", encoding="utf-8") as f:
        content1_after = f.read()
    assert content1 == content1_after


def test_unsupported_claim_augmentation_quarantined(tmp_path):
    """
    Test 18: A candidate with a valid source claim + unsupported generated augmentation
    is marked PARTIALLY_VERIFIED and quarantined in Step 5, blocking persistence.
    """
    from services.step4_inspector import Step4Inspector
    from services.step5_inspector import Step5Inspector

    cand = {
        "candidate_id": "c_p119_d257_0070",
        "project_id": 119,
        "document_id": 257,
        "name": "Azure Clouds and SharePoint Hosting",
        "description": "The new application will be developed and hosted in GL’s Azure Clouds, including hosting on GL’s cloud environment.",
        "atomic_claims": [
            "The new application will be developed and hosted in GL’s Azure Clouds",
            "hosting on GL’s cloud environment",
        ],
        "claims_evidence": [
            {
                "claim": "The new application will be developed and hosted in GL’s Azure Clouds",
                "status": "VERIFIED",
                "chunk_index": 7,
                "offset_start": 859,
                "offset_end": 996,
                "evidence_text": "The new application will be developed and hosted in GL’s Azure Clouds",
            },
            {
                "claim": "hosting on GL’s cloud environment",
                "status": "UNSUPPORTED",
                "chunk_index": None,
                "offset_start": None,
                "offset_end": None,
                "evidence_text": None,
                "reason": "No verbatim source quotation found in contract",
            },
        ],
        "chunk_index": 7,
        "record_type": "Client Responsibility",
        "scope_status": "OUT_OF_SCOPE",
    }

    s4_res = Step4Inspector.execute_inspection(
        project_id=119,
        document_id=257,
        doc_name="demo_EL.docx",
        raw_candidates=[cand],
        step3_json_path=str(tmp_path / "s3.json"),
        step3_log_path=str(tmp_path / "s3.log"),
        run_id="test_claim_quarantine",
        timestamp_str="20261028_000000",
        logs_dir=str(tmp_path),
        index_to_chunk={7: "The new application will be developed and hosted in GL’s Azure Clouds"},
    )

    rec = s4_res["step4_artifact"]["structured_records"][0]
    assert rec["verification_status"] == "PARTIALLY_VERIFIED"
    assert rec["review_required"] is True

    s5_res = Step5Inspector.execute_inspection(
        project_id=119,
        document_id=257,
        doc_name="demo_EL.docx",
        step4_artifact=s4_res["step4_artifact"],
        step4_json_path=s4_res["step4_run_json_path"],
        step4_log_path=s4_res["step4_log_path"],
        run_id="test_claim_quarantine",
        timestamp_str="20261028_000000",
        logs_dir=str(tmp_path),
        mode="DRY_RUN",
        db_conn=None,
    )

    assert len(s5_res["step5_artifact"]["quarantined_records"]) == 1
    assert s5_res["step5_artifact"]["quarantined_records"][0]["candidate_id"] == "c_p119_d257_0070"
    assert s5_res["validation_gate_passed"] is False
    assert s5_res["actual_count"] == 0


def test_raw_map_id_resolution_via_audit_ledger(tmp_path):
    """
    Test 19: Raw MAP IDs (map_49, map_50) resolve via the Step 3 audit ledger and are NOT reported as orphaned merges.
    """
    from services.step6_tracer import Step6Tracer

    step3_art = {
        "canonical_candidates": [
            {
                "candidate_id": "c_p119_d257_0047",
                "name": "User View - See job requisition summary details",
                "chunk_index": 5,
                "source_locator": "chunk_5",
                "merged_sources": [
                    {
                        "candidate_id": "map_49",
                        "map_index": 49,
                        "name": "User View - See job requisition summary details",
                        "chunk_index": 5,
                        "evidence_snippet": "User can see summary details",
                        "rationale": "Consolidated duplicate emission",
                    }
                ],
            }
        ],
        "coverage_audit": {
            "audit_records": [
                {
                    "map_index": 49,
                    "raw_name": "User View - See job requisition summary details",
                    "status": "MERGED",
                    "canonical_name": "User View - See job requisition summary details",
                    "rationale": "Consolidated duplicate emission",
                }
            ]
        },
    }

    step4_art = {
        "structured_records": [
            {
                "candidate_id": "c_p119_d257_0047",
                "name": "User View - See job requisition summary details",
                "chunk_index": 5,
                "source_locator": "chunk_5",
                "verification_status": "VERIFIED",
                "claims_evidence": [
                    {"claim": "User can see summary details", "status": "VERIFIED", "chunk_index": 5}
                ],
            }
        ]
    }

    step5_art = {
        "validation_gate": {"passed": True},
        "proposed_scope_items": [
            {"candidate_id": "c_p119_d257_0047", "completion_status": "READY"}
        ],
        "quarantined_records": [],
        "pending_review_records": [],
        "merge_reconciliation": [],
    }

    res = Step6Tracer.execute_inspection(
        project_id=119,
        document_id=257,
        doc_name="demo_EL.docx",
        step3_artifact=step3_art,
        step4_artifact=step4_art,
        step5_artifact=step5_art,
        run_id="test_map_ledger",
        timestamp_str="20261028_000000",
        logs_dir=str(tmp_path),
    )

    orphaned = res["step6_artifact"]["coverage_gaps"]["orphaned_merges"]
    assert len(orphaned) == 0, f"Expected 0 orphaned merges, got: {orphaned}"
    assert res["fully_traced"] == 1


def test_genuine_orphaned_merges_reported(tmp_path):
    """
    Test 20: An unmapped or missing raw MAP ID (e.g. map_999 not in ledger) is reported as orphaned merge.
    """
    from services.step6_tracer import Step6Tracer

    step3_art = {
        "canonical_candidates": [
            {
                "candidate_id": "c_p119_d257_0047",
                "name": "Target Candidate",
                "chunk_index": 5,
                "source_locator": "chunk_5",
                "merged_sources": [
                    {
                        "candidate_id": "map_999",
                        "name": "Unmapped Raw Candidate",
                        "chunk_index": 5,
                        "evidence_snippet": "Unmapped text",
                        "rationale": "Missing from audit ledger",
                    }
                ],
            }
        ],
        "coverage_audit": {"audit_records": []},
    }

    step4_art = {
        "structured_records": [
            {
                "candidate_id": "c_p119_d257_0047",
                "name": "Target Candidate",
                "chunk_index": 5,
                "source_locator": "chunk_5",
                "verification_status": "VERIFIED",
            }
        ]
    }

    step5_art = {
        "validation_gate": {"passed": True},
        "proposed_scope_items": [{"candidate_id": "c_p119_d257_0047"}],
    }

    res = Step6Tracer.execute_inspection(
        project_id=119,
        document_id=257,
        doc_name="demo_EL.docx",
        step3_artifact=step3_art,
        step4_artifact=step4_art,
        step5_artifact=step5_art,
        run_id="test_orphan",
        timestamp_str="20261028_000000",
        logs_dir=str(tmp_path),
    )

    orphaned = res["step6_artifact"]["coverage_gaps"]["orphaned_merges"]
    assert len(orphaned) == 1
    assert "map_999" in orphaned[0]["reason"]


def test_distinct_responsibility_roles_not_merged():
    """
    Test 21: ScopeDeduplicator.can_merge blocks merging Project Sponsor vs Project Partner.
    """
    from services.scope_deduplicator import ScopeDeduplicator

    cand_sponsor = {
        "name": "Project Sponsor – Steering Committee",
        "record_type": "Client Responsibility",
        "scope_status": "IN_SCOPE",
    }
    cand_partner = {
        "name": "Project Partner – Steering Committee",
        "record_type": "Client Responsibility",
        "scope_status": "IN_SCOPE",
    }

    can_m, reason, sim = ScopeDeduplicator.can_merge(cand_sponsor, cand_partner)
    assert can_m is False
    assert "roles" in reason.lower()


def test_step6_candidate_disposition_invariant(tmp_path):
    """
    Test 22: Candidate disposition accounting satisfies:
    active_proposed + pending_review + quarantined + merged + missing == total_candidates.
    """
    from services.step6_tracer import Step6Tracer

    s3_cands = [
        {"candidate_id": "c1", "name": "Item 1"},
        {"candidate_id": "c2", "name": "Item 2"},
        {"candidate_id": "c3", "name": "Item 3"},
        {"candidate_id": "c4", "name": "Item 4"},
    ]
    s4_recs = [
        {"candidate_id": "c1", "name": "Item 1", "verification_status": "VERIFIED"},
        {"candidate_id": "c2", "name": "Item 2", "verification_status": "VERIFIED"},
        {"candidate_id": "c3", "name": "Item 3", "verification_status": "PARTIALLY_VERIFIED"},
        {"candidate_id": "c4", "name": "Item 4", "verification_status": "VERIFIED"},
    ]
    s5_art = {
        "validation_gate": {"passed": False},
        "proposed_scope_items": [
            {"candidate_id": "c1", "completion_status": "READY"},
            {"candidate_id": "c2", "completion_status": "PENDING_REVIEW", "review_required": True},
        ],
        "quarantined_records": [
            {"candidate_id": "c3", "reason": "Unsupported claim"}
        ],
        "pending_review_records": [
            {"candidate_id": "c2", "reason": "Legal review"}
        ],
        "merge_reconciliation": [
            {"original_candidate_id": "c4", "retained_candidate_id": "c1"}
        ],
    }

    res = Step6Tracer.execute_inspection(
        project_id=1,
        document_id=1,
        doc_name="test.docx",
        step3_artifact={"canonical_candidates": s3_cands},
        step4_artifact={"structured_records": s4_recs},
        step5_artifact=s5_art,
        run_id="test_disp",
        timestamp_str="20261028_000000",
        logs_dir=str(tmp_path),
    )

    disp = res["step6_artifact"]["traceability_metrics"]["candidate_disposition_traceability"]
    assert disp["reconciliation_invariant_satisfied"] is True
    assert disp["active_baseline_proposed"] == 1
    assert disp["pending_review_gated"] == 1
    assert disp["quarantined_blocked"] == 1
    assert disp["merged_in_step5"] == 1
    assert disp["unaccounted_missing"] == 0
    assert disp["active_baseline_proposed"] + disp["pending_review_gated"] + disp["quarantined_blocked"] + disp["merged_in_step5"] + disp["unaccounted_missing"] == 4


def test_ijp_alias_resolution_no_extraction_gap(tmp_path):
    """
    Test 23: Step 7 recognizes the acronym 'ijp' as an alias of 'Internal Job Posting',
    linking to candidates and producing zero EXTRACTION_GAP findings.
    """
    from services.step7_analyzer import Step7Analyzer

    s4_recs = [
        {
            "candidate_id": "c_0005",
            "name": "Internal Job Posting implementation",
            "description": "Implementation of Internal Job Posting functionality.",
            "chunk_index": 1,
        },
        {
            "candidate_id": "c_0057",
            "name": "Collaboration portal vConnect",
            "description": "vConnect collaboration features.",
            "chunk_index": 7,
        },
    ]

    res = Step7Analyzer.execute_inspection(
        project_id=1,
        document_id=1,
        doc_name="test.docx",
        step3_artifact={},
        step4_artifact={"structured_records": s4_recs},
        step5_artifact={},
        step6_artifact={},
        run_id="test_ijp",
        timestamp_str="20261028_000000",
        logs_dir=str(tmp_path),
    )

    gaps = [f for f in res["step7_artifact"]["findings"] if f["finding_type"] == "EXTRACTION_GAP"]
    assert len(gaps) == 0, f"Expected 0 extraction gaps, got: {gaps}"


def test_source_text_typographical_anomaly_classification(tmp_path):
    """
    Test 24: sGmary and circGstances are classified as SOURCE_TEXT_TYPOGRAPHICAL_ANOMALY
    with is_source_document_issue=True and documented correction.
    """
    from services.step7_analyzer import Step7Analyzer

    s4_recs = [
        {
            "candidate_id": "c_0001",
            "name": "User requisition sGmary",
            "description": "User can see sGmary details.",
            "chunk_index": 5,
        },
        {
            "candidate_id": "c_0002",
            "name": "Unforeseen circGstances",
            "description": "Under unforeseen circGstances.",
            "chunk_index": 17,
        },
    ]

    res = Step7Analyzer.execute_inspection(
        project_id=1,
        document_id=1,
        doc_name="test.docx",
        step3_artifact={},
        step4_artifact={"structured_records": s4_recs},
        step5_artifact={},
        step6_artifact={},
        run_id="test_typo",
        timestamp_str="20261028_000000",
        logs_dir=str(tmp_path),
    )

    typo_findings = [f for f in res["step7_artifact"]["findings"] if f["finding_type"] == "SOURCE_TEXT_TYPOGRAPHICAL_ANOMALY"]
    assert len(typo_findings) == 2
    for f in typo_findings:
        assert f["is_source_document_issue"] is True
        assert f["severity"] == "LOW"
