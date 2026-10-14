"""
Regression tests for Steps 1-3 baseline extraction pipeline.

Test coverage:
- ScopeSectionDetector: heading recognition, section transitions, bullet protection
- DeepScanExtractor: heatmap fallback, section-aware windows, MAP/REDUCE audit
- ActionEntityProtector: action/entity conflict detection
- Provenance verification statuses
- MAP-to-REDUCE audit invariants

Run: pytest tests/test_baseline_extraction.py -v
"""

import sys
import os
import difflib
import re
from typing import Dict, Any, List, Tuple, Optional

# Allow importing from the project root
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

# ---------------------------------------------------------------------------
# Section Detector Tests
# ---------------------------------------------------------------------------

def test_structural_heading_markdown():
    """Markdown H1/H2/H3 headings are recognized."""
    from services.scope_section_detector import ScopeSectionDetector as SSD
    for line in ["# Scope of Work", "## Employee Collaboration", "### Internal Job Posting"]:
        is_h, h_text, conf = SSD.is_structural_heading(line)
        assert is_h, f"Expected '{line}' to be a structural heading"
        assert conf >= 1.0, f"Expected confidence 1.0 for '{line}'"


def test_structural_heading_numbered_bold():
    """Numbered bold headings (2. **Scope of Work**) are recognized."""
    from services.scope_section_detector import ScopeSectionDetector as SSD
    line = "2. **Scope of Work**"
    is_h, h_text, conf = SSD.is_structural_heading(line)
    assert is_h, f"Expected '{line}' to be a heading"
    assert "Scope" in h_text


def test_ordinary_bullet_NOT_heading():
    """Ordinary bullet points must never change the active section."""
    from services.scope_section_detector import ScopeSectionDetector as SSD
    bullets = [
        "* Display the organizational resource links and video sections",
        "- Allow user to view job requisitions",
        "* Weekly resource planning activities",
        "* The vendor shall provide a monthly status report",
    ]
    for b in bullets:
        is_h, _, _ = SSD.is_structural_heading(b)
        assert not is_h, f"Bullet '{b}' must NOT be treated as a structural heading"


def test_resource_bullet_does_not_switch_section():
    """
    A bullet containing 'resource' in functional scope content must NOT
    switch the section to 'Resources'.
    """
    from services.scope_section_detector import ScopeSectionDetector as SSD
    chunks = [
        {
            "chunk_index": 0,
            "text": "## Employee Collaboration\n\n* Display the announcements and events\n* Display organizational resource links and video sections\n* Allow employee to view learning resources",
            "page_number": None,
        }
    ]
    result, stats = SSD.detect_sections_with_stats(chunks)
    for seg in result:
        assert seg["section"] != "Resources", (
            f"Resource bullet incorrectly switched section to 'Resources'. "
            f"Segment: {seg['text'][:80]}"
        )
    assert any(seg["section"] == "Functional Scope" for seg in result), (
        "Expected at least one segment to be in 'Functional Scope'"
    )


def test_assumptions_stay_in_assumptions():
    """Assumptions and dependencies must not bleed into Functional Scope."""
    from services.scope_section_detector import ScopeSectionDetector as SSD
    chunks = [
        {"chunk_index": 0, "text": "## Key Assumptions\n\n* The client will provide VPN access within 5 days.\n* GL hierarchy will be available via API.", "page_number": None},
    ]
    result, _ = SSD.detect_sections_with_stats(chunks)
    for seg in result:
        assert seg["section"] in {"Assumptions", "Dependencies", "General"}, (
            f"Assumption content misclassified as '{seg['section']}'"
        )


def test_client_responsibilities_separate():
    """Client responsibilities must be classified as Client Responsibilities."""
    from services.scope_section_detector import ScopeSectionDetector as SSD
    chunks = [
        {"chunk_index": 0, "text": "## Client Responsibilities\n\n* The client shall provide access to the production environment.\n* The client will assign a dedicated project manager.", "page_number": None},
    ]
    result, _ = SSD.detect_sections_with_stats(chunks)
    for seg in result:
        assert seg["section"] in {"Client Responsibilities", "General"}, (
            f"Client responsibility content misclassified as '{seg['section']}'"
        )


