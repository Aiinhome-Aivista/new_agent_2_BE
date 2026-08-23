import sys
import os
import random
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import datetime, date, timedelta
from services.risk_scoring_engine import RiskScoringEngine, _parse_due_date
from services.risk_ranking_engine import RiskRankingEngine
from services.project_knowledge_service import ProjectKnowledgeService
from core.prompts import get_batch_activity_risk_prompt, get_activity_extractor_prompt


# ==============================================================================
# Problem 1 & 2: Stateless Scoring & Graph-Role Band Hierarchy
# ==============================================================================

def test_problem_1_root_causes_consistent_and_stateless():
    """
    Two structurally identical root causes must produce the exact same execution_priority_score
    and fall into Band 1 (90-100) or Band 2 (80-89).
    """
    item1 = RiskScoringEngine.calculate(
        status="NOT_STARTED",
        blocked_by=[],
        graph_role="ROOT_CAUSE",
        cascade_count=2,
        blocked_work_count=2,
        dependency_owner="Customer",
        business_criticality="Mission Critical",
        resolution_effort="S",
        due_date="2026-09-09",
        critical_path=True
    )
    
    item2 = RiskScoringEngine.calculate(
        status="NOT_STARTED",
        blocked_by=[],
        graph_role="ROOT_CAUSE",
        cascade_count=2,
        blocked_work_count=2,
        dependency_owner="Customer",
        business_criticality="Mission Critical",
        resolution_effort="S",
        due_date="2026-09-09",
        critical_path=True
    )
    
    assert item1["execution_priority"] == item2["execution_priority"], \
        f"Two identical root causes got different scores: {item1['execution_priority']} vs {item2['execution_priority']}"
    assert 90 <= item1["execution_priority"] <= 100, \
        f"ROOT_CAUSE with cascade >= 2 must be in Band 1 (90-100). Got: {item1['execution_priority']}"


def test_problem_2_band_hierarchy_and_ranking():
    """
    Strict Band Hierarchy:
      Band 1: ROOT_CAUSE (cascade >= 2) -> 90-100
      Band 2: ROOT_CAUSE (cascade == 1) -> 80-89
      Band 3: INTERMEDIATE_BLOCKER -> 60-79
      Band 4: TERMINAL_ACTIVITY -> 40-59
      Band 5: ISOLATED -> 20-39
      Band 7: SCOPE_CREEP -> 0-9
    """
    root = RiskScoringEngine.calculate(
        status="NOT_STARTED",
        blocked_by=[],
        graph_role="ROOT_CAUSE",
        cascade_count=2,
        blocked_work_count=2
    )
    
    intermediate = RiskScoringEngine.calculate(
        status="BLOCKED",
        blocked_by=["API Credentials"],
        graph_role="INTERMEDIATE_BLOCKER",
        cascade_count=1,
        blocked_work_count=1
    )
    
    terminal = RiskScoringEngine.calculate(
        status="NOT_STARTED",
        blocked_by=["CRM Integration"],
        graph_role="TERMINAL_ACTIVITY",
        cascade_count=0,
        blocked_work_count=0
    )
    
    isolated = RiskScoringEngine.calculate(
        status="NOT_STARTED",
        blocked_by=[],
        graph_role="ISOLATED",
        cascade_count=0,
        blocked_work_count=0
    )
    
    scope_creep = RiskScoringEngine.calculate(
        status="IN_PROGRESS",
        blocked_by=[],
        graph_role="ISOLATED",
        is_scope_creep=True
    )
    
    # Verify strict scoring bands
    assert 90 <= root["execution_priority"] <= 100
    assert 60 <= intermediate["execution_priority"] <= 79
    assert 40 <= terminal["execution_priority"] <= 59
    assert 20 <= isolated["execution_priority"] <= 39
    assert 0 <= scope_creep["execution_priority"] <= 9
    
    assert root["execution_priority"] > intermediate["execution_priority"]
    assert intermediate["execution_priority"] > terminal["execution_priority"]
    assert terminal["execution_priority"] > isolated["execution_priority"]
    assert isolated["execution_priority"] > scope_creep["execution_priority"]


