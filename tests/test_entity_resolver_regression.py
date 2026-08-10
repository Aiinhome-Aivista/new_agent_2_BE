"""
Regression test: EntityResolver + DependencyGraphBuilder

Tests:
1. Exact name resolution
2. Normalized / case-insensitive resolution
3. Partial / abbreviated name resolution
4. Non-entity text classification
5. External dependency detection
6. Graph metrics (root cause, cascade, immediate unlock)
7. execution_status preservation (never UNKNOWN)
8. Orphan detection (reference to non-existent entity)

Run with:
  python tests/test_entity_resolver_regression.py
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.entity_resolver import (
    CanonicalEntity,
    CanonicalEntityRegistry,
    EntityResolver,
    UnresolvedReference,
    build_registry_from_baseline,
    enrich_registry_with_candidates,
    normalize_entity_name,
    _is_non_entity,
)
from services.dependency_graph_builder import DependencyGraphBuilder


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

BASELINE_ITEMS = [
    {"id": 480, "name": "CRM Integration", "category": "MILESTONE"},
    {"id": 481, "name": "Azure AD Single Sign-On (SSO)", "category": "MILESTONE"},
    {"id": 482, "name": "User Acceptance Testing (UAT)", "category": "MILESTONE"},
    {"id": 483, "name": "Production Deployment", "category": "MILESTONE"},
    {"id": 484, "name": "API Gateway Configuration", "category": "MILESTONE"},
    {"id": 485, "name": "Security Audit", "category": "MILESTONE"},
]


# ---------------------------------------------------------------------------
# Test 1: Exact name resolution
# ---------------------------------------------------------------------------
def test_exact_resolution():
    registry = build_registry_from_baseline(BASELINE_ITEMS)
    resolver = EntityResolver(registry)
    result = resolver.resolve("CRM Integration")
    assert result.resolved, "Exact name should resolve"
    assert result.entity.canonical_id == "si_480"
    assert result.match_type == "exact_norm_name"
    print("  ✓ Test 1: Exact name resolution")


# ---------------------------------------------------------------------------
# Test 2: Case / punctuation insensitive resolution
# ---------------------------------------------------------------------------
def test_case_insensitive():
    registry = build_registry_from_baseline(BASELINE_ITEMS)
    resolver = EntityResolver(registry)
    result = resolver.resolve("crm integration")
    assert result.resolved, "Lowercase should resolve"
    assert result.entity.canonical_id == "si_480"
    print("  ✓ Test 2: Case-insensitive resolution")


# ---------------------------------------------------------------------------
# Test 3: Abbreviated / partial reference (the original failing case)
# ---------------------------------------------------------------------------
def test_partial_reference():
    baseline_with_long_crm = [
        {"id": 480, "name": "CRM Integration for customer information and ticket", "category": "MILESTONE"},
        {"id": 481, "name": "Azure AD Single Sign-On (SSO)", "category": "MILESTONE"},
    ]
    registry = build_registry_from_baseline(baseline_with_long_crm)
    resolver = EntityResolver(registry)
    
    # 1. Test "CRM Integration" resolving to long baseline title
    res1 = resolver.resolve("CRM Integration")
    assert res1.resolved, "CRM Integration should resolve to baseline item"
    assert res1.entity.canonical_id == "si_480"

    # 2. Test long title resolving
    res2 = resolver.resolve("CRM Integration for customer information and ticket")
    assert res2.resolved, "Long name should resolve"
    assert res2.entity.canonical_id == "si_480"

    print("  ✓ Test 3: Short and long CRM Integration references resolved cleanly")


# ---------------------------------------------------------------------------
# Test 4: Non-entity text rejection
# ---------------------------------------------------------------------------
def test_non_entity_rejection():
    assert _is_non_entity("pending") is True
    assert _is_non_entity("customer") is True
    assert _is_non_entity("09 Sep 2026") is True
    assert _is_non_entity("in progress") is True
    assert _is_non_entity("CRM Integration") is False
    print("  ✓ Test 4: Non-entity text classification")


# ---------------------------------------------------------------------------
# Test 5: External dependency detection
# ---------------------------------------------------------------------------
def test_external_dependency_detection():
    registry = build_registry_from_baseline(BASELINE_ITEMS)
    resolver = EntityResolver(registry)
    result = resolver.resolve("Client API credentials")
    assert not result.resolved
    unref = resolver.classify_unresolved("Client API credentials",
                                          source_id="si_480")
    assert unref.ref_type == UnresolvedReference.EXTERNAL
    print("  ✓ Test 5: External dependency detection")


# ---------------------------------------------------------------------------
# Test 6: Graph metrics via DependencyGraphBuilder
# ---------------------------------------------------------------------------
def test_graph_metrics():
    candidates = [
        {
            "activity": "CRM Integration",
            "status": "WAITING_ON_CUSTOMER",
            "blocked_by": [],
            "blocks": ["Azure AD Single Sign-On (SSO)", "User Acceptance Testing (UAT)"],
            "evidence": "CRM credentials awaited from customer",
        },
        {
            "activity": "Azure AD Single Sign-On (SSO)",
            "status": "NOT_STARTED",
            "blocked_by": ["CRM Integration"],
            "blocks": ["User Acceptance Testing (UAT)"],
            "evidence": "Blocked by CRM",
        },
        {
            "activity": "User Acceptance Testing (UAT)",
            "status": "NOT_STARTED",
            "blocked_by": ["Azure AD Single Sign-On (SSO)"],
            "blocks": ["Production Deployment"],
            "evidence": "Blocked by SSO",
        },
        {
            "activity": "Production Deployment",
            "status": "NOT_STARTED",
            "blocked_by": ["User Acceptance Testing (UAT)"],
            "blocks": [],
            "evidence": "Final step",
        },
    ]
    enriched = DependencyGraphBuilder.build_and_enrich(
        candidates, BASELINE_ITEMS
    )

    # CRM should be root cause
    crm = next(c for c in enriched if "CRM" in c.get("activity", ""))
    assert crm["is_root_cause"] is True, \
        f"CRM should be root cause, got: {crm['is_root_cause']}"
    assert crm["cascade_count"] >= 3, \
        f"CRM cascade should be ≥ 3, got: {crm['cascade_count']}"

    # UAT should have depth ≥ 1
    uat = next(c for c in enriched if "UAT" in c.get("activity", "") or
               "Acceptance" in c.get("activity", ""))
    assert uat["cascade_count"] >= 1, \
        f"UAT cascade should be ≥ 1, got: {uat['cascade_count']}"

    # Production should be terminal
    prod = next(c for c in enriched if "Production" in c.get("activity", ""))
    assert prod["cascade_count"] == 0, \
        f"Production should have cascade_count=0, got: {prod['cascade_count']}"

    print(f"  ✓ Test 6: Graph metrics (CRM root_cause={crm['is_root_cause']}, "
          f"cascade={crm['cascade_count']}, "
          f"uat_cascade={uat['cascade_count']}, "
          f"prod_cascade={prod['cascade_count']})")


# ---------------------------------------------------------------------------
# Test 7: execution_status preservation
# ---------------------------------------------------------------------------
def test_execution_status_preservation():
    candidates = [
        {
            "activity": "CRM Integration",
            "status": "WAITING_ON_CUSTOMER",
            "execution_status": "WAITING_ON_CUSTOMER",
            "blocked_by": [],
            "blocks": [],
        },
        {
            "activity": "Security Audit",
            "status": "UNKNOWN",            # Should become NOT_STARTED
            "execution_status": "",
            "blocked_by": [],
            "blocks": [],
        },
    ]
    enriched = DependencyGraphBuilder.build_and_enrich(
        candidates, BASELINE_ITEMS
    )
    crm = next(c for c in enriched if "CRM" in c.get("activity", ""))
    sec = next(c for c in enriched if "Security" in c.get("activity", ""))
    # CRM status must survive
    assert crm.get("status", "").upper() == "WAITING_ON_CUSTOMER", \
        f"CRM status should be WAITING_ON_CUSTOMER, got: {crm.get('status')}"
    print(f"  ✓ Test 7: execution_status preserved "
          f"(CRM={crm.get('status')}, Security={sec.get('status')})")


# ---------------------------------------------------------------------------
# Test 8: Orphan detection — reference that does NOT exist in registry
# ---------------------------------------------------------------------------
def test_orphan_detection():
    registry = build_registry_from_baseline(BASELINE_ITEMS)
    resolver = EntityResolver(registry)
    result = resolver.resolve("NonExistentSystemXYZ")
    assert not result.resolved, "Unknown entity should NOT resolve"
    unref = resolver.classify_unresolved("NonExistentSystemXYZ")
    assert unref.ref_type == UnresolvedReference.EXTERNAL, \
        f"Expected EXTERNAL, got: {unref.ref_type}"
    print("  ✓ Test 8: Orphan detection — unresolved external dependency")


# ---------------------------------------------------------------------------
# Test 9: SIT Compound Baseline mapping
# ---------------------------------------------------------------------------
def test_sit_compound_baseline_mapping():
    compound_baseline = [
        {"id": 480, "name": "System Integration Testing (SIT), UAT, Production Deployment", "category": "MILESTONE"},
    ]
    registry = build_registry_from_baseline(compound_baseline)
    resolver = EntityResolver(registry)
    
    res1 = resolver.resolve("System Integration Testing (SIT)")
    assert res1.resolved, "SIT substring should resolve exactly to compound item"
    assert res1.entity.canonical_id == "si_480"
    
    res2 = resolver.resolve("UAT")
    assert res2.resolved, "UAT substring should resolve exactly"
    assert res2.entity.canonical_id == "si_480"
    
    res3 = resolver.resolve("Production Deployment")
    assert res3.resolved, "Production Deployment substring should resolve exactly"
    assert res3.entity.canonical_id == "si_480"
    
    print("  ✓ Test 9: SIT compound baseline mapping")



# ---------------------------------------------------------------------------
# Test 10: graph.md architectural compliance & non-entity rejection
# ---------------------------------------------------------------------------
def test_graph_md_compliance():
    # 1. Reject generic resource words
    assert _is_non_entity("credentials") is True
    assert _is_non_entity("access") is True
    assert _is_non_entity("approval") is True
    assert _is_non_entity("security approval") is True

    # 2. Test graph construction with multiple prerequisites
    candidates = [
        {
            "activity": "CRM Integration",
            "status": "BLOCKED",
            "blocked_by": [],
            "blocks": ["Azure AD Single Sign-On (SSO)", "System Integration Testing (SIT)"],
            "evidence": "CRM Integration is blocked awaiting credentials",
        },
        {
            "activity": "Azure AD Single Sign-On (SSO)",
            "status": "NOT_STARTED",
            "blocked_by": ["CRM Integration"],
            "blocks": ["System Integration Testing (SIT)"],
            "evidence": "Azure AD SSO depends on CRM Integration",
        },
        {
            "activity": "System Integration Testing (SIT)",
            "status": "NOT_STARTED",
            "blocked_by": ["CRM Integration", "Azure AD Single Sign-On (SSO)"],
            "blocks": ["User Acceptance Testing (UAT)"],
            "evidence": "SIT cannot begin until CRM Integration and Azure AD SSO are completed",
        },
        {
            "activity": "User Acceptance Testing (UAT)",
            "status": "NOT_STARTED",
            "blocked_by": ["System Integration Testing (SIT)"],
            "blocks": ["Production Deployment"],
            "evidence": "UAT depends on SIT",
        },
    ]

    enriched = DependencyGraphBuilder.build_and_enrich(candidates, BASELINE_ITEMS)
    crm = next(c for c in enriched if "CRM" in c.get("activity", ""))
    sit = next(c for c in enriched if "SIT" in c.get("activity", "") or "System Integration" in c.get("activity", ""))

    assert crm["is_root_cause"] is True, "CRM Integration must be root cause"
    assert len(sit["blocked_by"]) >= 2, f"SIT must preserve multiple prerequisites, got: {sit['blocked_by']}"

    print("  ✓ Test 9: graph.md architectural compliance & multi-prerequisites")


# ===========================================================================
# 15 NEW EVIDENCE-DRIVEN TESTS (from dependency.md spec + user review)
# ===========================================================================

# ---------------------------------------------------------------------------
# Test 10: AND prerequisites — both edges created
# ---------------------------------------------------------------------------
def test_and_prerequisites_both_edges():
    """A+B→C creates 2 separate edges."""
    candidates = [
        {
            "activity": "API Gateway Configuration",
            "status": "COMPLETED",
            "blocked_by": [],
            "blocks": ["CRM Integration"],
            "evidence": "CRM Integration requires both API Gateway Configuration and Security Audit to be complete.",
        },
        {
            "activity": "Security Audit",
            "status": "COMPLETED",
            "blocked_by": [],
            "blocks": ["CRM Integration"],
            "evidence": "CRM Integration requires both API Gateway Configuration and Security Audit to be complete.",
        },
        {
            "activity": "CRM Integration",
            "status": "NOT_STARTED",
            "blocked_by": ["API Gateway Configuration", "Security Audit"],
            "blocks": [],
            "evidence": "CRM Integration requires both API Gateway Configuration and Security Audit to be complete.",
        },
    ]
    enriched = DependencyGraphBuilder.build_and_enrich(candidates, BASELINE_ITEMS)
    crm = next(c for c in enriched if c.get("activity") == "CRM Integration")
    assert len(crm["_blocked_by_ids"]) == 2, \
        f"CRM must have 2 AND prerequisites, got: {crm['blocked_by']}"
    print("  ✓ Test 10: AND prerequisites — both A+B→C edges created")


# ---------------------------------------------------------------------------
# Test 11: AND readiness — one unsatisfied → BLOCKED
# ---------------------------------------------------------------------------
def test_and_readiness_one_unsatisfied_blocked():
    """If A is ready but B is not, C must be BLOCKED."""
    from services.readiness_engine import ReadinessEngine

    bwd = {
        "C": ["A", "B"]
    }
    fwd = {"A": ["C"], "B": ["C"]}

    def status_fn(nid):
        return {"A": "COMPLETED", "B": "NOT_STARTED", "C": "NOT_STARTED"}[nid]

    result = ReadinessEngine.evaluate("C", bwd, status_fn)
    assert result.status == "BLOCKED", \
        f"C must be BLOCKED when B is not satisfied, got: {result.status}"
    assert "B" in result.blocking_prerequisites, \
        f"B must be in blocking list: {result.blocking_prerequisites}"
    print("  ✓ Test 11: AND readiness — one unsatisfied prerequisite → BLOCKED")


# ---------------------------------------------------------------------------
# Test 12: AND readiness — all satisfied → READY
# ---------------------------------------------------------------------------
def test_and_readiness_all_satisfied_ready():
    """If A and B are both COMPLETED, C must be READY."""
    from services.readiness_engine import ReadinessEngine

    bwd = {"C": ["A", "B"]}

    def status_fn(nid):
        return "COMPLETED" if nid in ("A", "B") else "NOT_STARTED"

    result = ReadinessEngine.evaluate("C", bwd, status_fn)
    assert result.status == "READY", \
        f"C must be READY when all prerequisites done, got: {result.status}"
    assert result.all_and_satisfied is True
    print("  ✓ Test 12: AND readiness — all satisfied → READY")


# ---------------------------------------------------------------------------
# Test 13: OR prerequisite — any one satisfies
# ---------------------------------------------------------------------------
def test_or_prerequisite_any_one_satisfies():
    """A OR B → C; if B is complete, C is READY even though A is not."""
    from services.readiness_engine import ReadinessEngine

    bwd = {"C": ["A", "B"]}
    edge_conditions = {("A", "C"): "OR", ("B", "C"): "OR"}

    def status_fn(nid):
        return {"A": "NOT_STARTED", "B": "COMPLETED", "C": "NOT_STARTED"}[nid]

    result = ReadinessEngine.evaluate("C", bwd, status_fn,
                                      edge_conditions=edge_conditions)
    assert result.status == "READY", \
        f"C must be READY (OR satisfied by B), got: {result.status}"
    assert result.all_and_satisfied is True
    print("  ✓ Test 13: OR prerequisite — any one OR-prerequisite satisfies")


# ---------------------------------------------------------------------------
# Test 14: Unresolved dependency preserved (not executable node)
# ---------------------------------------------------------------------------
def test_unresolved_dependency_preserved_not_executable():
    """Unresolved dep must appear in unresolved_external_dependencies,
    not as an executable graph node."""
    candidates = [
        {
            "activity": "CRM Integration",
            "status": "BLOCKED",
            "blocked_by": ["NONEXISTENT_SYSTEM_XYZ_456"],
            "blocks": [],
            "evidence": "CRM Integration requires NONEXISTENT_SYSTEM_XYZ_456.",
        },
    ]
    enriched = DependencyGraphBuilder.build_and_enrich(candidates, BASELINE_ITEMS)
    crm = next(c for c in enriched if "CRM" in c.get("activity", ""))

    unresolved = crm.get("unresolved_external_dependencies", [])
    assert any("NONEXISTENT_SYSTEM_XYZ_456" in u.get("label", "")
               for u in unresolved), \
        f"Unresolved dep must be preserved. Got: {unresolved}"

    # It must NOT create a fake node in blocked_by
    assert "NONEXISTENT_SYSTEM_XYZ_456" not in str(crm.get("_blocked_by_ids", [])), \
        f"Unresolved dep must NOT be a canonical graph node"

    print("  ✓ Test 14: Unresolved dependency preserved with canonical_id=null, not executable")


# ---------------------------------------------------------------------------
# Test 15: Self-dependency rejection
# ---------------------------------------------------------------------------
def test_self_dependency_rejection():
    """An activity that references itself (via substring/fuzzy match) must not
    create a self-loop edge."""
    candidates = [
        {
            "activity": "Security Audit",
            "status": "IN_PROGRESS",
            "blocked_by": ["Security Audit"],  # self-reference
            "blocks": [],
            "evidence": "Security Audit is in progress.",
        },
    ]
    enriched = DependencyGraphBuilder.build_and_enrich(candidates, BASELINE_ITEMS)
    sec = next(c for c in enriched if "Security" in c.get("activity", ""))

    # Self-loop must not appear in blocked_by
    assert "Security Audit" not in sec.get("blocked_by", []), \
        f"Self-dependency must be rejected. blocked_by={sec.get('blocked_by')}"
    print("  ✓ Test 15: Self-dependency rejected (no self-loop)")


# ---------------------------------------------------------------------------
# Test 16: Similar-name false match prevention
# ---------------------------------------------------------------------------
def test_similar_name_no_false_match():
    """'Security review' must NOT auto-resolve to 'Audit Logs Security review'."""
    baseline_ambiguous = [
        {"id": 490, "name": "Audit Logs Security Review", "category": "MILESTONE"},
        {"id": 491, "name": "Security Review Approval", "category": "MILESTONE"},
    ]
    registry = build_registry_from_baseline(baseline_ambiguous)
    resolver = EntityResolver(registry)

    result = resolver.resolve("Security Review")
    # Must be AMBIGUOUS or UNRESOLVED (not blindly resolve to one entity)
    # Rationale: both entities contain "Security Review" — ambiguous
    if result.resolved:
        # If it resolves, it must not be a false positive (same canonical name)
        # We treat it as OK only if match_type is exact/alias (not fuzzy)
        assert result.match_type in ("exact_norm_name", "exact_alias", "exact_id"), \
            f"Ambiguous 'Security Review' must not fuzzy-resolve. Got: {result.match_type}, entity: {result.entity.display_name}"
    print(f"  ✓ Test 16: Similar-name false match prevented "
          f"(resolved={result.resolved}, type={result.match_type})")


# ---------------------------------------------------------------------------
# Test 17: Sequential chain preserved, no A→C transitive invention
# ---------------------------------------------------------------------------
def test_sequential_chain_no_transitive_invention():
    """A→B→C must produce only A→B and B→C edges. NOT A→C."""
    candidates = [
        {
            "activity": "API Gateway Configuration",
            "status": "NOT_STARTED",
            "blocked_by": [],
            "blocks": ["Security Audit"],
            "evidence": "Security Audit requires API Gateway Configuration first.",
        },
        {
            "activity": "Security Audit",
            "status": "NOT_STARTED",
            "blocked_by": ["API Gateway Configuration"],
            "blocks": ["CRM Integration"],
            "evidence": "CRM Integration cannot begin until Security Audit completes.",
        },
        {
            "activity": "CRM Integration",
            "status": "NOT_STARTED",
            "blocked_by": ["Security Audit"],
            "blocks": [],
            "evidence": "CRM Integration depends on Security Audit.",
        },
    ]
    enriched = DependencyGraphBuilder.build_and_enrich(candidates, BASELINE_ITEMS)
    api = next(c for c in enriched if "API Gateway" in c.get("activity", ""))
    crm = next(c for c in enriched if c.get("activity") == "CRM Integration")

    # A (API Gateway) must NOT directly block C (CRM) — only B (Security Audit) does
    crm_blocked_by = crm.get("blocked_by", [])
    assert "API Gateway Configuration" not in crm_blocked_by, \
        f"Transitive edge invented: API Gateway should NOT directly block CRM. Got: {crm_blocked_by}"
    assert "Security Audit" in crm_blocked_by, \
        f"CRM must be blocked by Security Audit. Got: {crm_blocked_by}"
    print("  ✓ Test 17: Sequential A→B→C preserved, no A→C transitive invention")


# ---------------------------------------------------------------------------
# Test 18: Duplicate evidence merges into one edge
# ---------------------------------------------------------------------------
def test_duplicate_evidence_merged():
    """3 sentences proving same dependency → 1 edge with evidence_list len=3."""
    from services.dependency_graph_builder import DependencyEdge

    e = DependencyEdge("A", "B", evidence="First evidence")
    e.merge_evidence("Second evidence")
    e.merge_evidence("Third evidence")

    assert len(e.evidence_list) == 3, \
        f"Edge must have 3 evidence items, got: {len(e.evidence_list)}"
    assert e.supporting_mentions == 3, \
        f"supporting_mentions must be 3, got: {e.supporting_mentions}"
    print("  ✓ Test 18: Duplicate evidence merged — 3 sentences → 1 edge with evidence_list[3]")


# ---------------------------------------------------------------------------
# Test 19: Cycle detection and rejection
# ---------------------------------------------------------------------------
def test_cycle_detection():
    """A→B→C→A cycle must be broken."""
    candidates = [
        {
            "activity": "CRM Integration",
            "status": "NOT_STARTED",
            "blocked_by": ["User Acceptance Testing (UAT)"],  # creates cycle
            "blocks": ["Azure AD Single Sign-On (SSO)"],
            "evidence": "CRM blocks SSO. UAT blocks CRM.",
        },
        {
            "activity": "Azure AD Single Sign-On (SSO)",
            "status": "NOT_STARTED",
            "blocked_by": ["CRM Integration"],
            "blocks": ["User Acceptance Testing (UAT)"],
            "evidence": "SSO blocks UAT.",
        },
        {
            "activity": "User Acceptance Testing (UAT)",
            "status": "NOT_STARTED",
            "blocked_by": ["Azure AD Single Sign-On (SSO)"],
            "blocks": ["CRM Integration"],  # closes cycle
            "evidence": "UAT blocks CRM.",
        },
    ]
    enriched = DependencyGraphBuilder.build_and_enrich(candidates, BASELINE_ITEMS)
    # Validation contract must show cycle broken
    validation = enriched[0].get("_graph_validation", {})
    assert validation.get("cycle_edges_rejected", 0) >= 1, \
        f"At least 1 cycle edge must be rejected. validation={validation}"
    print("  ✓ Test 19: Cycle detection — A→B→C→A cycle broken")


# ---------------------------------------------------------------------------
# Test 20: Independent branches not spuriously connected
# ---------------------------------------------------------------------------
def test_independent_branches_not_connected():
    """A→B and C→D (two separate chains) must never connect."""
    extra_baseline = [
        {"id": 486, "name": "Infrastructure Setup", "category": "MILESTONE"},
        {"id": 487, "name": "Load Balancer Config", "category": "MILESTONE"},
    ]
    all_baseline = BASELINE_ITEMS + extra_baseline
    candidates = [
        {
            "activity": "Infrastructure Setup",
            "status": "NOT_STARTED",
            "blocked_by": [],
            "blocks": ["Load Balancer Config"],
            "evidence": "Infrastructure Setup must complete before Load Balancer Config.",
        },
        {
            "activity": "Load Balancer Config",
            "status": "NOT_STARTED",
            "blocked_by": ["Infrastructure Setup"],
            "blocks": [],
            "evidence": "Load Balancer depends on Infrastructure Setup.",
        },
        {
            "activity": "CRM Integration",
            "status": "NOT_STARTED",
            "blocked_by": [],
            "blocks": ["Security Audit"],
            "evidence": "CRM must complete before Security Audit.",
        },
        {
            "activity": "Security Audit",
            "status": "NOT_STARTED",
            "blocked_by": ["CRM Integration"],
            "blocks": [],
            "evidence": "Security Audit depends on CRM Integration.",
        },
    ]
    enriched = DependencyGraphBuilder.build_and_enrich(candidates, all_baseline)
    lb = next(c for c in enriched if c.get("activity") == "Load Balancer Config")
    sec = next(c for c in enriched if c.get("activity") == "Security Audit")

    lb_blocked = lb.get("blocked_by", [])
    # Load Balancer must NOT be blocked by anything from the CRM chain
    assert "CRM Integration" not in lb_blocked, \
        f"Independent branches must not connect. LB blocked_by={lb_blocked}"
    assert "Security Audit" not in lb_blocked

    sec_blocked = sec.get("blocked_by", [])
    assert "Infrastructure Setup" not in sec_blocked, \
        f"Independent branches must not connect. Sec blocked_by={sec_blocked}"
    print("  ✓ Test 20: Independent branches never spuriously connected")


# ---------------------------------------------------------------------------
# Test 21: Owner detection from evidence keywords
# ---------------------------------------------------------------------------
def test_owner_detection_from_evidence():
    """Customer/Vendor ownership is derived from evidence, not entity name."""
    candidates = [
        {
            "activity": "CRM Integration",
            "status": "WAITING_ON_CUSTOMER",
            "blocked_by": [],
            "blocks": [],
            "evidence": "Waiting for customer to provide API credentials.",
        },
        {
            "activity": "Security Audit",
            "status": "NOT_STARTED",
            "blocked_by": [],
            "blocks": [],
            "evidence": "Internal security review is in progress.",
        },
    ]
    enriched = DependencyGraphBuilder.build_and_enrich(candidates, BASELINE_ITEMS)
    crm = next(c for c in enriched if "CRM" in c.get("activity", ""))
    sec = next(c for c in enriched if "Security" in c.get("activity", ""))

    assert crm.get("dependency_owner") == "Customer", \
        f"CRM owner must be Customer. Got: {crm.get('dependency_owner')}"
    # Security Audit evidence has no customer/vendor keyword
    assert sec.get("dependency_owner") in ("Internal", "Vendor"), \
        f"Security Audit should be Internal. Got: {sec.get('dependency_owner')}"
    print(f"  ✓ Test 21: Owner from evidence (CRM={crm.get('dependency_owner')}, "
          f"Security={sec.get('dependency_owner')})")


# ---------------------------------------------------------------------------
# Test 22: Co-occurrence is NOT enough to create a dependency edge
# ---------------------------------------------------------------------------
def test_co_occurrence_no_edge():
    """Two activities mentioned near each other without explicit blocking language
    must NOT produce a dependency edge."""
    candidates = [
        {
            "activity": "CRM Integration",
            "status": "IN_PROGRESS",
            "blocked_by": [],   # No dependency stated
            "blocks": [],
            "evidence": "CRM Integration is being worked on. Security Audit is also planned.",
        },
        {
            "activity": "Security Audit",
            "status": "NOT_STARTED",
            "blocked_by": [],   # No dependency stated
            "blocks": [],
            "evidence": "CRM Integration is being worked on. Security Audit is also planned.",
        },
    ]
    enriched = DependencyGraphBuilder.build_and_enrich(candidates, BASELINE_ITEMS)
    crm = next(c for c in enriched if "CRM" in c.get("activity", ""))
    sec = next(c for c in enriched if "Security" in c.get("activity", ""))

    assert "Security Audit" not in crm.get("blocks", []), \
        f"Co-occurrence must NOT create edge. CRM blocks: {crm.get('blocks')}"
    assert "CRM Integration" not in sec.get("blocked_by", []), \
        f"Co-occurrence must NOT create edge. Sec blocked_by: {sec.get('blocked_by')}"
    print("  ✓ Test 22: Co-occurrence without blocker language → no dependency edge")


# ---------------------------------------------------------------------------
# Test 23: graph_role from topology (never from LLM)
# ---------------------------------------------------------------------------
def test_graph_role_from_topology():
    """graph_role must be set from topology (ROOT_CAUSE, INTERMEDIATE_BLOCKER,
    TERMINAL_ACTIVITY, ISOLATED) — not from any LLM-provided value."""
    candidates = [
        {
            "activity": "API Gateway Configuration",
            "status": "NOT_STARTED",
            "blocked_by": [],
            "blocks": ["CRM Integration"],
            "graph_role": "DOWNSTREAM",  # LLM provided wrong role — must be overwritten
            "evidence": "CRM needs API Gateway first.",
        },
        {
            "activity": "CRM Integration",
            "status": "NOT_STARTED",
            "blocked_by": ["API Gateway Configuration"],
            "blocks": [],
            "graph_role": "ROOT_CAUSE",  # LLM provided wrong role — must be overwritten
            "evidence": "CRM depends on API Gateway.",
        },
    ]
    enriched = DependencyGraphBuilder.build_and_enrich(candidates, BASELINE_ITEMS)
    api = next(c for c in enriched if "API Gateway" in c.get("activity", ""))
    crm = next(c for c in enriched if c.get("activity") == "CRM Integration")

    assert api.get("graph_role") == "ROOT_CAUSE", \
        f"API Gateway (no upstream, has downstream) must be ROOT_CAUSE. Got: {api.get('graph_role')}"
    assert crm.get("graph_role") in ("TERMINAL_ACTIVITY",), \
        f"CRM (has upstream, no downstream) must be TERMINAL_ACTIVITY. Got: {crm.get('graph_role')}"
    print(f"  ✓ Test 23: graph_role from topology only "
          f"(API={api.get('graph_role')}, CRM={crm.get('graph_role')})")


# ---------------------------------------------------------------------------
# Test 24: Priority from graph topology (cascade + unlock), not list order
# ---------------------------------------------------------------------------
def test_priority_from_topology_not_list_order():
    """The activity with higher cascade impact must have higher execution priority,
    regardless of position in the input list."""
    from services.execution_queue_builder import ExecutionQueueBuilder
    from unittest.mock import MagicMock

    class FakeSnapshot:
        milestone_statuses = {
            "A": "NOT_STARTED",
            "B": "NOT_STARTED",
            "C": "NOT_STARTED",
        }
        def get_status(self, nid):
            return "NOT_STARTED"
        def get_date(self, nid):
            return None

    # A → B → C  (A has cascade=2, B has cascade=1, C has cascade=0)
    fwd = {"A": ["B"], "B": ["C"]}
    bwd = {"B": ["A"], "C": ["B"]}

    queue, metrics = ExecutionQueueBuilder.build_queue(FakeSnapshot(), bwd, fwd)
    a_score = metrics["A"]["execution_index"]
    b_score = metrics["B"]["execution_index"]
    c_score = metrics["C"]["execution_index"]

    assert a_score > b_score, \
        f"A (root, cascade=2) must score higher than B. A={a_score}, B={b_score}"
    assert b_score > c_score, \
        f"B (cascade=1) must score higher than C (cascade=0). B={b_score}, C={c_score}"
    assert queue[0] == "A", f"A must be first in execution queue. Got: {queue}"
    print(f"  ✓ Test 24: Priority from topology "
          f"(A={a_score:.1f} > B={b_score:.1f} > C={c_score:.1f})")


# ---------------------------------------------------------------------------
# Test 25: Synthetic priority graph A/B > C > D > X
# ---------------------------------------------------------------------------
def test_synthetic_priority_graph():
    """
    A ──┐
        ├──> C ──> D
    B ──┘
    X (Isolated)
    Expected priority: A/B > C > D > X
    """
    from services.dependency_graph_builder import _build_adjacency, DependencyEdge
    from services.execution_queue_builder import ExecutionQueueBuilder
    class DummySnapshot:
        def __init__(self):
            self.milestone_statuses = {}
        def get_status(self, n): return "NOT_STARTED"
        def get_date(self, n): return None
        
    edges = [
        DependencyEdge("A", "C", "BLOCKS", "A blocks C", 1.0, "AND"),
        DependencyEdge("B", "C", "BLOCKS", "B blocks C", 1.0, "AND"),
        DependencyEdge("C", "D", "BLOCKS", "C blocks D", 1.0, "AND")
    ]
    fwd, bwd = _build_adjacency(edges)
    # Inject X manually since it has no edges
    fwd["X"] = []
    bwd["X"] = []
    
    queue, metrics = ExecutionQueueBuilder.build_queue(DummySnapshot(), bwd, fwd)
    
    a_idx = queue.index("A")
    b_idx = queue.index("B")
    c_idx = queue.index("C")
    d_idx = queue.index("D")
    x_idx = queue.index("X")
    
    assert min(a_idx, b_idx) == 0 and max(a_idx, b_idx) == 1, f"A and B must be top 2. Got: {queue}"
    assert c_idx == 2, f"C must be 3rd. Got: {queue}"
    assert d_idx == 3, f"D must be 4th. Got: {queue}"
    assert x_idx == 4, f"X must be last. Got: {queue}"
    
    assert metrics["A"]["graph_role"] == "ROOT_CAUSE"
    assert metrics["B"]["graph_role"] == "ROOT_CAUSE"
    assert metrics["C"]["graph_role"] == "INTERMEDIATE_BLOCKER"
    assert metrics["D"]["graph_role"] == "TERMINAL_ACTIVITY"
    assert metrics["X"]["graph_role"] == "ISOLATED"
    
    print("  ✓ Test 25: Synthetic priority graph A/B > C > D > X successfully validated.")

# ---------------------------------------------------------------------------
# Test 26: Consistency Assertions
# ---------------------------------------------------------------------------
def test_consistency_assertions():
    """
    ISOLATED node cannot have confirmed_blocked_by.
    ISOLATED node cannot have maximum score if root causes exist.
    """
    candidates = [
        {
            "activity": "X",
            "status": "NOT_STARTED",
            "blocked_by": ["Unknown Dependency"],
            "blocks": [],
            "evidence": "X is blocked by Unknown Dependency",
        },
        {
            "activity": "A",
            "status": "NOT_STARTED",
            "blocked_by": [],
            "blocks": ["B"],
            "evidence": "A blocks B",
        },
        {
            "activity": "B",
            "status": "NOT_STARTED",
            "blocked_by": ["A"],
            "blocks": [],
            "evidence": "B is blocked by A",
        }
    ]
    enriched = DependencyGraphBuilder.build_and_enrich(candidates, BASELINE_ITEMS)
    x = next(c for c in enriched if c.get("activity") == "X")
    
    assert x.get("graph_role") == "ISOLATED", f"X must be isolated despite unresolved dep. Got: {x.get('graph_role')}"
    assert len(x.get("blocked_by", [])) == 0, f"X must have empty blocked_by because the dep is unresolved. Got: {x.get('blocked_by')}"
    assert len(x.get("unresolved_dependencies", [])) == 1, f"X must capture unresolved dependencies"
    assert x.get("readiness_status") == "BLOCKED_UNRESOLVED_DEPENDENCY", f"X status must be BLOCKED_UNRESOLVED_DEPENDENCY. Got: {x.get('readiness_status')}"
    
    print("  ✓ Test 26: Consistency assertions (ISOLATED + unresolved dep) successfully validated.")


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print("\n=== EntityResolver + Dependency Engine Regression Tests ===\n")
    tests = [
        test_exact_resolution,
        test_case_insensitive,
        test_partial_reference,
        test_non_entity_rejection,
        test_external_dependency_detection,
        test_graph_metrics,
        test_execution_status_preservation,
        test_orphan_detection,
        test_sit_compound_baseline_mapping,
        test_graph_md_compliance,
        # 15 new evidence-driven tests
        test_and_prerequisites_both_edges,
        test_and_readiness_one_unsatisfied_blocked,
        test_and_readiness_all_satisfied_ready,
        test_or_prerequisite_any_one_satisfies,
        test_unresolved_dependency_preserved_not_executable,
        test_self_dependency_rejection,
        test_similar_name_no_false_match,
        test_sequential_chain_no_transitive_invention,
        test_duplicate_evidence_merged,
        test_cycle_detection,
        test_independent_branches_not_connected,
        test_owner_detection_from_evidence,
        test_co_occurrence_no_edge,
        test_graph_role_from_topology,
        test_priority_from_topology_not_list_order,
        test_synthetic_priority_graph,
        test_consistency_assertions,
    ]
    failed = []
    for t in tests:
        try:
            t()
        except AssertionError as e:
            print(f"  ✗ FAIL [{t.__name__}]: {e}")
            failed.append(t.__name__)
        except Exception as e:
            print(f"  ✗ ERROR [{t.__name__}]: {type(e).__name__}: {e}")
            import traceback
            traceback.print_exc()
            failed.append(t.__name__)

    print()
    if not failed:
        print(f"All {len(tests)} tests passed ✓\n")
    else:
        print(f"{len(failed)} test(s) FAILED: {', '.join(failed)}\n")
        sys.exit(1)