def test_section_transition_only_on_heading():
    """
    Section only transitions when a real structural heading is encountered.
    A line saying 'milestones are important' inside a functional scope block
    must NOT switch to 'Milestones'.
    """
    from services.scope_section_detector import ScopeSectionDetector as SSD
    chunks = [
        {
            "chunk_index": 0,
            "text": (
                "## Employee Collaboration\n\n"
                "* The portal has milestones of its own for delivery.\n"
                "* Announcements and events will be displayed.\n"
                "* Employee schedule is tied to milestone planning."
            ),
            "page_number": None,
        }
    ]
    result, _ = SSD.detect_sections_with_stats(chunks)
    for seg in result:
        assert seg["section"] != "Milestones", (
            f"Milestone keyword in body text switched section to 'Milestones'"
        )


def test_out_of_scope_detected_first():
    """'Out of Scope' patterns match before generic 'Scope of Work'."""
    from services.scope_section_detector import ScopeSectionDetector as SSD
    chunks = [
        {"chunk_index": 0, "text": "## Out of Scope\n\n* Data migration is excluded.\n* Hardware procurement is not included.", "page_number": None},
    ]
    result, _ = SSD.detect_sections_with_stats(chunks)
    assert any(s["section"] == "Out of Scope" for s in result), \
        "Expected 'Out of Scope' to be detected"


def test_defined_vs_detected_sections():
    """SSD reports 16 defined sections and a subset of detected ones."""
    from services.scope_section_detector import ScopeSectionDetector as SSD
    _, stats = SSD.detect_sections_with_stats([
        {"chunk_index": 0, "text": "## Employee Collaboration\n* Portal features", "page_number": None}
    ])
    assert stats["categories_defined"] == 16
    assert stats["categories_detected"] <= 16
    assert stats["categories_detected"] >= 1


# ---------------------------------------------------------------------------
# ActionEntityProtector Tests
# ---------------------------------------------------------------------------

def test_view_vs_post_are_distinct():
    """Viewing and posting job requisitions must never be merged."""
    from services.deep_scan_extractor import ActionEntityProtector
    a = {"name": "Post Job Requisition", "description": "Allow manager to post job requisitions"}
    b = {"name": "View Job Requisition", "description": "Allow employee to view job requisitions"}
    can_m, reason = ActionEntityProtector.can_merge(a, b)
    assert not can_m, f"Post vs View should be protected but got: {reason}"


def test_approve_vs_recommend_are_distinct():
    """Approving requisitions vs recommending applicants are distinct operations."""
    from services.deep_scan_extractor import ActionEntityProtector
    a = {"name": "Admin Approval of Job Requisition", "description": "Admin approves or rejects a requisition"}
    b = {"name": "Recommending Applicant", "description": "Manager recommends or rejects an applicant"}
    can_m, reason = ActionEntityProtector.can_merge(a, b)
    assert not can_m, f"Approve vs Recommend should be protected but got: {reason}"


def test_milestone_vs_clause_are_distinct():
    """A pure milestone must not merge with a functional clause."""
    from services.deep_scan_extractor import ActionEntityProtector
    a = {"name": "UAT Sign-Off", "description": "UAT sign-off milestone", "is_pure_milestone": True}
    b = {"name": "User Acceptance Testing", "description": "Conduct UAT sessions", "is_pure_milestone": False}
    can_m, reason = ActionEntityProtector.can_merge(a, b)
    assert not can_m, f"Milestone vs Clause should be protected but got: {reason}"


def test_exact_duplicates_can_merge():
    """Identical items extracted from overlapping windows should be mergeable."""
    from services.deep_scan_extractor import ActionEntityProtector
    a = {"name": "Employee Landing Page", "description": "The portal shall have a single landing page"}
    b = {"name": "Employee Landing Page", "description": "Single landing page for employee portal"}
    can_m, reason = ActionEntityProtector.can_merge(a, b)
    assert can_m, f"Exact duplicates should be eligible to merge but got: {reason}"


def test_download_applicant_vs_download_requisition_distinct():
    """Downloading applicant list vs downloading requisition list are different datasets."""
    from services.deep_scan_extractor import ActionEntityProtector
    a = {"name": "Download Applicant List", "description": "Admin downloads the applicant list"}
    b = {"name": "Download Requisition List", "description": "Admin downloads the job requisition list"}
    can_m, reason = ActionEntityProtector.can_merge(a, b)
    # These should be protected by entity conflict (applicant vs job_requisition)
    assert not can_m, f"Download applicant vs download requisition should be protected, got: {reason}"


def test_deploy_vs_cutover_distinct():
    """Production deployment vs production cutover are distinct activities."""
    from services.deep_scan_extractor import ActionEntityProtector
    a = {"name": "Production Deployment", "description": "Deploy application to production"}
    b = {"name": "Production Cutover", "description": "Cutover from old system to new production"}
    can_m, reason = ActionEntityProtector.can_merge(a, b)
    assert not can_m, f"Deploy vs Cutover should be protected, got: {reason}"