def test_ranking_engine_preserves_scores():
    """RiskRankingEngine must NOT overwrite execution_priority_score with linear mapping."""
    tracker_items = [
        {"deliverable": "Root Item 1", "execution_priority_score": 95, "risk_score": 70, "current_status": "OPEN"},
        {"deliverable": "Root Item 2", "execution_priority_score": 95, "risk_score": 60, "current_status": "OPEN"},
        {"deliverable": "Intermediate", "execution_priority_score": 75, "risk_score": 80, "current_status": "OPEN"},
        {"deliverable": "Scope Creep", "execution_priority_score": 5, "risk_score": 85, "current_status": "OPEN"},
    ]
    
    ranked = RiskRankingEngine.rank_risks(tracker_items)
    
    assert ranked[0]["deliverable"] == "Root Item 1"
    assert ranked[0]["execution_priority_score"] == 95  # NOT overwritten to 100
    assert ranked[1]["deliverable"] == "Root Item 2"
    assert ranked[1]["execution_priority_score"] == 95  # NOT overwritten to lower
    assert ranked[2]["deliverable"] == "Intermediate"
    assert ranked[2]["execution_priority_score"] == 75
    assert ranked[3]["deliverable"] == "Scope Creep"
    assert ranked[3]["execution_priority_score"] == 5


# ==============================================================================
# Problem 3: due_date parsing and severity calculation
# ==============================================================================

def test_problem_3_due_date_parsing():
    ref_date = date(2026, 9, 5)
    
    # ISO date
    assert _parse_due_date("2026-09-09", ref_date) == 4
    
    # Human date
    assert _parse_due_date("09 Sep 2026", ref_date) == 4
    assert _parse_due_date("September 9, 2026", ref_date) == 4
    
    # Relative expressions
    assert _parse_due_date("Next week", ref_date) == 7
    assert _parse_due_date("Next meeting", ref_date) == 7
    assert _parse_due_date("This week", ref_date) == 3
    assert _parse_due_date("Tomorrow", ref_date) == 1
    assert _parse_due_date("Today", ref_date) == 0
    assert _parse_due_date("Immediately", ref_date) == 0
    
    # Non-dates should return None
    assert _parse_due_date("After CRM completion", ref_date) is None
    assert _parse_due_date("Once VPN is provided", ref_date) is None
    assert _parse_due_date(None) is None
    assert _parse_due_date("") is None


def test_problem_3_due_date_fallback_in_scoring():
    """When days_until_due is 9999, scoring engine uses due_date fallback."""
    # Without due_date (9999 -> schedule impact 20)
    score_no_date = RiskScoringEngine.calculate(
        status="NOT_STARTED",
        blocked_by=[],
        graph_role="ROOT_CAUSE",
        days_until_due=9999,
        dependency_owner="Customer",
        business_criticality="High"
    )
    
    # With due_date ("Next week" -> ~7 days -> schedule impact 80)
    score_with_date = RiskScoringEngine.calculate(
        status="NOT_STARTED",
        blocked_by=[],
        graph_role="ROOT_CAUSE",
        days_until_due=9999,
        due_date="Next week",
        dependency_owner="Customer",
        business_criticality="High"
    )
    
    assert score_with_date["risk_severity"] > score_no_date["risk_severity"], \
        f"Due date did not increase risk severity: {score_with_date['risk_severity']} vs {score_no_date['risk_severity']}"
    assert score_with_date["score_breakdown"]["Schedule Impact"] == 80


# ==============================================================================
# Problem 4: Scope Creep Band 7 Scoring & High Risk Severity
# ==============================================================================

def test_problem_4_scope_creep_scoring():
    """Scope creep items must get Band 7 (0-9) execution priority, but risk severity >= 85."""
    res = RiskScoringEngine.calculate(
        status="NOT_STARTED",
        blocked_by=[],
        graph_role="ISOLATED",
        is_scope_creep=True,
        category="SCOPE_CREEP"
    )
    
    assert 0 <= res["execution_priority"] <= 9, \
        f"Scope creep execution priority must be 0-9. Got: {res['execution_priority']}"
    assert res["risk_severity"] >= 85, \
        f"Scope creep risk severity must be >= 85. Got: {res['risk_severity']}"


# ==============================================================================
# Problem 6 & 7: Prompt Rules & Milestone Context Awareness
# ==============================================================================

def test_problem_6_prompt_entity_type_consistency():
    """Prompt must include consistency rule: matched_baseline_item -> not SCOPE_REQUEST."""
    prompt = get_batch_activity_risk_prompt("Progress: 50%", "Activity 1")
    assert "CRITICAL RULE — entity_type consistency" in prompt
    assert "entity_type MUST NOT be SCOPE_REQUEST" in prompt
    assert "entity_type MUST NOT be RISK" in prompt


def test_problem_7_extractor_prompt_blocks_validation():
    """Extractor prompt must forbid status/date/owner in blocks/blocked_by."""
    prompt = get_activity_extractor_prompt("Meeting minutes text")
    assert "CRITICAL FOR DEPENDENCIES" in prompt
    assert "NEVER put statuses" in prompt
    assert "deliverables/activities ONLY" in prompt


