import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.project_knowledge_service import ProjectKnowledgeService

class MockCursor:
    def __init__(self):
        self.queries = []
        self._results = {}
        
    def execute(self, query, params=None):
        self.queries.append((query.strip(), params))
        
    def fetchall(self):
        last_query = self.queries[-1][0].lower() if self.queries else ""
        if "from project_milestones" in last_query:
            return [
                {"id": 1, "name": "CRM Integration", "status": "IN_PROGRESS", "planned_date": "2026-08-30"},
                {"id": 2, "name": "AI Knowledge Search", "status": "COMPLETED", "planned_date": "2026-07-27"},
                {"id": 3, "name": "SIT Testing", "status": "BLOCKED", "planned_date": "2026-09-15"},
            ]
        elif "from milestone_dependencies" in last_query:
            return [
                {"parent_name": "CRM Integration", "child_name": "SIT Testing", "dependency_type": "FINISH_TO_START"}
            ]
        elif "from tracker_items" in last_query and "status = 'open'" in last_query:
            return [
                {
                    "title": "Production CRM API credentials",
                    "item_type": "BLOCKER",
                    "risk_category": "WAITING_DEPENDENCY",
                    "risk_level": "HIGH",
                    "status": "OPEN",
                    "execution_priority_score": 95,
                    "risk_score": 85,
                    "graph_role": "ROOT_CAUSE",
                    "reasoning": "Awaiting customer API keys.",
                    "recommended_action": "Escalate to customer POC."
                }
            ]
        elif "from tracker_items" in last_query and "status = 'resolved'" in last_query:
            return [
                {
                    "title": "Document Indexing",
                    "item_type": "ACTIVITY",
                    "risk_category": "RESOLVED",
                    "risk_level": "LOW",
                    "status": "RESOLVED",
                    "resolution": "Indexing completed.",
                    "reasoning": "Done.",
                    "resolved_at": "2026-08-20 10:00:00"
                }
            ]
        return []

def test_pm_execution_context_builder():
    cursor = MockCursor()
    context = ProjectKnowledgeService.get_pm_execution_context(cursor, project_id=1)
    
    assert "LIVE PROJECT EXECUTION & RISK REGISTER SUMMARY" in context
    assert "Total Open/Active Risks & Blockers: 1" in context
    assert "Total Resolved Risks & Completed Actions: 1" in context
    assert "Production CRM API credentials" in context
    assert "Document Indexing" in context
    assert "CRM Integration" in context
    assert "SIT Testing" in context
    
    print("\n[OK] test_pm_execution_context_builder PASSED!")

if __name__ == "__main__":
    test_pm_execution_context_builder()
