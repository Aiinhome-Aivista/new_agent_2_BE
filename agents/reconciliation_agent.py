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
        draft_prompt = f"""You are an expert contract risk evaluator for professional services engagements.

Evaluate the following item against the project baseline and context.

Procedural Rules:
{context.get("procedural_rules")}

Item Type: {item_type}
{item_desc}

Evidence Text from Document:
{item.get("evidence_text", "N/A")}

Baseline Evidence (from contract/EL/IFA):
{evidence_text if evidence_text.strip() else "No direct baseline evidence found for this item."}

Your task:
1. Determine if this item is within the original project scope or is a deviation. If baseline evidence contradicts itself (e.g. an original EL vs an Addendum), you MUST prioritize the information from the most recent/latest document source.
2. Classify the risk category
3. Calculate a risk score from 0 to 100
4. Provide a detailed description explaining WHY you assigned this risk score

Risk Score Guidelines:
- 0-20 (LOW): Item is clearly within scope, no concerns
- 21-40 (MEDIUM): Minor deviation or potential concern worth monitoring
- 41-70 (HIGH): Significant deviation from baseline, scope creep detected, or deliverable at risk
- 71-100 (CRITICAL): Major out-of-scope work, critical deadline miss, or requires immediate escalation

Risk Categories:
- SCOPE_CREEP: Work being done that was not in the original contract
- DELAY: Timeline slippage, missed deadlines, delayed milestones
- MISSING_DELIVERABLE: A contracted deliverable is not being tracked or delivered
- DEPENDENCY: External dependency creating risk
- STAKEHOLDER: Stakeholder-related concern (communication gaps, approval delays)
- GENERAL: Does not fit a specific category

Output MUST be a valid JSON object:
{{
  "is_out_of_scope": false,
  "risk_score": 25,
  "risk_level": "MEDIUM",
  "risk_category": "SCOPE_CREEP",
  "description": "This task was not mentioned in the original engagement letter. The client requested it during the project, which constitutes scope creep. The team has already spent 20 hours on this without a formal change request.",
  "reasoning": "Based on baseline evidence, the original scope only covered web platform design. The iOS app design was not part of the contract.",
  "confidence": 0.85,
  "requires_escalation": false
}}

Rules:
- "risk_score" MUST be an integer between 0 and 100.
- "risk_level" MUST be one of: "LOW", "MEDIUM", "HIGH", "CRITICAL".
- "risk_category" MUST be one of: "SCOPE_CREEP", "DELAY", "MISSING_DELIVERABLE", "DEPENDENCY", "STAKEHOLDER", "GENERAL".
- "description" MUST be 2-3 sentences explaining WHY this risk score was assigned. Include specific evidence.
- "confidence" MUST be a float between 0.0 and 1.0.
"""
        draft = LLMService.generate_json(draft_prompt)
        
        # 5. Verify Phase (Reflexion step 2) — cross-check with rules
        verify_prompt = f"""Review this draft risk assessment for accuracy and consistency.

Rules:
{context.get("procedural_rules")}

Draft Assessment:
{draft}

Baseline Evidence:
{evidence_text if evidence_text.strip() else "No baseline evidence found."}

Verification checklist:
1. If "is_out_of_scope" is true, does the description cite specific missing scope evidence?
2. Is the risk_score consistent with the risk_level? (0-20=LOW, 21-40=MEDIUM, 41-70=HIGH, 71-100=CRITICAL)
3. Is the description specific enough to explain WHY this score was given?
4. Does the risk_category match the actual type of risk?

Output the finalized assessment as a valid JSON object with the same schema. Fix any inconsistencies.
{{
  "is_out_of_scope": false,
  "risk_score": 25,
  "risk_level": "MEDIUM",
  "risk_category": "SCOPE_CREEP",
  "description": "Detailed 2-3 sentence reason",
  "reasoning": "Detailed reasoning with evidence citations",
  "confidence": 0.85,
  "requires_escalation": false
}}
"""
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
