# tests/test_agentic_rag.py
import sys
import os
import pytest
from unittest.mock import MagicMock

# Add project root to path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from services.rag_guardrail_service import RAGGuardrailService
from services.graph_rag_service import GraphRAGService


def test_rag_guardrail_out_of_domain():
    """Verify that general knowledge / off-topic queries are intercepted with safe refusal."""
    out_of_domain_queries = [
        "Who is the prime minister of India?",
        "Who is the president of USA?",
        "What is the capital of France?",
        "Tell me a joke about engineers",
        "Write a poem about nature",
        "What is the current stock price of Apple?",
        "Who won the FIFA world cup?"
    ]
    for q in out_of_domain_queries:
        res = RAGGuardrailService.classify_and_guard(q, project_name="Agent 2 Test")
        assert res["is_in_domain"] is False, f"Expected {q} to be out of domain"
        assert "dedicated to this project" in res["safe_response"]
        assert "general knowledge" in res["safe_response"]


def test_rag_guardrail_in_domain_and_greetings():
    """Verify that project-specific queries and greetings pass through appropriately."""
    # Greetings
    hello_res = RAGGuardrailService.classify_and_guard("Hello", project_name="Cloud Migration")
    assert hello_res["is_in_domain"] is True
    assert "Cloud Migration" in hello_res["safe_response"]

    # In-domain project queries
    project_queries = [
        "Why is User Acceptance Testing in risk and not complete?",
        "What are the approved deliverables in the baseline?",
        "What is the milestone deadline for System Integration Testing?",
        "If CRM API credentials are provided, will UAT be freely completed?",
        "Who is responsible for the API credentials blocker?"
    ]
    for q in project_queries:
        res = RAGGuardrailService.classify_and_guard(q, project_name="Cloud Migration")
        assert res["is_in_domain"] is True
        assert res["safe_response"] is None
        assert res["confidence"] >= 0.90


def test_rag_guardrail_ambiguity_clarification():
    """Verify that ambiguous single-word queries trigger clarification prompts."""
    ambiguous_queries = ["status", "risk", "delay", "blocker"]
    for q in ambiguous_queries:
        res = RAGGuardrailService.classify_and_guard(q, project_name="Cloud Migration")
        assert res["needs_clarification"] is True
        assert res["clarification_prompt"] is not None
        assert "Could you please specify" in res["clarification_prompt"]


def test_graph_rag_lineage_and_root_cause_tracing():
    """Verify GraphRAG traces upstream root causes and downstream impact correctly."""
    mock_cursor = MagicMock()
    mock_cursor.fetchall.side_effect = [
        # 1. tracker_items rows
        [
            {
                "id": 1,
                "title": "Production CRM API credentials",
                "item_type": "BLOCKER",
                "risk_category": "EXTERNAL",
                "risk_level": "CRITICAL",
                "status": "OPEN",
                "execution_priority_score": 92,
                "risk_score": 90,
                "graph_role": "ROOT_CAUSE",
                "reasoning": '{"blocked_by": [], "blocks": ["CRM Integration"], "executive_summary": "Waiting on Customer for production credentials."}',
                "recommended_action": "Escalate to Customer PM"
            },
            {
                "id": 2,
                "title": "CRM Integration",
                "item_type": "ACTIVITY",
                "risk_category": "TECHNICAL",
                "risk_level": "HIGH",
                "status": "OPEN",
                "execution_priority_score": 85,
                "risk_score": 80,
                "graph_role": "INTERMEDIATE_BLOCKER",
                "reasoning": '{"blocked_by": ["Production CRM API credentials"], "blocks": ["System Integration Testing (SIT)"]}',
                "recommended_action": "Complete adapter configuration"
            },
            {
                "id": 3,
                "title": "System Integration Testing (SIT)",
                "item_type": "ACTIVITY",
                "risk_category": "TECHNICAL",
                "risk_level": "HIGH",
                "status": "OPEN",
                "execution_priority_score": 75,
                "risk_score": 70,
                "graph_role": "INTERMEDIATE_BLOCKER",
                "reasoning": '{"blocked_by": ["CRM Integration"], "blocks": ["User Acceptance Testing"]}',
                "recommended_action": "Execute test suites"
            },
            {
                "id": 4,
                "title": "User Acceptance Testing",
                "item_type": "ACTIVITY",
                "risk_category": "GENERAL",
                "risk_level": "MEDIUM",
                "status": "OPEN",
                "execution_priority_score": 55,
                "risk_score": 50,
                "graph_role": "TERMINAL_ACTIVITY",
                "reasoning": '{"blocked_by": ["System Integration Testing (SIT)"], "blocks": []}',
                "recommended_action": "Schedule client walkthrough"
            }
        ],
        # 2. milestone_dependencies rows
        []
    ]

    query = "Why is User Acceptance Testing blocked and what are its dependencies?"
    context = GraphRAGService.get_graph_rag_context(mock_cursor, project_id=45, query=query)

    assert "TARGET ACTIVITY DEPENDENCY LINEAGE: 'User Acceptance Testing'" in context
    assert "System Integration Testing (SIT)" in context
    assert "CRM Integration" in context
    assert "Production CRM API credentials" in context
    assert "Root Cause Blocker(s): Production CRM API credentials" in context
    assert "ACTIVE ROOT CAUSE BLOCKERS" in context