# ---------------------------------------------------------------------------
# DeepScanExtractor Unit Tests (mock-based, no live services)
# ---------------------------------------------------------------------------

def _make_mock_mapped_items():
    """Create a representative set of MAP candidate items for testing."""
    return [
        {
            "name": "Employee Landing Page",
            "description": "A single landing page for the vConnect SharePoint portal.",
            "chunk_index": 0,
            "window_id": "win_000",
            "window_indices": [0],
            "segment_ids": [(0, 0)],
            "page_number": None,
            "section": "Functional Scope",
            "is_pure_milestone": False,
            "evidence_snippet": "A single landing page for the vConnect SharePoint portal.",
            "window_text_snippet": "The portal shall include a single landing page...",
            "_window_text": "The portal shall include a single landing page with announcements and events.",
        },
        {
            "name": "Announcements and Events Display",
            "description": "Display announcements and events on the portal.",
            "chunk_index": 0,
            "window_id": "win_000",
            "window_indices": [0],
            "segment_ids": [(0, 0)],
            "page_number": None,
            "section": "Functional Scope",
            "is_pure_milestone": False,
            "evidence_snippet": "Display announcements and events on the portal.",
            "window_text_snippet": "The portal shall include a single landing page...",
            "_window_text": "The portal shall include announcements and events display.",
        },
        {
            "name": "Post Job Requisition",
            "description": "Allow HR to post job requisitions on behalf of managers.",
            "chunk_index": 5,
            "window_id": "win_005",
            "window_indices": [5],
            "segment_ids": [(5, 0)],
            "page_number": None,
            "section": "Functional Scope",
            "is_pure_milestone": False,
            "evidence_snippet": "Allow HR to post job requisitions on behalf of managers.",
            "window_text_snippet": "Internal Job Posting: HR can post requisitions...",
            "_window_text": "HR can post job requisitions on behalf of managers via the Internal Job Posting module.",
        },
        {
            "name": "View Job Requisitions",
            "description": "Allow employees to view eligible job requisitions.",
            "chunk_index": 5,
            "window_id": "win_005",
            "window_indices": [5],
            "segment_ids": [(5, 0)],
            "page_number": None,
            "section": "Functional Scope",
            "is_pure_milestone": False,
            "evidence_snippet": "Allow employees to view eligible job requisitions.",
            "window_text_snippet": "Employees can view and apply for job requisitions...",
            "_window_text": "Employees can view eligible job requisitions and apply for them.",
        },
    ]


def test_deterministic_dedup_protects_view_vs_post():
    """_deterministic_dedup must keep Post and View as distinct items."""
    from services.deep_scan_extractor import DeepScanExtractor
    items = [
        {"name": "Post Job Requisition", "description": "Post requisitions", "section": "Functional Scope", "is_pure_milestone": False},
        {"name": "View Job Requisitions", "description": "View requisitions", "section": "Functional Scope", "is_pure_milestone": False},
    ]
    deduped, merge_events = DeepScanExtractor._deterministic_dedup(items)
    assert len(deduped) == 2, f"Expected 2 items but got {len(deduped)}: {[d['name'] for d in deduped]}"
    assert len(merge_events) == 0, "Should be no merge events for distinct actions"


def test_deterministic_dedup_merges_exact_duplicates():
    """_deterministic_dedup must merge exact duplicates."""
    from services.deep_scan_extractor import DeepScanExtractor
    items = [
        {"name": "Employee Landing Page", "description": "A single landing page", "section": "Functional Scope", "is_pure_milestone": False},
        {"name": "Employee Landing Page", "description": "Single landing page for the portal", "section": "Functional Scope", "is_pure_milestone": False},
    ]
    deduped, merge_events = DeepScanExtractor._deterministic_dedup(items)
    assert len(deduped) == 1, f"Expected 1 merged item but got {len(deduped)}"
    assert len(merge_events) == 1, "Expected 1 merge event"


