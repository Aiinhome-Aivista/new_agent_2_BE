import json
from services.llm_service import LLMService
from services.project_knowledge_service import ProjectKnowledgeService
from agents.risk_evaluator_subagents import ActivityExtractorAgent, BatchActivityRiskAgent
from agents.tracker_audit_agent import TrackerAuditAgent
from agents.alerting_agent import AlertingAgent

# ---------------------------------------------------------------------------
# DETERMINISTIC MATCHING HELPERS
# These run BEFORE any LLM call. If confidence is high enough, LLM is skipped.
# ---------------------------------------------------------------------------

def _normalize(text: str) -> str:
    """Normalize text for matching: lowercase, strip, remove common filler."""
    stop_words = {"the", "a", "an", "of", "for", "in", "on", "to", "and", "or", "is", "was", "has", "have"}
    words = text.lower().strip().split()
    return " ".join(w for w in words if w not in stop_words)


def _deterministic_match(activity_name: str, scope_items: list) -> tuple:
    """
    STEP 3: Deterministic Scope Matching.
    Tries exact → normalized → substring → token overlap in that order.
    
    Returns (scope_item_dict, confidence_score 0-100, match_type)
    If no match found, returns (None, 0, None).
    
    Per the spec: if confidence >= threshold, DO NOT invoke LLM.
    """
    act_norm = _normalize(activity_name)
    act_words = set(act_norm.split())

    best_match = None
    best_score = 0
    best_type = None

    for si in scope_items:
        si_norm = _normalize(si["name"])
        si_words = set(si_norm.split())

        # 1. Exact match (normalized)
        if act_norm == si_norm:
            return si, 100, "exact"

        # 2. Substring match
        if act_norm in si_norm or si_norm in act_norm:
            score = 90
            if score > best_score:
                best_match, best_score, best_type = si, score, "substring"
            continue

        # 3. Token overlap (Jaccard-style)
        if act_words and si_words:
            overlap = len(act_words & si_words)
            union = len(act_words | si_words)
            if union > 0:
                jaccard = (overlap / union) * 100
                # Require at least 2 common words AND >50% Jaccard
                if overlap >= 2 and jaccard > 50 and jaccard > best_score:
                    best_match, best_score, best_type = si, int(jaccard), "token_overlap"

    return best_match, best_score, best_type


# ---------------------------------------------------------------------------
# RISK EVALUATION AGENT
# ---------------------------------------------------------------------------