def test_graph_rag_what_if_simulation():
    """Verify GraphRAG computes what-if unblock simulations with multi-step impact."""
    mock_cursor = MagicMock()
    mock_cursor.fetchall.side_effect = [
        [
            {
                "id": 1,
                "title": "Production CRM API credentials",
                "status": "OPEN",
                "graph_role": "ROOT_CAUSE",
                "execution_priority_score": 90,
                "reasoning": '{"blocked_by": [], "blocks": ["CRM Integration"]}',
                "recommended_action": "Obtain API keys"
            },
            {
                "id": 2,
                "title": "CRM Integration",
                "status": "OPEN",
                "graph_role": "INTERMEDIATE_BLOCKER",
                "execution_priority_score": 80,
                "reasoning": '{"blocked_by": ["Production CRM API credentials"], "blocks": ["System Integration Testing (SIT)"]}',
                "recommended_action": ""
            },
            {
                "id": 3,
                "title": "System Integration Testing (SIT)",
                "status": "OPEN",
                "graph_role": "INTERMEDIATE_BLOCKER",
                "execution_priority_score": 70,
                "reasoning": '{"blocked_by": ["CRM Integration", "Azure AD SSO"], "blocks": []}',
                "recommended_action": ""
            },
            {
                "id": 4,
                "title": "Azure AD SSO",
                "status": "OPEN",
                "graph_role": "ROOT_CAUSE",
                "execution_priority_score": 60,
                "reasoning": '{"blocked_by": [], "blocks": ["System Integration Testing (SIT)"]}',
                "recommended_action": ""
            }
        ],
        []
    ]

    query = "If Production CRM API credentials are provided, will System Integration Testing unblock?"
    context = GraphRAGService.get_graph_rag_context(mock_cursor, project_id=45, query=query)

    assert "WHAT-IF UNBLOCK SIMULATION" in context
    assert "CRM Integration: FREELY UNBLOCKED" in context


if __name__ == "__main__":
    test_rag_guardrail_out_of_domain()
    test_rag_guardrail_in_domain_and_greetings()
    test_rag_guardrail_ambiguity_clarification()
    test_graph_rag_lineage_and_root_cause_tracing()
    test_graph_rag_what_if_simulation()
    print("\n" + "=" * 60)
    print(" ALL AGENTIC & GRAPHRAG TESTS PASSED SUCCESSFULLY! ")
    print("=" * 60)