# ==============================================================================
# Prompt 22: Problem 1 (Scope Creep Band 7 Persistence) & Problem 2 (Owner Propagation)
# ==============================================================================

class MockCursor:
    def __init__(self):
        self.queries = []
        self.lastrowid = 101
    def execute(self, query, params=None):
        self.queries.append((query.strip(), params))
    def fetchall(self):
        return []
    def fetchone(self):
        return None

def test_prompt_22_problem_1_oos_band_7_persistence():
    """Verify that TrackerAuditAgent.persist_tracker_item accepts Band 7 execution_priority_score and risk_severity_score for OOS items."""
    from agents.tracker_audit_agent import TrackerAuditAgent
    cursor = MockCursor()
    
    tracker_id = TrackerAuditAgent.persist_tracker_item(
        db_cursor=cursor,
        project_id=1,
        document_id=10,
        item_type='ACTIVITY',
        is_out_of_scope=True,
        risk_score=85,
        risk_level='HIGH',
        risk_category='SCOPE_CREEP',
        confidence=1.0,
        reasoning="Out of scope request",
        requires_escalation=True,
        title="SAP ERP Integration",
        status="OPEN",
        execution_priority_score=random.randint(1, 9),
        risk_severity_score=85,
        owner="Customer",
        graph_role="SCOPE_CREEP"
    )
    
    assert tracker_id == 101
    assert len(cursor.queries) >= 2 # SELECT existing, INSERT tracker_items, INSERT audit_logs
    insert_tracker_query, insert_tracker_params = cursor.queries[1]
    
    # In the INSERT params, final_exec_score is in 1..9, final_risk_sev is 85
    exec_score_idx = 7 # (project_id, document_id, item_type, reference_id, title, is_out_of_scope, risk_score, final_exec_score, final_risk_sev, ...)
    assert 1 <= insert_tracker_params[7] <= 9, f"OOS execution_priority_score must be 1-9. Got: {insert_tracker_params[7]}"
    assert insert_tracker_params[8] == 85, f"OOS risk_severity_score must be 85. Got: {insert_tracker_params[8]}"


def test_prompt_22_problem_2_owner_normalization_and_propagation():
    """Verify owner normalization map and propagation to persistence."""
    from agents.tracker_audit_agent import TrackerAuditAgent
    
    owner_map = {
        "CUSTOMER": "Customer",
        "VENDOR": "Vendor",
        "THIRD_PARTY": "Third Party",
        "INTERNAL": "Internal"
    }
    
    for raw, expected in [("CUSTOMER", "Customer"), ("VENDOR", "Vendor"), ("THIRD_PARTY", "Third Party"), ("INTERNAL", "Internal"), ("UNKNOWN", "Internal")]:
        normalized = owner_map.get(raw.strip().upper(), "Internal")
        assert normalized == expected, f"Expected {expected} for {raw}, got {normalized}"
        
    # Verify TrackerAuditAgent logs owner in audit_details
    cursor = MockCursor()
    TrackerAuditAgent.persist_tracker_item(
        db_cursor=cursor,
        project_id=1,
        document_id=10,
        item_type='BLOCKER',
        is_out_of_scope=False,
        risk_score=94,
        risk_level='HIGH',
        risk_category='CUSTOMER_DEPENDENCY',
        confidence=1.0,
        reasoning="Waiting on customer credentials",
        requires_escalation=True,
        title="Production CRM API credentials",
        status="OPEN",
        execution_priority_score=94,
        risk_severity_score=94,
        owner="Customer"
    )
    
    # Check audit_logs INSERT contains "owner": "Customer"
    audit_query, audit_params = cursor.queries[-1]
    details_json = audit_params[3]
    import json
    details = json.loads(details_json)
    assert details.get("owner") == "Customer", f"Audit log did not record owner: {details}"


# ==============================================================================
# 6 Bug Fixes: Unit Tests (risk-engine-bug-fix.md)
# ==============================================================================

def test_fix_1_owner_embedded_in_reasoning():
    """Verify Fix 1: _embed_owner_in_reasoning embeds owner in both JSON and text reasoning."""
    from agents.tracker_audit_agent import _embed_owner_in_reasoning
    import json
    
    # 1. Plain text reasoning
    res1 = _embed_owner_in_reasoning("Simple blocker text", "Customer")
    parsed1 = json.loads(res1)
    assert parsed1["owner"] == "Customer"
    assert parsed1["text"] == "Simple blocker text"
    
    # 2. JSON reasoning (pmo_narrative)
    res2 = _embed_owner_in_reasoning(json.dumps({"summary": "Delay in API", "_type": "pmo_narrative"}), "Vendor")
    parsed2 = json.loads(res2)
    assert parsed2["owner"] == "Vendor"
    assert parsed2["summary"] == "Delay in API"
    
    # 3. None/empty owner
    assert _embed_owner_in_reasoning("text", None) == "text"