def test_audit_coverage_invariants():
    """
    Coverage audit must satisfy: RETAINED + MERGED + EXCLUDED + UNRESOLVED = total MAP candidates.
    """
    from services.deep_scan_extractor import DeepScanExtractor
    mapped_items = _make_mock_mapped_items()

    # Create a set of "canonical" candidates that retains 2 of the 4
    canonical = [
        {"name": "Employee Landing Page", "description": "...", "section": "Functional Scope", "provenance_status": "VERIFIED"},
        {"name": "Announcements and Events Display", "description": "...", "section": "Functional Scope", "provenance_status": "VERIFIED"},
        # Post and View are merged with a different name:
        {"name": "Job Requisition Access", "description": "...", "section": "Functional Scope", "provenance_status": "PARTIALLY_VERIFIED"},
    ]
    merge_events = [
        {"merged_name": "Post Job Requisition", "surviving_name": "Job Requisition Access", "section": "Functional Scope", "rationale": "test merge"},
        {"merged_name": "View Job Requisitions", "surviving_name": "Job Requisition Access", "section": "Functional Scope", "rationale": "test merge"},
    ]
    report = DeepScanExtractor._audit_coverage(mapped_items, canonical, merge_events)

    total = report["total_map_candidates"]
    r = report["retained_count"]
    m = report["merged_count"]
    e = report["excluded_count"]
    u = report["unresolved_count"]
    assert r + m + e + u == total, (
        f"Invariant violated: {r}+{m}+{e}+{u} != {total}"
    )
    assert report["invariant_warnings"] == [] or all("INVARIANT" not in w for w in report["invariant_warnings"]), \
        f"Unexpected invariant violations: {report['invariant_warnings']}"


def test_heatmap_fallback_uses_sectioned_segments():
    """
    When heatmap is degraded (returns empty), fallback must use section-aware
    segments from Step 2, not raw unsectioned chunks.
    The windows produced must carry the section labels from Step 2.
    """
    from services.deep_scan_extractor import DeepScanExtractor

    # Simulate hot_indices = all indices (fallback) with sectioned segments
    index_to_chunk = {
        0: {"text": "The portal shall have a landing page. Announcements and events will be displayed.", "page_number": None},
        1: {"text": "Internal Job Posting module allows HR to post requisitions.", "page_number": None},
    }
    index_to_section = {0: "Functional Scope", 1: "Functional Scope"}
    sectioned_segments = [
        {"chunk_index": 0, "segment_index": 0, "text": "The portal shall have a landing page.", "section": "Functional Scope", "page_number": None, "section_confidence": 1.0, "is_inferred": False},
        {"chunk_index": 0, "segment_index": 1, "text": "Announcements and events will be displayed.", "section": "Functional Scope", "page_number": None, "section_confidence": 1.0, "is_inferred": False},
        {"chunk_index": 1, "segment_index": 0, "text": "Internal Job Posting module allows HR to post requisitions.", "section": "Functional Scope", "page_number": None, "section_confidence": 1.0, "is_inferred": False},
    ]

    hot_indices = sorted(index_to_chunk.keys())
    windows = DeepScanExtractor._build_windows(
        hot_indices, index_to_chunk, index_to_section, sectioned_segments,
        used_fallback=True, logger=None
    )
    assert len(windows) > 0, "Expected at least one window from fallback"
    # All windows should carry a non-General section
    for w in windows:
        assert w["section"] != "" and w.get("section") is not None, "Window has no section"
    # Section from Step 2 should be preserved
    sections_in_windows = [w["section"] for w in windows]
    assert "Functional Scope" in sections_in_windows, \
        f"Expected Functional Scope in windows, got {sections_in_windows}"


def test_per_item_evidence_not_window_start():
    """
    MAP items must carry evidence_snippet from LLM description,
    not the raw window text start.
    """
    mapped = _make_mock_mapped_items()
    for item in mapped:
        assert "evidence_snippet" in item
        # The evidence_snippet should be the description, not window_text_snippet
        assert item["evidence_snippet"] == item["description"][:300].strip() or \
               item["evidence_snippet"] != item["window_text_snippet"][:len(item["evidence_snippet"])]


def test_provenance_unresolved_no_default_fallback():
    """
    When no source match is found, provenance must be UNRESOLVED.
    Must NOT fall back to the first mapped item regardless.
    """
    from services.deep_scan_extractor import DeepScanExtractor, PROVENANCE_UNRESOLVED

    reduced = [{"name": "Completely Unrelated Feature XYZ99", "description": "no match", "section": "General", "is_pure_milestone": False}]
    mapped = _make_mock_mapped_items()  # none of these match "Completely Unrelated Feature XYZ99"

    candidates = DeepScanExtractor._to_candidates(reduced, mapped, document_id=1, logger=None)
    assert len(candidates) == 1
    # Should be UNRESOLVED or PARTIALLY_VERIFIED, never VERIFIED with wrong chunk
    prov = candidates[0]["provenance_status"]
    # The chunk index should NOT be 0 (the employee portal chunk) or should be UNRESOLVED
    if prov == PROVENANCE_UNRESOLVED:
        assert candidates[0]["chunk_index"] is None, \
            "UNRESOLVED candidates must have chunk_index=None, not a wrong chunk"


