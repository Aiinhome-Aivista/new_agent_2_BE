import json
from services.llm_service import LLMService
from agents.risk_evaluator_subagents import (
    InScopeEvaluationAgent,
    OutOfScopeDetectionAgent,
    DeliverableTimelineEvaluationAgent
)
from agents.tracker_audit_agent import TrackerAuditAgent
from agents.alerting_agent import AlertingAgent


def _fetch_scope_items(db_cursor, project_id: int) -> list:
    """
    Fetch IN_SCOPE items ONLY from the latest APPROVED baseline for this project,
    and ONLY those that are still ACTIVE.

    Why APPROVED only?
    - DRAFT baselines contain items that are still under review and not yet
      contractually agreed upon with the client.
    - Using DRAFT items for risk evaluation would cause false negatives
      (agent marks something as IN_SCOPE when the client never signed off on it).
    - When the Engagement Manager approves a new baseline version, the NEXT
      document processing run will automatically use that new approved list.
    """
    try:
        db_cursor.execute("""
            SELECT si.id, si.name, si.scope_type
            FROM scope_items si
            JOIN scope_baselines sb ON si.baseline_id = sb.id
            WHERE si.project_id = %s
              AND si.scope_type = 'IN_SCOPE'
              AND sb.status = 'APPROVED'
              AND si.completion_status = 'ACTIVE'
            ORDER BY sb.id DESC, si.id ASC
        """, (project_id,))
        return db_cursor.fetchall() or []
    except Exception as e:
        print(f"Warning: Could not fetch approved scope items: {e}")
        return []


def _match_to_scope_item(detected_name: str, similar_deliverable: str, scope_items: list):
    """
    Fuzzy-match a detected activity/deliverable name to an actual scope item.
    Returns (scope_item_id, scope_item_name) or (None, None) if no match found.
    Priority: 
      1. Match similar_deliverable field (what LLM says it's similar to)
      2. Substring match on detected_name
    """
    detected_lower = detected_name.lower().strip()
    similar_lower = (similar_deliverable or '').lower().strip()

    # Priority 1: match via similar_deliverable (LLM already did the reasoning)
    if similar_lower and similar_lower not in ('n/a', 'none', ''):
        for si in scope_items:
            si_name_lower = si['name'].lower()
            if similar_lower in si_name_lower or si_name_lower in similar_lower:
                return si['id'], si['name']
            # Word overlap check
            similar_words = set(similar_lower.split())
            si_words = set(si_name_lower.split())
            if len(similar_words & si_words) >= 2:
                return si['id'], si['name']

    # Priority 2: substring match on the raw detected activity name
    for si in scope_items:
        si_name_lower = si['name'].lower()
        if detected_lower in si_name_lower or si_name_lower in detected_lower:
            return si['id'], si['name']
        # Word overlap check (at least 2 words match)
        detected_words = set(detected_lower.split())
        si_words = set(si_name_lower.split())
        if len(detected_words & si_words) >= 2:
            return si['id'], si['name']

    return None, None