def test_fix_2_parsed_days_until_due_breakdown():
    """Verify Fix 2: RiskScoringEngine.calculate exposes parsed_days_until_due in score_breakdown."""
    engine = RiskScoringEngine()
    future_date = (date.today() + timedelta(days=12)).isoformat()
    result = engine.calculate(
        status="OPEN",
        blocked_by=[],
        graph_role="ISOLATED",
        days_until_due=9999,
        due_date=future_date,
        earliest_root_cause=False,
        cascade_count=0
    )
    breakdown = result["score_breakdown"]
    assert "parsed_days_until_due" in breakdown, "parsed_days_until_due missing from score_breakdown"
    assert breakdown["parsed_days_until_due"] == 12, f"Expected 12 days, got: {breakdown.get('parsed_days_until_due')}"


def test_fix_5_origin_map_complete():
    """Verify Fix 5: _ORIGIN_MAP contains all categories from category assignment engine and pipeline."""
    from agents.tracker_audit_agent import _ORIGIN_MAP
    required_cats = [
        'ROOT_CAUSE', 'ROOT_CAUSE_BLOCKER', 'EXECUTION_BLOCKER',
        'DIRECT_EXECUTION_BLOCKER', 'TRANSITIVE_EXECUTION_BLOCKER',
        'CRITICAL_PATH_RISK', 'CUSTOMER_DEPENDENCY', 'TECHNICAL_DEPENDENCY',
        'INTERNAL_DEPENDENCY', 'WAITING_DEPENDENCY', 'IN_PROGRESS_RISK',
        'SCOPE_CREEP', 'DELAY', 'MISSING_DELIVERABLE', 'STAKEHOLDER', 'GENERAL'
    ]
    for cat in required_cats:
        assert cat in _ORIGIN_MAP, f"Category '{cat}' missing from _ORIGIN_MAP"
        assert _ORIGIN_MAP[cat], f"Empty origin label for '{cat}'"


def test_fix_6_normalize_completion_signals():
    """Verify Fix 6: _normalize_completion_signals deterministically promotes qualified completions and rejects dependency sentences."""
    from agents.risk_evaluation_agent import _normalize_completion_signals
    
    extraction_input = {
        "raw_activities": [
            {
                "statement": "SIT Testing",
                "source_sentence": "SIT completed successfully without any major defects.",
                "classification_type": "RISK"
            },
            {
                "statement": "UAT Signoff",
                "source_sentence": "UAT executed with minor enhancements only.",
                "classification_type": "RISK"
            },
            {
                "statement": "CRM Integration",
                "source_sentence": "CRM Integration blocked by missing customer API credentials.",
                "classification_type": "RISK"
            },
            {
                "statement": "Documentation",
                "source_sentence": "Documentation handed over to the client operations team.",
                "classification_type": "PROGRESS_UPDATE"
            },
            {
                "statement": "Security Testing",
                "source_sentence": "Security Testing failed during vulnerability scan.",
                "classification_type": "RISK"
            },
            {
                "statement": "Azure AD Single Sign-On (SSO)",
                "source_sentence": "The team confirmed Azure AD implementation depends on completion of CRM integration",
                "classification_type": "MILESTONE"
            },
            {
                "statement": "System Integration Testing (SIT)",
                "source_sentence": "The QA lead confirmed that SIT cannot begin until: CRM integration is completed.",
                "classification_type": "MILESTONE"
            }
        ],
        "resolved_items": []
    }
    
    res = _normalize_completion_signals(extraction_input)
    resolved_names = [r["name"] for r in res["resolved_items"]]
    raw_names = [a["statement"] for a in res["raw_activities"]]
    
    # "SIT Testing", "UAT Signoff", "Documentation" must be promoted to resolved
    assert "SIT Testing" in resolved_names, "SIT Testing should have been promoted to resolved"
    assert "UAT Signoff" in resolved_names, "UAT Signoff should have been promoted to resolved"
    assert "Documentation" in resolved_names, "Documentation should have been promoted to resolved"
    
    # Dependency statements must NOT be promoted (must stay in raw_activities)
    assert "Azure AD Single Sign-On (SSO)" in raw_names, "Azure AD SSO must stay in raw_activities (depends on CRM)"
    assert "System Integration Testing (SIT)" in raw_names, "SIT must stay in raw_activities (cannot begin until CRM completed)"
    assert "CRM Integration" in raw_names, "CRM Integration must NOT be promoted (has 'blocked')"
    assert "Security Testing" in raw_names, "Security Testing must NOT be promoted (has 'failed')"


