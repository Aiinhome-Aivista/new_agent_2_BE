from services.llm_service import LLMService
from tools.mcp_tools import MCPTools

class InScopeEvaluationAgent:
    @classmethod
    def evaluate(cls, project_id: int, document_text: str) -> dict:
        """
        Reads the uploaded MOM/Status Report, extracts activities, and matches them to baseline.
        """
        context = MCPTools.get_project_context(project_id)
        
        # We fetch the baseline deliverables to provide as context
        baseline_evidence = MCPTools.search_baseline(project_id, "All deliverables and in-scope activities")
        evidence_text = "\n".join([f"- {e['text']}" for e in baseline_evidence[:10]])

        prompt = f"""You are the In-Scope Evaluation Agent.
Your job is to read the following project status document and extract all work items, action items, and activities.
Then, compare them with the approved project baseline to determine if they belong to an existing in-scope deliverable.

Document Text:
{document_text}

Baseline Evidence:
{evidence_text}

Procedural Rules:
{context.get("procedural_rules")}

Output MUST be a valid JSON object matching this exact schema:
{{
  "agent": "InScopeEvaluation",
  "activities": [
      {{
          "activity": "Backend API Development",
          "classification": "IN_SCOPE",
          "deliverable": "Backend Development",
          "status": "In Progress",
          "progress_percentage": 50,
          "confidence": 96
      }}
  ]
}}

Valid classifications: "IN_SCOPE", "POSSIBLY_IN_SCOPE", "UNABLE_TO_DETERMINE".
"""
        return LLMService.generate_json(prompt)


class OutOfScopeDetectionAgent:
    @classmethod
    def detect(cls, project_id: int, extracted_activities: list, document_text: str) -> dict:
        """
        Detects out of scope work from the extracted activities.
        """
        # Search specifically for out-of-scope exclusions in the baseline
        exclusions = MCPTools.search_baseline(project_id, "Out of scope exclusions and limitations")
        exclusion_text = "\n".join([f"- {e['text']}" for e in exclusions[:10]])

        prompt = f"""You are the Out-of-Scope Detection Agent.
Your job is to read the extracted activities from the latest status document and detect any work that does not belong to the contractual scope.

Extracted Activities:
{extracted_activities}

Baseline Exclusions & Limitations:
{exclusion_text}

Document Context:
{document_text}

Output MUST be a valid JSON object matching this exact schema:
{{
  "agent": "OutOfScopeDetection",
  "activities": [
      {{
          "activity": "SAP Integration",
          "classification": "OUT_OF_SCOPE",
          "reason": "Explicitly excluded in Engagement Letter",
          "similar_deliverable": "N/A",
          "confidence": 99
      }}
  ]
}}

Valid classifications: "OUT_OF_SCOPE", "POSSIBLE_SCOPE_CREEP", "REVIEW_REQUIRED".
Only include activities that are flagged as out of scope or requiring review. Do NOT include perfectly in-scope activities here.
"""
        return LLMService.generate_json(prompt)


class DeliverableTimelineEvaluationAgent:
    @classmethod
    def evaluate(cls, project_id: int, document_text: str) -> dict:
        """
        Evaluates deliverables for delays, blockers, and dependencies.
        """
        prompt = f"""You are the Deliverable & Timeline Evaluation Agent.
Your job is to evaluate the project execution quality based on the uploaded document.
Determine if deliverables are delayed, blocked, or missing dependencies.

Document Text:
{document_text}

Extract any blockers from statements like "Waiting for client", "Pending requirement", "Resource unavailable".

Output MUST be a valid JSON object matching this exact schema:
{{
  "agent": "DeliverableTimelineEvaluation",
  "deliverables": [
      {{
          "deliverable": "UI Design",
          "expected_date": "2026-08-10",
          "current_status": "Delayed",
          "delay_days": 8,
          "blockers": ["Client approval pending"],
          "dependency_status": "Blocked",
          "risk": "HIGH"
      }}
  ]
}}

Valid Risk levels: "LOW", "MEDIUM", "HIGH", "CRITICAL".
"""
        return LLMService.generate_json(prompt)