class RiskEvaluationAgent:
    @classmethod
    def evaluate_document(cls, project_id: int, document_id: int, document_text: str, db_cursor,
                          activity_map: dict = None, request_map: dict = None) -> dict:
        """
        Orchestrates the 3 sub-agents, aggregates their results, calculates overall risk,
        stores the history in `risk_evaluations`, and updates `tracker_items` with
        the actual scope item name as the card title.
        """
        activity_map = activity_map or {}
        request_map = request_map or {}

        # Fetch approved scope items from MySQL — these are the "ground truth" scope names
        scope_items = _fetch_scope_items(db_cursor, project_id)

        # 1. Run Sub-Agent 1 (In-Scope) — receives BOTH MySQL scope list + ChromaDB evidence
        in_scope_result = InScopeEvaluationAgent.evaluate(
            project_id, document_text, mysql_scope_items=scope_items
        )

        # 2. Run Sub-Agent 2 (Out-of-Scope) — receives BOTH MySQL scope list + ChromaDB exclusion evidence
        activities = in_scope_result.get("activities", [])
        out_of_scope_result = OutOfScopeDetectionAgent.detect(
            project_id, activities, document_text, mysql_scope_items=scope_items
        )

        # 3. Run Sub-Agent 3 (Deliverables & Timeline) — receives MySQL scope list for accurate name mapping
        timeline_result = DeliverableTimelineEvaluationAgent.evaluate(
            project_id, document_text, mysql_scope_items=scope_items
        )

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

        # 6. Fetch stakeholders for alerts
        db_cursor.execute("SELECT email, role FROM stakeholders WHERE project_id = %s", (project_id,))
        stakeholders = db_cursor.fetchall()

        # 7. Persist Out-of-Scope risks — title = matched scope item name
        for oos_item in out_of_scope_result.get("activities", []):
            oos_name = oos_item.get('activity', 'Unknown')
            similar_deliverable = oos_item.get('similar_deliverable', '')

            # Match to actual scope item from MySQL
            matched_scope_id, matched_scope_name = _match_to_scope_item(
                oos_name, similar_deliverable, scope_items
            )

            # Fuzzy match activity_map for reference_id (links to project_activities row)
            oos_name_clean = oos_name.lower().strip()
            ref_id = None
            for name, act_id in activity_map.items():
                if name in oos_name_clean or oos_name_clean in name:
                    ref_id = act_id
                    break

            confidence_val = oos_item.get('confidence', 50) / 100.0
            reasoning = f"{oos_item.get('reason', '')} (Confidence: {oos_item.get('confidence', 0)}%)"
            full_reasoning = f"Activity: {oos_name}\n{reasoning}"
            item_risk_score = 80 if oos_item.get('classification') == 'OUT_OF_SCOPE' else 50
            item_risk_level = 'HIGH' if oos_item.get('classification') == 'OUT_OF_SCOPE' else 'MEDIUM'

            # Use the matched scope item name as title; fallback to detected activity name
            card_title = matched_scope_name if matched_scope_name else oos_name

            TrackerAuditAgent.persist_tracker_item(
                db_cursor, project_id, document_id, 'ACTIVITY',
                True, item_risk_score, item_risk_level, 'SCOPE_CREEP',
                confidence_val, full_reasoning, True,
                title=card_title, reference_id=ref_id
            )

            if item_risk_score >= 70:
                AlertingAgent.dispatch_alert(
                    project_id, f"Scope Creep Risk: {card_title}",
                    full_reasoning, stakeholders, db_cursor=db_cursor
                )

        # 8. Persist Delayed Deliverable risks — title = matched scope item name
        for deliv in timeline_result.get("deliverables", []):
            if deliv.get('risk') in ['HIGH', 'CRITICAL'] or deliv.get('current_status') == 'Delayed':
                deliv_name = deliv.get('deliverable', 'Unknown')

                # Match to actual scope item
                matched_scope_id, matched_scope_name = _match_to_scope_item(
                    deliv_name, '', scope_items
                )

                deliv_name_clean = deliv_name.lower().strip()
                ref_id = None
                for name, act_id in activity_map.items():
                    if name in deliv_name_clean or deliv_name_clean in name:
                        ref_id = act_id
                        break

                reasoning = f"Delayed by {deliv.get('delay_days', 0)} days. Blockers: {', '.join(deliv.get('blockers', []))}"
                full_reasoning = f"Deliverable: {deliv_name}\n{reasoning}"
                item_risk_score = 85 if deliv.get('risk') == 'CRITICAL' else 65
                item_risk_level = deliv.get('risk', 'HIGH')

                # Use the matched scope item name as title; fallback to deliverable name
                card_title = matched_scope_name if matched_scope_name else deliv_name

                TrackerAuditAgent.persist_tracker_item(
                    db_cursor, project_id, document_id, 'ACTION_ITEM',
                    False, item_risk_score, item_risk_level, 'DELAY',
                    1.0, full_reasoning, True,
                    title=card_title, reference_id=ref_id
                )

                if item_risk_score >= 70:
                    AlertingAgent.dispatch_alert(
                        project_id, f"Delay Risk: {card_title}",
                        full_reasoning, stakeholders, db_cursor=db_cursor
                    )

        return {
            "overallRisk": overall_risk,
            "riskScore": risk_score,
            "summary": summary,
            "recommendations": recommendations,
            "subAgentResults": sub_agent_results
        }