class RiskEvaluationAgent:
    """
    Implements the Activity-Centric risk evaluation flow from risk-tracker-improve-1.md.
    
    Total LLM calls: 3 max (regardless of document size or activity count)
      - Call 1: Activity Extraction
      - Call 2: Batch Risk Evaluation (ambiguous activities only, 0 if all matched)  
      - Call 3: Risk Aggregation
    
    Deterministically-matched activities SKIP the LLM entirely.
    """

    # Confidence threshold above which LLM evaluation is skipped
    DETERMINISTIC_CONFIDENCE_THRESHOLD = 85

    @classmethod
    def evaluate_document(cls, project_id: int, document_id: int, document_text: str, db_cursor,
                          activity_map: dict = None, request_map: dict = None) -> dict:
        activity_map = activity_map or {}
        request_map = request_map or {}

        # ===========================================================
        # STEP 1: Fetch approved baseline from MySQL (no LLM)
        # ===========================================================
        scope_items = ProjectKnowledgeService.get_approved_baseline(db_cursor, project_id)

        # ===========================================================
        # STEP 2: Extract activities once — LLM CALL #1
        # The document is sent to the LLM exactly once for extraction.
        # ===========================================================
        raw_activities = ActivityExtractorAgent.extract_activities(document_text)

        # Deduplicate
        seen = set()
        cleaned_activities = []
        for item in raw_activities:
            name = str(item.get("activity", "")).strip()
            if not name or name.lower() in seen:
                continue
            seen.add(name.lower())
            cleaned_activities.append(name)

        # ===========================================================
        # STEP 3: Deterministic Scope Matching (no LLM)
        # High-confidence matches → IN_SCOPE immediately, skip LLM.
        # Ambiguous activities → go to hybrid retrieval + LLM.
        # ===========================================================
        deterministic_in_scope = []   # Matched with high confidence
        ambiguous_activities = []      # Need ChromaDB + LLM evaluation

        for activity_name in cleaned_activities:
            matched_si, confidence, match_type = _deterministic_match(activity_name, scope_items)

            if confidence >= cls.DETERMINISTIC_CONFIDENCE_THRESHOLD:
                # High-confidence match — mark as IN_SCOPE without LLM
                print(f"  [Deterministic] '{activity_name}' → IN_SCOPE via {match_type} (confidence: {confidence}%)")
                deterministic_in_scope.append({
                    "activity": activity_name,
                    "classification": "IN_SCOPE",
                    "deliverable": matched_si["name"],
                    "confidence": confidence,
                    "match_type": match_type
                })
            else:
                # Ambiguous — needs deeper analysis
                ambiguous_activities.append({
                    "activity": activity_name,
                    "matched_si": matched_si,  # Could be None or a low-confidence match
                    "confidence": confidence
                })

        # ===========================================================
        # STEP 4: Hybrid Retrieval for ambiguous activities (no LLM)
        # Per-activity compact context using MySQL metadata + ChromaDB.
        # ===========================================================
        activities_with_contexts = []
        for item in ambiguous_activities:
            activity_name = item["activity"]
            matched_si = item["matched_si"]

            # Build per-activity compact context
            context = ProjectKnowledgeService.get_activity_context(
                project_id=project_id,
                activity_name=activity_name,
                matched_scope_item=matched_si
            )
            activities_with_contexts.append({
                "activity": activity_name,
                "context": context,
                "matched_si": matched_si
            })

        # ===========================================================
        # STEP 5: Batch LLM Risk Evaluation — LLM CALL #2
        # ALL ambiguous activities are evaluated in ONE single LLM call.
        # If all activities matched deterministically, this call is SKIPPED.
        # ===========================================================
        llm_risk_results = []
        if activities_with_contexts:
            print(f"  [LLM] Batch-evaluating {len(activities_with_contexts)} ambiguous activities...")
            llm_risk_results = BatchActivityRiskAgent.evaluate_batch(activities_with_contexts)

        # ===========================================================
        # STEP 6: Categorize all results
        # ===========================================================
        out_of_scope_activities = []
        timeline_deliverables = []
        in_scope_activities = list(deterministic_in_scope)  # Start with deterministic matches

        # Process LLM results for ambiguous activities
        for i, result in enumerate(llm_risk_results):
            activity_name = result.get("activity", "Unknown")
            risk_cat = result.get("risk_category", "NONE")
            risk_lvl = result.get("risk_level", "LOW")
            reasoning = result.get("reasoning", "")
            matched_name = result.get("matched_baseline_item")

            # Fallback: use the deterministic partial match if LLM didn't identify one
            if not matched_name and i < len(activities_with_contexts):
                si = activities_with_contexts[i].get("matched_si")
                if si:
                    matched_name = si["name"]

            mapped_deliv = matched_name or activity_name

            if risk_cat == "SCOPE_CREEP":
                out_of_scope_activities.append({
                    "activity": activity_name,
                    "classification": "OUT_OF_SCOPE" if risk_lvl in ["HIGH", "CRITICAL"] else "POSSIBLE_SCOPE_CREEP",
                    "reason": reasoning,
                    "similar_deliverable": mapped_deliv,
                    "confidence": 90
                })
            elif risk_cat in ["DELAY", "DEPENDENCY", "BLOCKED"]:
                timeline_deliverables.append({
                    "deliverable": mapped_deliv,
                    "expected_date": "Unknown",
                    "current_status": "Delayed" if risk_cat == "DELAY" else "Blocked",
                    "delay_days": 0,
                    "blockers": [reasoning],
                    "dependency_status": risk_cat,
                    "risk": risk_lvl
                })
            else:
                in_scope_activities.append({
                    "activity": activity_name,
                    "classification": "IN_SCOPE",
                    "deliverable": mapped_deliv,
                    "confidence": 90
                })

        in_scope_result = {"activities": in_scope_activities}
        out_of_scope_result = {"activities": out_of_scope_activities}
        timeline_result = {"deliverables": timeline_deliverables}

        # ===========================================================
        # STEP 7: Aggregation — LLM CALL #3
        # Produces overall risk score, summary, and recommendations.
        # ===========================================================
        aggregation_prompt = f"""You are the Risk Aggregation Agent.
Summarize the following risk evaluation results and compute an overall project risk score.

In-Scope Activities: {len(in_scope_activities)} (including {len(deterministic_in_scope)} confirmed by baseline matching)
Out-of-Scope / Scope Creep Items: {len(out_of_scope_activities)}
Delayed / Blocked Deliverables: {len(timeline_deliverables)}

Scope Creep Items:
{json.dumps(out_of_scope_activities, indent=2)}

Delayed / Blocked Items:
{json.dumps(timeline_deliverables, indent=2)}

Output MUST be a valid JSON object:
{{
   "overallRisk": "HIGH",
   "riskScore": 72,
   "summary": "2 sentence summary of overall project risk status.",
   "recommendations": [
      "One specific actionable recommendation per identified risk."
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

        # ===========================================================
        # STEP 8: Persist to DB
        # ===========================================================
        insert_eval_sql = """
            INSERT INTO risk_evaluations
            (project_id, document_id, overall_risk_score, overall_risk_level, summary, recommendations, sub_agent_results)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
        """
        db_cursor.execute(insert_eval_sql, (
            project_id, document_id, risk_score, overall_risk, summary,
            json.dumps(recommendations), json.dumps(sub_agent_results)
        ))

        # Fetch stakeholders for alerts
        db_cursor.execute("SELECT email, role FROM stakeholders WHERE project_id = %s", (project_id,))
        stakeholders = db_cursor.fetchall()

        # Persist Out-of-Scope risks to tracker
        for oos_item in out_of_scope_result.get("activities", []):
            oos_name = oos_item.get('activity', 'Unknown')
            similar_deliverable = oos_item.get('similar_deliverable', '')

            # Find matching scope item for reference
            matched_scope_id = None
            matched_scope_name = similar_deliverable
            for si in scope_items:
                if si["name"].lower() in similar_deliverable.lower() or similar_deliverable.lower() in si["name"].lower():
                    matched_scope_id = si["id"]
                    matched_scope_name = si["name"]
                    break

            oos_name_clean = oos_name.lower().strip()
            ref_id = None
            for name, act_id in activity_map.items():
                if name in oos_name_clean or oos_name_clean in name:
                    ref_id = act_id
                    break

            confidence_val = oos_item.get('confidence', 90) / 100.0
            reasoning = f"{oos_item.get('reason', '')} (Confidence: {oos_item.get('confidence', 90)}%)"
            full_reasoning = f"Activity: {oos_name}\n{reasoning}"
            item_risk_score = 80 if oos_item.get('classification') == 'OUT_OF_SCOPE' else 50
            item_risk_level = 'HIGH' if oos_item.get('classification') == 'OUT_OF_SCOPE' else 'MEDIUM'
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

        # Persist Timeline / Delay risks to tracker
        for deliv in timeline_result.get("deliverables", []):
            deliv_name = deliv.get('deliverable', 'Unknown')
            matched_scope_name = None
            for si in scope_items:
                if si["name"].lower() in deliv_name.lower() or deliv_name.lower() in si["name"].lower():
                    matched_scope_name = si["name"]
                    break

            deliv_name_clean = deliv_name.lower().strip()
            ref_id = None
            for name, act_id in activity_map.items():
                if name in deliv_name_clean or deliv_name_clean in name:
                    ref_id = act_id
                    break

            reasoning = f"Status: {deliv.get('current_status')}. Blockers: {', '.join(deliv.get('blockers', []))}"
            full_reasoning = f"Deliverable: {deliv_name}\n{reasoning}"
            item_risk_score = 85 if deliv.get('risk') == 'CRITICAL' else 65
            item_risk_level = deliv.get('risk', 'HIGH')
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
