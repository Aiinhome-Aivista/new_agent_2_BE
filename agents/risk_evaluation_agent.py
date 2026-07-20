import json
from services.llm_service import LLMService
from agents.risk_evaluator_subagents import (
    InScopeEvaluationAgent,
    OutOfScopeDetectionAgent,
    DeliverableTimelineEvaluationAgent
)

class RiskEvaluationAgent:
    @classmethod
    def evaluate_document(cls, project_id: int, document_id: int, document_text: str, db_cursor) -> dict:
        """
        Orchestrates the 3 sub-agents, aggregates their results, calculates overall risk,
        stores the history in `risk_evaluations`, and updates `tracker_items`.
        """
        # 1. Run Sub-Agent 1 (In-Scope)
        in_scope_result = InScopeEvaluationAgent.evaluate(project_id, document_text)
        
        # 2. Run Sub-Agent 2 (Out-of-Scope)
        # We pass the activities extracted from Sub-Agent 1 to Sub-Agent 2
        activities = in_scope_result.get("activities", [])
        out_of_scope_result = OutOfScopeDetectionAgent.detect(project_id, activities, document_text)
        
        # 3. Run Sub-Agent 3 (Deliverables & Timeline)
        timeline_result = DeliverableTimelineEvaluationAgent.evaluate(project_id, document_text)
        
        # 4. Aggregate and Generate Overall Risk Summary
        aggregation_prompt = f"""You are the Parent Risk Evaluation Agent.
Your job is to aggregate the outputs of three specialized sub-agents and generate an overall risk summary for the project.

Sub-Agent 1 (In-Scope Evaluation):
{json.dumps(in_scope_result, indent=2)}

Sub-Agent 2 (Out-of-Scope Detection):
{json.dumps(out_of_scope_result, indent=2)}

Sub-Agent 3 (Deliverables & Timeline):
{json.dumps(timeline_result, indent=2)}

Calculate the Overall Risk Score (0-100) and Overall Risk Level (LOW, MEDIUM, HIGH, CRITICAL) considering:
- Number of Out-of-Scope Activities
- Number of Delayed Deliverables
- Missing Deliverables
- Active Blockers
- Progress against Milestones

Output MUST be a valid JSON object matching this exact schema:
{{
   "overallRisk": "HIGH",
   "riskScore": 72,
   "summary": "Project risk increased due to delayed deliverables and newly detected scope creep.",
   "recommendations": [
      "Submit a change request for SAP Integration.",
      "Escalate the UI Design blocker to the client."
   ]
}}
"""
        final_assessment = LLMService.generate_json(aggregation_prompt)
        
        overall_risk = final_assessment.get("overallRisk", "LOW")
        risk_score = final_assessment.get("riskScore", 0)
        summary = final_assessment.get("summary", "")
        recommendations = final_assessment.get("recommendations", [])
        
        sub_agent_results = {
            "in_scope": in_scope_result,
            "out_of_scope": out_of_scope_result,
            "timeline": timeline_result
        }
        
        # 5. Store in Risk History (risk_evaluations)
        insert_eval_sql = """
            INSERT INTO risk_evaluations 
            (project_id, document_id, overall_risk_score, overall_risk_level, summary, recommendations, sub_agent_results)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
        """
        db_cursor.execute(insert_eval_sql, (
            project_id, document_id, risk_score, overall_risk, summary, 
            json.dumps(recommendations), json.dumps(sub_agent_results)
        ))
        
        # 6. Update Scope Tracker (tracker_items)
        # For simplicity and UI compatibility, we will map the OutOfScope and Delayed Deliverables to tracker_items
        for oos_item in out_of_scope_result.get("activities", []):
            reasoning = f"{oos_item.get('reason', '')} (Confidence: {oos_item.get('confidence', 0)}%)"
            db_cursor.execute("""
                INSERT INTO tracker_items 
                (project_id, source_document_id, item_type, is_out_of_scope, risk_score, risk_level, risk_category, confidence, reasoning, requires_escalation, status)
                VALUES (%s, %s, 'ACTIVITY', 1, %s, %s, 'SCOPE_CREEP', %s, %s, 1, 'OPEN')
            """, (
                project_id, document_id, 
                80 if oos_item.get('classification') == 'OUT_OF_SCOPE' else 50,
                'HIGH' if oos_item.get('classification') == 'OUT_OF_SCOPE' else 'MEDIUM',
                oos_item.get('confidence', 50) / 100.0,
                f"Activity: {oos_item.get('activity')}\n{reasoning}"
            ))
            
        for deliv in timeline_result.get("deliverables", []):
            if deliv.get('risk') in ['HIGH', 'CRITICAL'] or deliv.get('current_status') == 'Delayed':
                reasoning = f"Delayed by {deliv.get('delay_days', 0)} days. Blockers: {', '.join(deliv.get('blockers', []))}"
                db_cursor.execute("""
                    INSERT INTO tracker_items 
                    (project_id, source_document_id, item_type, is_out_of_scope, risk_score, risk_level, risk_category, confidence, reasoning, requires_escalation, status)
                    VALUES (%s, %s, 'ACTION_ITEM', 0, %s, %s, 'DELAY', 1.0, %s, 1, 'OPEN')
                """, (
                    project_id, document_id, 
                    85 if deliv.get('risk') == 'CRITICAL' else 65,
                    deliv.get('risk', 'HIGH'),
                    f"Deliverable: {deliv.get('deliverable')}\n{reasoning}"
                ))

        return {
            "overallRisk": overall_risk,
            "riskScore": risk_score,
            "summary": summary,
            "recommendations": recommendations,
            "subAgentResults": sub_agent_results
        }