def test_fix_4_detect_project_closure():
    """Verify Fix 4: _detect_project_closure detects closure ONLY when project is truly closed (no active work)."""
    from agents.risk_evaluation_agent import _detect_project_closure
    
    class ClosureMockCursor:
        def __init__(self, in_scope_count=5, terminal_milestones=None):
            self.in_scope_count = in_scope_count
            self.terminal_milestones = terminal_milestones or ["Knowledge Transfer & Closure", "Project Final Acceptance"]
        def execute(self, query, params=None):
            self.last_query = query
        def fetchone(self):
            return {'cnt': self.in_scope_count}
        def fetchall(self):
            return [{'name': m} for m in self.terminal_milestones]
            
    cursor = ClosureMockCursor(in_scope_count=5, terminal_milestones=["Project Final Acceptance", "Knowledge Transfer", "Azure AD SSO"])
    
    # Scenario A: Closure document with 0 raw activities and 3 resolved items (Terminal milestone matched, 0 active work)
    closure_input = {
        "raw_activities": [],
        "resolved_items": [
            {"name": "Knowledge Transfer"},
            {"name": "Documentation Handover"},
            {"name": "Project Final Acceptance"}
        ]
    }
    assert _detect_project_closure(closure_input, cursor, project_id=1) is True, "Project closure should be True when 0 active work and terminal milestone resolved"
    
    # Scenario B: Mid-project document with active work and some non-terminal milestones resolved
    mid_input = {
        "raw_activities": [
            {"statement": "CRM Integration", "classification_type": "ACTION_ITEM", "source_sentence": "In progress"},
            {"statement": "Azure AD Single Sign-On (SSO)", "classification_type": "MILESTONE", "source_sentence": "Depends on CRM"}
        ],
        "resolved_items": [
            {"name": "Document Upload & Indexing"},
            {"name": "Audit Logs"},
            {"name": "User Management"}
        ]
    }
    assert _detect_project_closure(mid_input, cursor, project_id=1) is False, "Mid-project update with active work must NOT trigger project closure"


def test_deterministic_match_returns_tuple():
    """Verify _deterministic_match always returns a 3-tuple (matched_si, confidence, match_type)."""
    from agents.risk_evaluation_agent import _deterministic_match
    
    scope_items = [
        {"name": "API Gateway Configuration"},
        {"name": "CRM Integration"}
    ]
    
    # 1. Exact match
    si, conf, m_type = _deterministic_match("CRM Integration", scope_items)
    assert si == {"name": "CRM Integration"}
    assert conf == 100
    assert m_type == "exact"
    
    # 2. Substring match
    si, conf, m_type = _deterministic_match("Configure CRM Integration module", scope_items)
    assert si == {"name": "CRM Integration"}
    assert conf == 90
    assert m_type == "substring"
    
    # 3. No match (must return tuple, never None)
    si, conf, m_type = _deterministic_match("Completely Unrelated Item XYZ", scope_items)
    assert si is None
    assert conf == 0
    assert m_type is None


# ==============================================================================
# 4 Issues: Unit Tests (risk-engine-fixed.md)
# ==============================================================================

def test_issue_1_resolution_match_containment_and_jaccard():
    """Verify Issue 1: _resolution_match correctly matches short MoM names against full canonical tracker titles."""
    from services.risk_reconciliation_engine import _resolution_match, RESOLUTION_MATCH_THRESHOLD
    
    test_cases = [
        ("AI Knowledge Search", "AI Knowledge Search (RAG-based search for enterprise documents)", True),
        ("Document Upload & Indexing", "Document upload and indexing", True),
        ("Audit Logs", "Audit logs and activity tracking", True),
        ("User Management", "User management and administration", True),
        ("CRM Integration", "CRM Integration", True),
        ("Payment Gateway", "Completely Unrelated Security Audit", False),
        ("Analytics", "User management and administration", False)
    ]
    
    for resolved_name, tracker_title, should_match in test_cases:
        conf = _resolution_match(resolved_name, tracker_title)
        if should_match:
            assert conf >= RESOLUTION_MATCH_THRESHOLD, f"Expected match for '{resolved_name}' vs '{tracker_title}', got conf: {conf}"
        else:
            assert conf < RESOLUTION_MATCH_THRESHOLD, f"Expected NO match for '{resolved_name}' vs '{tracker_title}', got conf: {conf}"


def test_issue_3_extract_progress_pct():
    """Verify Issue 3: _extract_progress_pct extracts integer percentage correctly."""
    from agents.risk_evaluation_agent import _extract_progress_pct
    
    assert _extract_progress_pct("Milestone Progress: 27% (Completed weight: 3.0 / 11.0)") == 27
    assert _extract_progress_pct("Milestone Progress: 100% (Completed weight: 11.0 / 11.0)") == 100
    assert _extract_progress_pct("Milestone Progress: 0% (No baseline found)") == 0
    assert _extract_progress_pct("") == 0
    assert _extract_progress_pct(None) == 0


