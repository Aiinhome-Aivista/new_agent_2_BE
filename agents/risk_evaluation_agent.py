import json
import re
from typing import Callable, Optional
from services.llm_service import LLMService
from services.project_knowledge_service import ProjectKnowledgeService
from services.risk_config_service import RiskConfigurationService
from services.risk_scoring_engine import RiskScoringEngine
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

# (re already imported at top)

def _strip_date_from_title(title: str) -> str:
    """
    Remove date suffixes from tracker titles.
    e.g. "UAT - 15 May 2026" → "UAT"
         "Go Live - 30 June 2026" → "Go Live"
    """
    # Remove " - DD Month YYYY" or " - Month YYYY" or " - YYYY-MM-DD" patterns
    cleaned = re.sub(r'\s*[-–]\s*\d{1,2}\s+\w+\s+\d{4}', '', title)
    cleaned = re.sub(r'\s*[-–]\s*\d{4}-\d{2}-\d{2}', '', cleaned)
    cleaned = re.sub(r'\s*[-–]\s*\w+\s+\d{4}', '', cleaned)
    return cleaned.strip()


def _resolve_tracker_title(activity_name: str, matched_baseline_item: str,
                           in_scope_items: list, all_baseline_items: list = None) -> tuple:
    """
    Implements the Tracker Title Priority rule:
      1. Matched IN_SCOPE baseline item name  → (canonical_name, True)
      2. Matched OUT_OF_SCOPE baseline item   → (canonical_name, False)
      3. Normalized activity name             → (activity_name, False)

    Uses all_baseline_items to resolve canonical names for OUT_OF_SCOPE entries.
    Returns (canonical_title, is_confirmed_in_scope)
    """
    all_items = (all_baseline_items or []) + (in_scope_items or [])

    if matched_baseline_item:
        norm_match = _normalize(matched_baseline_item)

        # Priority 1: check IN_SCOPE items first
        for si in in_scope_items:
            si_norm = _normalize(si["name"])
            if norm_match == si_norm or norm_match in si_norm or si_norm in norm_match:
                return _strip_date_from_title(si["name"]), True

        # Priority 2: check ALL baseline items (including OUT_OF_SCOPE exclusions)
        for si in all_items:
            si_norm = _normalize(si["name"])
            if norm_match == si_norm or norm_match in si_norm or si_norm in norm_match:
                return _strip_date_from_title(si["name"]), False

        # Priority 2 fallback: use whatever the LLM said (already normalized)
        return _strip_date_from_title(matched_baseline_item), False

    # Priority 3: no baseline match — use normalized activity name
    return _strip_date_from_title(activity_name), False


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
                          activity_map: dict = None, request_map: dict = None,
                          emit: Optional[Callable[[str, int], None]] = None) -> dict:
        activity_map = activity_map or {}
        request_map = request_map or {}

        def _emit(step: str, pct: int):
            if emit:
                emit(step, pct)

        # ── Load config from DB via caching service (Phase 2 inputs) ──────────
        # These come from risk_parameter_config, risk_threshold_config, etc.
        # Config changes take effect immediately without deployment.
        risk_params    = RiskConfigurationService.get_parameters(db_cursor)
        risk_thresholds = RiskConfigurationService.get_thresholds(db_cursor)
        risk_rules     = RiskConfigurationService.get_rules(db_cursor)
        impact_matrix  = RiskConfigurationService.get_impact_matrix(db_cursor)
        alert_rules    = RiskConfigurationService.get_alert_rules(db_cursor)

        _emit("Loading Project Baseline", 10)
        scope_items = ProjectKnowledgeService.get_approved_baseline(db_cursor, project_id)

        # Fetch ALL baseline items (IN_SCOPE + OUT_OF_SCOPE) for canonical name resolution.
        all_baseline_items = ProjectKnowledgeService.get_full_baseline(db_cursor, project_id)

        # STEP 2: Extract activities once — LLM CALL #1
        _emit("Extracting Activities", 35)
        raw_activities = ActivityExtractorAgent.extract_activities(document_text)

        # ── Post-extraction: resolve canonical title immediately, then deduplicate ──
        # Root cause fix: deduplication must happen on the CANONICAL title, not the raw
        # normalized activity string. Otherwise "Evaluate SAP Integration Request" and
        # "SAP Integration Request Assessment" produce two different norm_keys and both
        # survive into the pipeline, creating duplicate tracker records.
        #
        # Pipeline:
        #   raw_activity → _resolve_tracker_title → canonical_title → dedup by canonical
        seen_canonical = set()
        cleaned_activities = []  # list of {activity, canonical_title, is_in_scope, source_sentence}
        for item in raw_activities:
            name = str(item.get("activity", "")).strip()
            if not name:
                continue

            # Resolve canonical title against full baseline RIGHT NOW, before anything else
            canonical_title, is_in_scope = _resolve_tracker_title(
                name, None, scope_items, all_baseline_items
            )

            # Dedup key is the canonical title — so "SAP Integration Request Assessment"
            # and "Evaluate SAP Integration Request" both resolve to "SAP Integration" → merged
            dedup_key = _normalize(canonical_title)
            if dedup_key in seen_canonical:
                print(f"  [Dedup] Merged '{name}' → already seen as '{canonical_title}'")
                continue
            seen_canonical.add(dedup_key)

            cleaned_activities.append({
                "activity": name,
                "canonical_title": canonical_title,
                "is_in_scope": is_in_scope,
                "source_sentence": item.get("source_sentence", name)
            })

        # ── STEP 3: Deterministic Scope Matching (no LLM) ─────────────────────
        # High-confidence matches → IN_SCOPE immediately, skip LLM.
        # Ambiguous activities → go to hybrid retrieval + LLM.
        deterministic_in_scope = []   # Matched with high confidence
        ambiguous_activities = []      # Need ChromaDB + LLM evaluation

        for act_item in cleaned_activities:
            activity_name = act_item["activity"]
            canonical_title = act_item["canonical_title"]
            is_already_in_scope = act_item["is_in_scope"]
            source_sentence = act_item["source_sentence"]
            matched_si, confidence, match_type = _deterministic_match(activity_name, scope_items)

            # Also try matching on canonical_title if activity_name didn't hit
            if confidence < cls.DETERMINISTIC_CONFIDENCE_THRESHOLD:
                matched_si2, confidence2, match_type2 = _deterministic_match(canonical_title, scope_items)
                if confidence2 > confidence:
                    matched_si, confidence, match_type = matched_si2, confidence2, match_type2

            if confidence >= cls.DETERMINISTIC_CONFIDENCE_THRESHOLD or is_already_in_scope:
                # High-confidence match or pre-resolved IN_SCOPE — mark without LLM
                resolved_name = matched_si["name"] if matched_si else canonical_title
                final_title, _ = _resolve_tracker_title(activity_name, resolved_name, scope_items, all_baseline_items)
                print(f"  [Deterministic] '{activity_name}' → IN_SCOPE as '{final_title}' (confidence: {confidence}%)")
                deterministic_in_scope.append({
                    "activity": activity_name,
                    "classification": "IN_SCOPE",
                    "deliverable": final_title,
                    "canonical_title": final_title,
                    "confidence": confidence,
                    "match_type": match_type,
                    "source_sentence": source_sentence
                })
            else:
                # Ambiguous — needs deeper analysis
                ambiguous_activities.append({
                    "activity": activity_name,
                    "canonical_title": canonical_title,
                    "source_sentence": source_sentence,
                    "matched_si": matched_si,
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
                "source_sentence": item.get("source_sentence", activity_name),
                "context": context,
                "matched_si": matched_si
            })

        # STEP 5: Batch LLM Risk Evaluation — LLM CALL #2
        _emit("Running In-Scope Evaluation Agent", 55)
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
            reasoning = result.get("reasoning", "")
            matched_baseline_name = result.get("matched_baseline_item")

            # Phase 1 multi-dimensional signals from LLM
            signals = result.get("signals", {})
            if not isinstance(signals, dict):
                signals = {}
            confidence   = float(result.get("confidence", 0.7))
            business_impact = result.get("business_impact", "LOW")

            # Fallback: use the pre-resolved canonical_title from the extraction phase
            pre_resolved = activities_with_contexts[i].get("canonical_title") if i < len(activities_with_contexts) else None

            # Fallback chain: LLM match → deterministic partial match → pre-resolved canonical
            if not matched_baseline_name and i < len(activities_with_contexts):
                si = activities_with_contexts[i].get("matched_si")
                if si:
                    matched_baseline_name = si["name"]
            if not matched_baseline_name and pre_resolved:
                matched_baseline_name = pre_resolved

            # Get source_sentence as evidence
            source_sentence = ""
            if i < len(activities_with_contexts):
                source_sentence = activities_with_contexts[i].get("source_sentence", activity_name)

            # TRACKER TITLE PRIORITY: resolve canonical title and confirm if IN_SCOPE
            canonical_title, is_confirmed_in_scope = _resolve_tracker_title(
                activity_name, matched_baseline_name, scope_items, all_baseline_items
            )

            # CRITICAL: If item is confirmed IN_SCOPE, it can NEVER be SCOPE_CREEP.
            if is_confirmed_in_scope and risk_cat == "SCOPE_CREEP":
                print(f"  [Override] '{activity_name}' is IN approved baseline — overriding SCOPE_CREEP to NONE")
                risk_cat = "NONE"

            # ── PHASE 2: Deterministic Weighted Scoring ───────────────────────
            # LLM (Phase 1) diagnosed WHAT risk exists and which signals are present.
            # RiskScoringEngine (Phase 2) calculates HOW SEVERE deterministically.
            item_risk_score, score_breakdown = RiskScoringEngine.calculate(
                risk_category=risk_cat,
                signals=signals,
                confidence=confidence,
                business_impact=business_impact,
                params=risk_params,
                impact_matrix=impact_matrix,
                rules=risk_rules,
            )
            item_risk_level = RiskConfigurationService.classify_severity(item_risk_score, risk_thresholds)

            # Evidence-backed reasoning with full score breakdown
            full_evidence = RiskScoringEngine.format_reasoning(
                score=item_risk_score,
                severity=item_risk_level,
                breakdown=score_breakdown,
                mom_evidence=source_sentence,
                llm_reasoning=reasoning,
            )

            if risk_cat == "SCOPE_CREEP":
                out_of_scope_activities.append({
                    "activity": canonical_title,
                    "classification": "OUT_OF_SCOPE" if item_risk_level in ["HIGH", "CRITICAL"] else "POSSIBLE_SCOPE_CREEP",
                    "reason": full_evidence,
                    "similar_deliverable": matched_baseline_name or canonical_title,
                    "confidence": int(confidence * 100),
                    "risk_score": item_risk_score,
                    "risk_level": item_risk_level,
                })
            elif risk_cat in ["DELAY", "DEPENDENCY", "BLOCKED"]:
                timeline_deliverables.append({
                    "deliverable": canonical_title,
                    "expected_date": "Unknown",
                    "current_status": "Delayed" if risk_cat == "DELAY" else "Blocked",
                    "delay_days": 0,
                    "blockers": [source_sentence] if source_sentence else [],
                    "dependency_status": risk_cat,
                    "risk": item_risk_level,
                    "risk_score": item_risk_score,
                    "reasoning": full_evidence,
                })
            else:
                in_scope_activities.append({
                    "activity": canonical_title,
                    "classification": "IN_SCOPE",
                    "deliverable": canonical_title,
                    "confidence": int(confidence * 100),
                })

        in_scope_result = {"activities": in_scope_activities}
        out_of_scope_result = {"activities": out_of_scope_activities}
        timeline_result = {"deliverables": timeline_deliverables}

        # STEP 7: Aggregation — LLM CALL #3
        _emit("Calculating Risk Score", 80)
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

        # STEP 8: Persist to DB
        _emit("Saving Results", 92)
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
            oos_name = oos_item.get('activity', 'Unknown')  # Already canonical from pipeline

            # Look in ALL baseline items (not just IN_SCOPE) for canonical wording + ID
            matched_scope_id = None
            matched_scope_name = None
            for si in all_baseline_items:
                si_norm = _normalize(si["name"])
                oos_norm = _normalize(oos_name)
                if si_norm == oos_norm or si_norm in oos_norm or oos_norm in si_norm:
                    matched_scope_id = si["id"]
                    matched_scope_name = si["name"]  # Exact baseline wording
                    break

            card_title = matched_scope_name if matched_scope_name else oos_name

            oos_name_clean = oos_name.lower().strip()
            ref_id = None
            for name, act_id in activity_map.items():
                if name in oos_name_clean or oos_name_clean in name:
                    ref_id = act_id
                    break

            # Phase 2 scores already calculated — read directly from the item
            item_risk_score = oos_item.get('risk_score', 50)
            item_risk_level = oos_item.get('risk_level', 'MEDIUM')
            confidence_val  = oos_item.get('confidence', 70) / 100.0
            full_reasoning  = oos_item.get('reason', f'Activity: {oos_name}')

            TrackerAuditAgent.persist_tracker_item(
                db_cursor, project_id, document_id, 'ACTIVITY',
                True, item_risk_score, item_risk_level, 'SCOPE_CREEP',
                confidence_val, full_reasoning, True,
                title=card_title, reference_id=ref_id
            )

            # Use alert threshold from DB config (not hardcoded 70)
            oos_alert_rule = alert_rules.get(item_risk_level, {})
            if oos_alert_rule.get('send_email') and item_risk_score >= oos_alert_rule.get('min_score_threshold', 70):
                AlertingAgent.dispatch_alert(
                    project_id, f"Scope Creep Risk: {card_title}",
                    full_reasoning, stakeholders, db_cursor=db_cursor
                )

        # Persist Timeline / Delay risks to tracker
        for deliv in timeline_result.get("deliverables", []):
            deliv_name = deliv.get('deliverable', 'Unknown')  # Already canonical from pipeline

            # Resolve against ALL baseline items for canonical baseline wording
            matched_scope_name = None
            for si in all_baseline_items:
                si_norm = _normalize(si["name"])
                deliv_norm = _normalize(deliv_name)
                if si_norm == deliv_norm or si_norm in deliv_norm or deliv_norm in si_norm:
                    matched_scope_name = si["name"]
                    break

            card_title = matched_scope_name if matched_scope_name else deliv_name

            deliv_name_clean = deliv_name.lower().strip()
            ref_id = None
            for name, act_id in activity_map.items():
                if name in deliv_name_clean or deliv_name_clean in name:
                    ref_id = act_id
                    break

            # Phase 2 scores already calculated — read directly from item
            item_risk_score = deliv.get('risk_score', 40)
            item_risk_level = deliv.get('risk', 'MEDIUM')
            full_reasoning  = deliv.get('reasoning', f"Deliverable: {deliv_name}")

            TrackerAuditAgent.persist_tracker_item(
                db_cursor, project_id, document_id, 'ACTION_ITEM',
                False, item_risk_score, item_risk_level, 'DELAY',
                1.0, full_reasoning, True,
                title=card_title, reference_id=ref_id
            )

            # Use alert threshold from DB config (not hardcoded 70)
            delay_alert_rule = alert_rules.get(item_risk_level, {})
            if delay_alert_rule.get('send_email') and item_risk_score >= delay_alert_rule.get('min_score_threshold', 70):
                AlertingAgent.dispatch_alert(
                    project_id, f"Delay Risk: {card_title}",
                    full_reasoning, stakeholders, db_cursor=db_cursor
                )

        _emit("Completed", 100)
        return {
            "overallRisk": overall_risk,
            "riskScore": risk_score,
            "summary": summary,
            "recommendations": recommendations,
            "subAgentResults": sub_agent_results
        }
