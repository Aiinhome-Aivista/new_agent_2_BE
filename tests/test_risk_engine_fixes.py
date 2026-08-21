import sys
import os
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


if __name__ == "__main__":
    test_problem_1_root_causes_consistent_and_stateless()
    test_problem_2_band_hierarchy_and_ranking()
    test_ranking_engine_preserves_scores()
    test_problem_3_due_date_parsing()
    test_problem_3_due_date_fallback_in_scoring()
    test_problem_4_scope_creep_scoring()
    test_problem_6_prompt_entity_type_consistency()
    test_problem_7_extractor_prompt_blocks_validation()
    print("\n" + "=" * 60)
    print(" ALL 7 PROBLEM FIXES VERIFIED AND PASSED SUCCESSFULLY! ")
    print("=" * 60)