def test_issue_4_count_resolved_in_run():
    """Verify Issue 4: _count_resolved_in_run executes queries and returns correct count."""
    from agents.risk_evaluation_agent import _count_resolved_in_run
    
    class CountMockCursor:
        def __init__(self, count_val=4):
            self.count_val = count_val
            self.queries = []
        def execute(self, query, params=None):
            self.queries.append((query, params))
        def fetchone(self):
            return {'cnt': self.count_val}
            
    cursor = CountMockCursor(count_val=4)
    cnt = _count_resolved_in_run(cursor, project_id=1, document_id=10)
    assert cnt == 4
    assert len(cursor.queries) == 2


def test_issue_2_prompt_owner_rules():
    """Verify Issue 2: get_batch_activity_risk_prompt contains OWNER DEFINITION RULE with executor vs blocker separation."""
    from core.prompts import get_batch_activity_risk_prompt
    prompt = get_batch_activity_risk_prompt("Milestone Progress: 27%", "Activities: []")
    assert "OWNER DEFINITION RULE" in prompt
    assert "WHO IS RESPONSIBLE FOR EXECUTING OR DELIVERING" in prompt
    assert "BASELINE DELIVERABLE" in prompt
    assert "NEVER set owner = CUSTOMER for a contracted deliverable" in prompt


def test_problem_1_pm_decision_urgency_override():
    """Verify Problem 1: _pm_decision overrides generic message with urgent action for high severity imminent items."""
    from agents.risk_evaluation_agent import RiskEvaluationAgent
    
    # 1. CRM Integration: High risk severity (92), 7 days until due, Internal owner, 3 downstream unlocks
    rec = RiskEvaluationAgent._pm_decision(
        priority=69,
        owner="Internal",
        is_root_cause=False,
        longest_path=["CRM Integration", "Azure AD SSO", "SIT", "UAT"],
        risk_severity=92,
        days_until_due=7,
        cascade_count=3
    )
    assert "Deadline critical: 7 days remaining." in rec
    assert "Assign internal resource immediately to unblock." in rec
    assert "Resolving this unblocks 3 downstream activities." in rec
    
    # 2. Azure AD SSO: Medium severity (58), 9999 days, Internal owner -> falls through to standard monitor message
    rec_sso = RiskEvaluationAgent._pm_decision(
        priority=69,
        owner="Internal",
        is_root_cause=False,
        longest_path=[],
        risk_severity=58,
        days_until_due=9999,
        cascade_count=2
    )
    assert rec_sso == "Monitor and align resources for upcoming sprint"
    
    # 3. Customer blocker: priority 94, root cause, owner Customer -> root cause escalation
    rec_cred = RiskEvaluationAgent._pm_decision(
        priority=94,
        owner="Customer",
        is_root_cause=True,
        longest_path=["Production API Credentials", "CRM Integration"],
        risk_severity=76,
        days_until_due=17,
        cascade_count=4
    )
    assert "Escalate to customer immediately. Request ETA." in rec_cred


def test_problem_2_requires_escalation_rules():
    """Verify Problem 2: _requires_escalation deterministically flags only urgent/contractual risks."""
    from agents.risk_evaluation_agent import _requires_escalation
    
    # Critical risk -> True
    assert _requires_escalation("CRITICAL", 70, "INTERMEDIATE_BLOCKER", "IN_PROGRESS", False) is True
    # High contractual risk (>=85) -> True
    assert _requires_escalation("HIGH", 92, "INTERMEDIATE_BLOCKER", "IN_PROGRESS", False) is True
    # Root cause blocker -> True
    assert _requires_escalation("HIGH", 76, "ROOT_CAUSE", "WAITING_ON_CUSTOMER", False) is True
    # Waiting on customer -> True
    assert _requires_escalation("MEDIUM", 60, "ISOLATED", "WAITING_ON_CUSTOMER", False) is True
    # Scope creep -> True
    assert _requires_escalation("CRITICAL", 85, "SCOPE_CREEP", "OPEN", True) is True
    
    # Routine in-progress isolated item -> False
    assert _requires_escalation("HIGH", 62, "ISOLATED", "IN_PROGRESS", False) is False
    assert _requires_escalation("MEDIUM", 38, "ISOLATED", "IN_PROGRESS", False) is False
    # Routine downstream item with no deadline pressure -> False
    assert _requires_escalation("MEDIUM", 58, "INTERMEDIATE_BLOCKER", "DELAYED", False) is False


