from services.llm_service import LLMService
from tools.mcp_tools import MCPTools

class ReconciliationAgent:
    @classmethod
    def evaluate_risk(cls, project_id: int, item: dict, item_type: str = "ACTIVITY") -> dict:
        """
        Hybrid Risk Engine: Evaluates an extracted item against the project baseline.
        
        Returns a structured risk assessment with:
        - risk_score: 0-100 integer
        - risk_level: LOW / MEDIUM / HIGH / CRITICAL
        - risk_category: SCOPE_CREEP / DELAY / MISSING_DELIVERABLE / DEPENDENCY / STAKEHOLDER / GENERAL
        - description: 2-3 sentence explanation of WHY this risk score was given
        - is_out_of_scope: boolean
        - requires_escalation: boolean
        - confidence: 0.0-1.0
        - reasoning: detailed AI reasoning with citations
        """
        # 1. Get Project Context
        context = MCPTools.get_project_context(project_id)
        
        # 2. Search Baseline Evidence
        query = item.get("activity_name", item.get("request_name", item.get("blocker_name", item.get("action_name", item.get("decision", item.get("risk_name", ""))))))
        evidence = MCPTools.search_baseline(project_id, query)
        
        evidence_text = "\n".join([
            f"- [Source: {e.get('metadata', {}).get('document_name', 'Unknown Document')}] {e['text']} (Score: {e.get('rerank_score', e.get('score', 0)):.2f})"
            for e in evidence
        ])
        # 3. Build item description for the LLM
        item_desc = ""
        if item_type == "ACTIVITY":
            item_desc = f"Activity: {item.get('activity_name', 'Unknown')}\nDescription: {item.get('description', '')}\nStatus: {item.get('activity_status', 'UNKNOWN')}\nRequested By: {item.get('requested_by', 'N/A')}\nOwner: {item.get('owner', 'N/A')}"
        elif item_type == "NEW_REQUEST":
            item_desc = f"New Request: {item.get('request_name', 'Unknown')}\nDescription: {item.get('description', '')}\nRequested By: {item.get('requested_by', 'N/A')}"
        elif item_type == "BLOCKER":
            item_desc = f"Blocker: {item.get('blocker_name', 'Unknown')}\nDescription: {item.get('description', '')}\nAffected Activity: {item.get('affected_activity', 'N/A')}\nSeverity: {item.get('severity', 'N/A')}"
        elif item_type == "ACTION_ITEM":
            item_desc = f"Action Item: {item.get('action_name', 'Unknown')}\nAssigned To: {item.get('assigned_to', 'N/A')}\nDue Date: {item.get('due_date', 'N/A')}\nStatus: {item.get('status', 'PENDING')}"
        elif item_type == "DECISION":
            item_desc = f"Decision: {item.get('decision', 'Unknown')}\nDecided By: {item.get('decided_by', 'N/A')}\nImpact: {item.get('impact', 'N/A')}"
        elif item_type == "RISK_MENTIONED":
            item_desc = f"Risk: {item.get('risk_name', 'Unknown')}\nDescription: {item.get('description', '')}\nLikelihood: {item.get('likelihood', 'N/A')}\nImpact: {item.get('impact', 'N/A')}"
        else:
            item_desc = str(item)

        # 4. Draft Phase (Reflexion step 1)
        from core.prompts import get_reconciliation_draft_prompt
        draft_prompt = get_reconciliation_draft_prompt(
            procedural_rules=context.get("procedural_rules", ""),
            item_type=item_type,
            item_desc=item_desc,
            item_evidence=item.get("evidence_text", "N/A"),
            baseline_evidence=evidence_text if evidence_text.strip() else "No direct baseline evidence found for this item."
        )
        draft = LLMService.generate_json(draft_prompt)
        
        # 5. Verify Phase (Reflexion step 2) — cross-check with rules
        from core.prompts import get_reconciliation_verify_prompt
        verify_prompt = get_reconciliation_verify_prompt(
            procedural_rules=context.get("procedural_rules", ""),
            draft=draft,
            baseline_evidence=evidence_text if evidence_text.strip() else "No baseline evidence found."
        )
        finalized = LLMService.generate_json(verify_prompt)
        
        # 6. Post-process: enforce consistency between risk_score and risk_level
        score = int(finalized.get("risk_score", 0))
        if score <= 20:
            finalized["risk_level"] = "LOW"
        elif score <= 40:
            finalized["risk_level"] = "MEDIUM"
        elif score <= 70:
            finalized["risk_level"] = "HIGH"
        else:
            finalized["risk_level"] = "CRITICAL"
        
        finalized["risk_score"] = score
        
        return finalized
