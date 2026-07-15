from services.llm_service import LLMService
from tools.mcp_tools import MCPTools

class ReconciliationAgent:
    @classmethod
    def evaluate_risk(cls, project_id: int, activity_or_request: dict) -> dict:
        # 1. Get Context
        context = MCPTools.get_project_context(project_id)
        
        # 2. Search Baseline Evidence
        query = activity_or_request.get("activity_name", activity_or_request.get("request_name", ""))
        evidence = MCPTools.search_baseline(project_id, query)
        
        evidence_text = "\n".join([f"- {e['text']} (Score: {e.get('rerank_score', e.get('score', 0)):.2f})" for e in evidence])
        
        # 3. Draft Phase (Reflexion step 1)
        draft_prompt = f"""
You are an expert contract risk evaluator.
Evaluate the following activity/request against the project baseline and context.

Procedural Rules:
{context.get("procedural_rules")}

Activity/Request:
{activity_or_request}

Baseline Evidence:
{evidence_text}

Draft an initial assessment in JSON:
{{
  "is_out_of_scope": boolean,
  "risk_score": 0.0 to 1.0,
  "reasoning": "string"
}}
"""
        draft = LLMService.generate_json(draft_prompt)
        
        # 4. Verify Phase (Reflexion step 2)
        verify_prompt = f"""
Review this draft risk assessment against the procedural rules.
Rules:
{context.get("procedural_rules")}

Draft:
{draft}

Evidence:
{evidence_text}

If the draft claims Out of Scope, does it cite exact text? If confidence is low, is the risk score adjusted?
Output a Finalized assessment in JSON:
{{
  "is_out_of_scope": boolean,
  "risk_score": 0.0 to 1.0,
  "reasoning": "string (improved with citations if needed)",
  "confidence": 0.0 to 1.0,
  "requires_escalation": boolean
}}
"""
        finalized = LLMService.generate_json(verify_prompt)
        return finalized