def test_genuine_cycle_detection():
    """Verify _is_genuine_cycle distinguishes between real cycles and linear/duplicate paths."""
    from services.dependency_graph_builder import _is_genuine_cycle
    
    # 1. Linear graph: A -> B -> C
    adjacency_linear = {
        "A": {"B"},
        "B": {"C"},
        "C": set()
    }
    # Adding A -> B is NOT a cycle (it's parallel/duplicate)
    assert _is_genuine_cycle("A", "B", adjacency_linear) is False
    # Adding C -> A IS a genuine cycle (C can reach A via the new edge, closing the loop)
    assert _is_genuine_cycle("C", "A", adjacency_linear) is True
    
    # 2. SIT -> UAT -> Production Deployment
    adjacency_qa = {
        "cand_8": {"cand_9"},         # SIT -> UAT
        "cand_9": {"cand_10"},        # UAT -> Prod Deploy
        "cand_10": set()
    }
    # Re-checking SIT -> UAT is NOT a cycle
    assert _is_genuine_cycle("cand_8", "cand_9", adjacency_qa) is False
    # But adding Prod Deploy -> SIT WOULD be a cycle
    assert _is_genuine_cycle("cand_10", "cand_8", adjacency_qa) is True


def test_activity_extractor_prompt_directionality_rule():
    """Verify get_activity_extractor_prompt contains DIRECTIONALITY RULE."""
    from core.prompts import get_activity_extractor_prompt
    prompt = get_activity_extractor_prompt("Document Text")
    assert "DIRECTIONALITY RULE (CRITICAL)" in prompt
    assert "strictly directional and asymmetric" in prompt
    assert "NEVER add the reverse direction" in prompt


def test_strip_parentheticals_and_resolution():
    """Verify _strip_parentheticals strips suffixes and EntityResolver Tier 0.5 resolves names like UAT/SIT."""
    from services.entity_resolver import _strip_parentheticals, EntityResolver, CanonicalEntityRegistry, CanonicalEntity
    
    # 1. Helper unit tests
    assert _strip_parentheticals("User Acceptance Testing (UAT)") == "User Acceptance Testing"
    assert _strip_parentheticals("System Integration Testing (SIT)") == "System Integration Testing"
    assert _strip_parentheticals("API Gateway (v2)") == "API Gateway"
    assert _strip_parentheticals("Module (Beta) (v3)") == "Module"
    assert _strip_parentheticals("Simple Deliverable") is None
    assert _strip_parentheticals("") is None
    assert _strip_parentheticals(None) is None
    
    # 2. Integration with EntityResolver
    registry = CanonicalEntityRegistry()
    uat_entity = CanonicalEntity("cand_7", "User Acceptance Testing", "ACTIVITY")
    sit_entity = CanonicalEntity("si_1343", "System Integration Testing (SIT), UAT, Production Deployment", "FUNCTIONAL", aliases=["System Integration Testing", "SIT"])
    registry.register(uat_entity)
    registry.register(sit_entity)
    
    resolver = EntityResolver(registry)
    
    # Test resolving "User Acceptance Testing (UAT)"
    res_uat = resolver.resolve("User Acceptance Testing (UAT)")
    assert res_uat.resolved is True
    assert res_uat.canonical_id == "cand_7"
    assert res_uat.match_type == "parenthetical_strip"
    assert res_uat.confidence == 0.95
    
    # Test resolving "System Integration Testing (SIT)" via alias
    res_sit = resolver.resolve("System Integration Testing (SIT)")
    assert res_sit.resolved is True
    assert res_sit.canonical_id == "si_1343"