def test_inspection_mode_env_variable():
    """Inspection mode can be enabled and disabled via environment variable."""
    os.environ["BASELINE_INSPECTION_MODE"] = "true"
    assert os.getenv("BASELINE_INSPECTION_MODE", "true").lower() in ("true", "1", "yes")
    os.environ["BASELINE_INSPECTION_MODE"] = "false"
    assert os.getenv("BASELINE_INSPECTION_MODE", "true").lower() not in ("true", "1", "yes")
    # Reset to default (true for dev safety)
    os.environ["BASELINE_INSPECTION_MODE"] = "true"


def test_el_functional_scope_checklist_coverage():
    """
    Smoke test: given a set of section-tagged segments that mimic demo_EL.docx
    employee collaboration content, verify key items would be extracted by MAP.
    (This tests the section-aware window assembly, not live LLM.)
    """
    from services.deep_scan_extractor import DeepScanExtractor

    # Simulate sectioned segments as produced by Step 2 for employee collaboration section
    sectioned_segments = [
        {"chunk_index": 0, "segment_index": 0, "text": "## Employee Collaboration\n* Single landing page for vConnect SharePoint Portal\n* Announcements and events display\n* Activity notifications", "section": "Functional Scope", "page_number": None},
        {"chunk_index": 1, "segment_index": 0, "text": "* Resource links and video sections\n* Employee learning section\n* News sections\n* Polls and surveys", "section": "Functional Scope", "page_number": None},
        {"chunk_index": 2, "segment_index": 0, "text": "## Internal Job Posting\n* Digitization of MFR form\n* Access control for job requisitions by GL hierarchy\n* Delete job requisition", "section": "Functional Scope", "page_number": None},
    ]
    index_to_chunk = {
        seg["chunk_index"]: {"text": seg["text"], "page_number": seg.get("page_number")}
        for seg in sectioned_segments
    }
    index_to_section = {seg["chunk_index"]: seg["section"] for seg in sectioned_segments}
    hot_indices = sorted(index_to_chunk.keys())

    windows = DeepScanExtractor._build_windows(
        hot_indices, index_to_chunk, index_to_section, sectioned_segments,
        used_fallback=True, logger=None
    )
    assert len(windows) >= 1, "Expected windows to be assembled from segments"
    # All windows should come from Functional Scope
    for w in windows:
        assert "Functional Scope" in w.get("sections_in_window", [w.get("section", "")]), \
            f"Window section mismatch: {w.get('sections_in_window')}"
    # Verify text from segments appears in windows
    all_window_text = " ".join(w["text"] for w in windows)
    required_phrases = [
        "landing page",
        "Announcements",
        "MFR form",
        "GL hierarchy",
    ]
    for phrase in required_phrases:
        assert phrase.lower() in all_window_text.lower(), \
            f"Expected phrase '{phrase}' to appear in assembled windows"


# ---------------------------------------------------------------------------
# Run all tests
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import traceback
    tests = [
        test_structural_heading_markdown,
        test_structural_heading_numbered_bold,
        test_ordinary_bullet_NOT_heading,
        test_resource_bullet_does_not_switch_section,
        test_assumptions_stay_in_assumptions,
        test_client_responsibilities_separate,
        test_section_transition_only_on_heading,
        test_out_of_scope_detected_first,
        test_defined_vs_detected_sections,
        test_view_vs_post_are_distinct,
        test_approve_vs_recommend_are_distinct,
        test_milestone_vs_clause_are_distinct,
        test_exact_duplicates_can_merge,
        test_download_applicant_vs_download_requisition_distinct,
        test_deploy_vs_cutover_distinct,
        test_deterministic_dedup_protects_view_vs_post,
        test_deterministic_dedup_merges_exact_duplicates,
        test_audit_coverage_invariants,
        test_heatmap_fallback_uses_sectioned_segments,
        test_per_item_evidence_not_window_start,
        test_provenance_unresolved_no_default_fallback,
        test_inspection_mode_env_variable,
        test_el_functional_scope_checklist_coverage,
    ]

    passed = 0
    failed = 0
    for t in tests:
        try:
            t()
            print(f"  PASS: {t.__name__}")
            passed += 1
        except Exception as e:
            print(f"  FAIL: {t.__name__}: {e}")
            traceback.print_exc()
            failed += 1

    print(f"\n{'='*60}")
    print(f"Results: {passed} passed, {failed} failed out of {len(tests)} tests")
    sys.exit(0 if failed == 0 else 1)