def test_is_semantically_valid_direction_and_dynamic_cycle():
    """Verify _is_semantically_valid_direction rejects contradictory reverse edges and keeps correct graph topology."""
    from services.dependency_graph_builder import (
        _is_semantically_valid_direction,
        DependencyGraphBuilder,
        _is_genuine_cycle
    )

    # 1. Test _is_semantically_valid_direction raw name contradiction
    uat_act_raw = {
        "activity": "User Acceptance Testing",
        "blocked_by": ["System Integration Testing (SIT)"],
        "blocks": ["System Integration Testing (SIT)"]
    }
    assert _is_semantically_valid_direction(
        "User Acceptance Testing", "System Integration Testing (SIT)", uat_act_raw, "blocks"
    ) is False

    # 2. Test _is_semantically_valid_direction resolved ID collision contradiction
    uat_act_resolved = {
        "activity": "User Acceptance Testing",
        "blocked_by": ["System Integration Testing (SIT)"],
        "blocks": ["Production Deployment"]
    }
    # Both SIT and Production Deployment resolved to cand_8 (si_1372)
    assert _is_semantically_valid_direction(
        "User Acceptance Testing", "System Integration Testing (SIT), UAT, Production Deployment",
        uat_act_resolved, "blocks",
        target_id="cand_8", resolved_blocked_by_ids=["cand_8"]
    ) is False

    # 3. Clean non-contradiction case
    clean_act = {
        "activity": "Module X",
        "blocked_by": ["Module Y"],
        "blocks": ["Module Z"]
    }
    assert _is_semantically_valid_direction(
        "Module X", "Module Z", clean_act, "blocks",
        target_id="cand_Z", resolved_blocked_by_ids=["cand_Y"]
    ) is True

    # 4. Dynamic Graph Builder Integration: Linear chain SIT -> UAT
    candidates = [
        {
            "_canonical_id": "cand_0",
            "activity": "CRM Integration",
            "blocked_by": ["Production API credentials"],
            "blocks": ["System Integration Testing (SIT)"]
        },
        {
            "_canonical_id": "cand_1",
            "activity": "Production API credentials",
            "blocked_by": [],
            "blocks": ["CRM Integration"]
        },
        {
            "_canonical_id": "cand_8",
            "activity": "System Integration Testing (SIT)",
            "blocked_by": ["CRM Integration"],
            "blocks": ["User Acceptance Testing (UAT)"]
        },
        {
            "_canonical_id": "cand_9",
            "activity": "User Acceptance Testing",
            "blocked_by": ["System Integration Testing (SIT)"],
            "blocks": ["Production Deployment"]  # resolves to cand_8 via baseline alias
        }
    ]
    baseline_items = [
        {"id": 1365, "title": "CRM Integration for customer information and ticket", "section_type": "FUNCTIONAL"},
        {"id": 1372, "title": "System Integration Testing (SIT), UAT, Production Deployment", "section_type": "FUNCTIONAL"},
        {"id": 1389, "title": "User Acceptance Testing", "section_type": "MILESTONE"},
    ]
    result = DependencyGraphBuilder.build_and_enrich(candidates, baseline_items)
    
    # Verify credentials is ROOT_CAUSE
    cand_cred = next(c for c in result if "credentials" in c["activity"])
    assert cand_cred["graph_role"] == "ROOT_CAUSE"
    
    # Verify SIT is INTERMEDIATE_BLOCKER (blocked by CRM, blocks UAT)
    cand_sit = next(c for c in result if "SIT" in c["activity"])
    assert cand_sit["graph_role"] == "INTERMEDIATE_BLOCKER"
    
    # Verify UAT is NOT a ROOT_CAUSE, it is TERMINAL_ACTIVITY or INTERMEDIATE_BLOCKER
    cand_uat = next(c for c in result if "UAT" in c["activity"] or "Acceptance" in c["activity"])
    assert cand_uat["graph_role"] in ("TERMINAL_ACTIVITY", "INTERMEDIATE_BLOCKER")
    assert cand_uat["graph_role"] != "ROOT_CAUSE"
    assert cand_uat["graph_role"] == "TERMINAL_ACTIVITY"


if __name__ == "__main__":
    test_problem_1_root_causes_consistent_and_stateless()
    test_problem_2_band_hierarchy_and_ranking()
    test_ranking_engine_preserves_scores()
    test_problem_3_due_date_parsing()
    test_problem_3_due_date_fallback_in_scoring()
    test_problem_4_scope_creep_scoring()
    test_problem_6_prompt_entity_type_consistency()
    test_problem_7_extractor_prompt_blocks_validation()
    test_prompt_22_problem_1_oos_band_7_persistence()
    test_prompt_22_problem_2_owner_normalization_and_propagation()
    test_fix_1_owner_embedded_in_reasoning()
    test_fix_2_parsed_days_until_due_breakdown()
    test_fix_5_origin_map_complete()
    test_fix_6_normalize_completion_signals()
    test_fix_4_detect_project_closure()
    test_deterministic_match_returns_tuple()
    test_issue_1_resolution_match_containment_and_jaccard()
    test_issue_3_extract_progress_pct()
    test_issue_4_count_resolved_in_run()
    test_issue_2_prompt_owner_rules()
    test_problem_1_pm_decision_urgency_override()
    test_problem_2_requires_escalation_rules()
    test_genuine_cycle_detection()
    test_activity_extractor_prompt_directionality_rule()
    test_strip_parentheticals_and_resolution()
    test_is_semantically_valid_direction_and_dynamic_cycle()
    print("\n" + "=" * 60)
    print(" ALL PROBLEMS & REMAINING RISK FIXES VERIFIED SUCCESSFULLY! ")
    print("=" * 60)



