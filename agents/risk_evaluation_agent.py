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
from services.milestone_dependency_service import MilestoneDependencyService
from services.dependency_classification_service import DependencyClassificationService
from services.category_assignment_engine import CategoryAssignmentEngine

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
    Implements the Tracker Title Priority rule by delegating to NormalizationService.
    Returns (canonical_title, is_confirmed_in_scope)
    """
    from services.normalization_service import NormalizationService
    return NormalizationService.resolve_canonical_entity(
        activity_name, matched_baseline_item, in_scope_items, all_baseline_items
    )


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
        impact_matrix  = RiskConfigurationService.get_impact_matrix(db_cursor)
        alert_rules    = RiskConfigurationService.get_alert_rules(db_cursor)

        _emit("Loading Project Baseline", 10)
        scope_items = ProjectKnowledgeService.get_approved_baseline(db_cursor, project_id)

        # Fetch ALL baseline items (IN_SCOPE + OUT_OF_SCOPE) for canonical name resolution.
        all_baseline_items = ProjectKnowledgeService.get_full_baseline(db_cursor, project_id)

        # Build dependency graph ONCE — used for both scoring and LLM context injection.
        # This reads deliverable_progress.dependencies to find which items block others.
        dependency_graph = MilestoneDependencyService.build_rich_dependency_graph(db_cursor, project_id)
        dependency_context_block = ProjectKnowledgeService.get_dependency_context_block(dependency_graph)
        if dependency_context_block:
            print(f"  [DependencyRisk] Detected blocking relationships:\n{dependency_context_block}")

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
        # High-confidence matches used to bypass the LLM, but now ALL items go to LLM for risk diagnosis.
        # Ambiguous activities → go to hybrid retrieval + LLM.
        deterministic_in_scope = []   # Keeping empty for prompt compatibility later
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
                resolved_name = matched_si["name"] if matched_si else canonical_title
                final_title, _ = _resolve_tracker_title(activity_name, resolved_name, scope_items, all_baseline_items)
                print(f"  [Deterministic] '{activity_name}' → mapped as '{final_title}' (confidence: {confidence}%)")
                ambiguous_activities.append({
                    "activity": activity_name,
                    "canonical_title": final_title,
                    "source_sentence": source_sentence,
                    "matched_si": matched_si,
                    "confidence": confidence
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
            
            # Retrieve milestone progress block to inject into the LLM prompt
            milestone_progress_block = ProjectKnowledgeService.calculate_milestone_progress(db_cursor, project_id)
            print(f"  [Info] Injected into prompt: {milestone_progress_block}")

            # Combine milestone progress + dependency context for richer LLM awareness
            combined_context = milestone_progress_block
            if dependency_context_block:
                combined_context += "\n\n" + dependency_context_block
            
            llm_risk_results = BatchActivityRiskAgent.evaluate_batch(activities_with_contexts, combined_context)


        # ===========================================================
        # STEP 6: Deterministic Risk & Execution Priority Analysis
        # ===========================================================
        _emit("Calculating Execution Priorities", 70)
        from services.dependency_analysis_service import DependencyAnalysisService
        from services.risk_scoring_engine import RiskScoringEngine
        from services.risk_ranking_engine import RiskRankingEngine
        
        # 1. Prepare Milestone mappings and graph
        db_cursor.execute("SELECT id, name, planned_date, status FROM project_milestones WHERE project_id = %s", (project_id,))
        milestone_details = {r['id']: r for r in db_cursor.fetchall()}
        
        db_cursor.execute("SELECT m.id as milestone_id, s.name as scope_name FROM scope_items s JOIN scope_milestone_mapping mmap ON s.id = mmap.scope_item_id JOIN project_milestones m ON mmap.milestone_id = m.id WHERE s.project_id = %s", (project_id,))
        scope_to_milestone_id = {r['scope_name'].lower(): r['milestone_id'] for r in db_cursor.fetchall()}
        milestone_id_to_name = {m['id']: m['name'] for m in milestone_details.values()}

        # Map canonical titles to milestone IDs
        # IMPORTANT: scope_to_milestone_id only contains items in the scope BASELINE.
        # Items like Azure AD SSO may exist as project_milestones but not scope_items.
        # We build a second name->id map from ALL project milestones as a fallback.
        milestone_name_to_id = {_normalize(m['name']): m_id for m_id, m in milestone_details.items()}
        
        def get_milestone_id(canonical_title):
            c_norm = _normalize(canonical_title)
            # 1. Exact scope mapping first (highest confidence)
            for db_scope_name, m_id in scope_to_milestone_id.items():
                db_norm = _normalize(db_scope_name)
                if db_norm == c_norm or db_norm in c_norm or c_norm in db_norm:
                    return m_id
            # 2. Fallback: match against ALL project milestone names
            for db_norm, m_id in milestone_name_to_id.items():
                if db_norm == c_norm or db_norm in c_norm or c_norm in db_norm:
                    return m_id
            return None

        # Build status map for dependency analysis
        milestone_status_map = {}
        for i, result in enumerate(llm_risk_results):
            activity_name = result.get("activity", "Unknown")
            matched_baseline_name = result.get("matched_baseline_item")
            pre_resolved = activities_with_contexts[i].get("canonical_title") if i < len(activities_with_contexts) else None
            
            if not matched_baseline_name and i < len(activities_with_contexts):
                si = activities_with_contexts[i].get("matched_si")
                if si: matched_baseline_name = si["name"]
            if not matched_baseline_name and pre_resolved:
                matched_baseline_name = pre_resolved

            canonical_title, _ = _resolve_tracker_title(
                activity_name, matched_baseline_name, scope_items, all_baseline_items
            )
            result["_canonical_title"] = canonical_title
            
            m_id = get_milestone_id(canonical_title)
            if m_id:
                milestone_status_map[m_id] = result.get("status", "UNKNOWN").upper()
                
        # 2. Dependency Analysis (Deterministic)
        _, backward_graph = MilestoneDependencyService.build_dependency_graph(db_cursor, project_id)
        dep_analysis_results = DependencyAnalysisService.analyze_dependencies(milestone_status_map, backward_graph)
        
        tracker_items = []
        out_of_scope_activities = []
        in_scope_activities = list(deterministic_in_scope)
        
        # 3. Execution Priority Analysis & Risk Scoring
        from datetime import datetime
        today = datetime.now().date()
        category_priorities = RiskConfigurationService.get_category_priorities(db_cursor)

        for i, result in enumerate(llm_risk_results):
            activity_name = result.get("activity", "Unknown")
            canonical_title = result["_canonical_title"]
            status = result.get("status", "UNKNOWN").upper()
            blocked_by = result.get("blocked_by", [])
            evidence = result.get("evidence_text", "")
            
            # Resolve scope matching
            matched_baseline_name = result.get("matched_baseline_item")
            _, is_confirmed_in_scope = _resolve_tracker_title(
                activity_name, matched_baseline_name, scope_items, all_baseline_items
            )
            
            entity_type = result.get("entity_type", "MILESTONE").upper()
            
            # ── Dependency Analysis prep ──────────────────────────────
            m_id = get_milestone_id(canonical_title)
            
            # Entity Type Override based on Baseline or Milestone Match
            # If it explicitly matches an IN_SCOPE item OR a known milestone ID, it's a MILESTONE
            # (Unless explicitly flagged as a CUSTOMER DEPENDENCY)
            if (is_confirmed_in_scope or m_id is not None) and entity_type != "DEPENDENCY":
                entity_type = "MILESTONE"
                
            dependency_source = None
            if entity_type == "DEPENDENCY" or blocked_by:
                dependency_source = DependencyClassificationService.classify(
                    activity_name, blocked_by, evidence, entity_type=entity_type
                )
                
            # Dependency analysis data (m_id already computed above)
            dep_data = dep_analysis_results.get(m_id, {})
            cascade_count = dep_data.get("cascade_count", 0)
            is_root_cause = dep_data.get("is_root_cause", False)

            # ── Determine if this item is a DIRECT blocker ──────────────────
            # A milestone is a "direct blocker" if it is the immediate child
            # of an incomplete root-cause (i.e. its own parent status is not COMPLETED).
            is_direct_blocker = False
            if m_id and m_id in backward_graph:
                direct_deps = backward_graph[m_id]
                for dep_id in direct_deps:
                    dep_status = milestone_status_map.get(dep_id, "COMPLETED")
                    if dep_status in ["BLOCKED", "DELAYED", "IN_PROGRESS", "NOT_STARTED"]:
                        status = "BLOCKED"
                        is_direct_blocker = True

                # Override LLM's generic blocked_by with exact DB direct blockers
                db_direct_blocked_by = [
                    milestone_id_to_name.get(d) for d in direct_deps
                    if d in milestone_id_to_name
                ]
                if db_direct_blocked_by:
                    blocked_by = db_direct_blocked_by

            # If the LLM extracted blockers, or the graph says it's blocked → BLOCKED
            if blocked_by and status not in ["BLOCKED", "DELAYED"]:
                status = "BLOCKED"

            # ── Assign Category (graph-first) ───────────────────────────────
            category_rules = RiskConfigurationService.get_category_rules(db_cursor)

            # Collect all transitive downstream names for critical-path check
            blocking = dep_data.get("downstream_milestones", [])
            downstream_names = [milestone_id_to_name.get(m, str(m)) for m in blocking]

            risk_cat = CategoryAssignmentEngine.assign_category(
                rules=category_rules,
                entity_type=entity_type,
                status=status,
                dependency_source=dependency_source,
                is_root_cause=is_root_cause,
                cascade_count=cascade_count,
                downstream_names=downstream_names,
                is_direct_blocker=is_direct_blocker,
                item_name=canonical_title,
            )

            # ── STAGE DIAGNOSTIC LOG ────────────────────────────────────────
            _WATCH = {"api", "vpn", "azure", "knowledge", "sit", "crm"}
            if any(w in canonical_title.lower() for w in _WATCH):
                print(f"[STAGE] '{canonical_title[:40]}'"
                      f" | entity={entity_type} | src={dependency_source}"
                      f" | root={is_root_cause} | cascade={cascade_count}"
                      f" | direct_blocker={is_direct_blocker}"
                      f" | status={status}"
                      f" | cat={risk_cat}")
            
            # Map back to old flags for the Scoring Engine
            is_scope_creep = (risk_cat == "SCOPE_CREEP")

            # Date calculation — use a SEPARATE variable to avoid shadowing dep m_id
            days_overdue = 0
            days_until_due = 9999
            date_m_id = None
            c_norm = _normalize(canonical_title)
            for db_scope_name, mid in scope_to_milestone_id.items():
                db_norm = _normalize(db_scope_name)
                if db_norm == c_norm or db_norm in c_norm or c_norm in db_norm:
                    date_m_id = mid
                    break
            
            p_date_str = None
            if date_m_id and date_m_id in milestone_details:
                p_date_str = milestone_details[date_m_id].get("planned_date")
                if p_date_str:
                    try:
                        p_date = datetime.strptime(str(p_date_str).split(' ')[0], "%Y-%m-%d").date()
                        if today > p_date:
                            days_overdue = (today - p_date).days
                            days_until_due = 0
                        else:
                            days_until_due = (p_date - today).days
                            days_overdue = 0
                    except Exception:
                        pass



            # Execution Priority Scoring
            score_result = RiskScoringEngine.calculate(
                status=status,
                blocked_by=blocked_by,
                is_root_cause=is_root_cause,
                cascade_count=cascade_count,
                days_overdue=days_overdue,
                days_until_due=days_until_due,
                is_scope_creep=is_scope_creep,
                confidence=1.0,
                business_impact="MEDIUM",
                params=risk_params,
                impact_matrix=impact_matrix,
                category=risk_cat,
            )
            
            exec_score = score_result["execution_priority_score"]
            breakdown = score_result["score_breakdown"]
            severity = RiskConfigurationService.classify_severity(exec_score, risk_thresholds)
            
            original_contract_sentence = ""
            for si in all_baseline_items:
                si_norm = _normalize(si["name"])
                if si_norm == c_norm or si_norm in c_norm or c_norm in si_norm:
                    original_contract_sentence = si.get("name", "")
                    break

            reasoning = result.get("reasoning", "")
            
            direct_blocking = dep_data.get("direct_downstream_milestones", [])
            direct_blocking_names = [milestone_id_to_name.get(m, str(m)) for m in direct_blocking]

            # Grouping
            if is_scope_creep:
                out_of_scope_activities.append({
                    "activity": canonical_title,
                    "entity_type": entity_type,
                    "classification": "OUT_OF_SCOPE" if severity in ["HIGH", "CRITICAL"] else "POSSIBLE_SCOPE_CREEP",
                    "reason": reasoning,
                    "similar_deliverable": canonical_title,
                    "confidence": 100,
                    "risk_score": exec_score,
                    "risk_level": severity,
                    
                    "category": risk_cat,
                    "current_status": status,
                    "progress": result.get("progress"),
                    "is_root_cause": is_root_cause,
                    "cascade_count": cascade_count,
                    "blockers": blocked_by,
                    "blocking_names": downstream_names,
                    "direct_blocking_names": direct_blocking_names,
                    "score_breakdown": breakdown,
                    "mom_evidence": evidence,
                    "original_contract_sentence": original_contract_sentence
                })
            elif status in ["BLOCKED", "DELAYED", "IN_PROGRESS", "NOT_STARTED"]:
                tracker_items.append({
                    "deliverable": canonical_title,
                    "entity_type": entity_type,
                    "expected_date": str(p_date_str) if p_date_str else "Unknown",
                    "current_status": status,
                    "progress": result.get("progress"),
                    "delay_days": days_overdue,
                    "blockers": blocked_by,
                    "confidence": 100,
                    "execution_priority_score": exec_score,
                    "dependency_status": risk_cat,
                    "category": risk_cat,
                    "is_root_cause": is_root_cause,
                    "cascade_count": cascade_count,
                    "days_overdue": days_overdue,
                    "days_until_due": days_until_due,
                    "risk": severity,
                    "risk_score": exec_score,
                    "reasoning": reasoning,
                    
                    "blocking_names": downstream_names,
                    "direct_blocking_names": direct_blocking_names,
                    "score_breakdown": breakdown,
                    "mom_evidence": evidence,
                    "original_contract_sentence": original_contract_sentence
                })
                
                # DEBUG INJECTION
                if "CRM" in canonical_title.upper() or "API" in canonical_title.upper():
                    print(f"=== DEBUG: {canonical_title} ===")
                    print(f"  entity_type: {entity_type}")
                    print(f"  dependency_source: {dependency_source}")
                    print(f"  is_root_cause: {is_root_cause}")
                    print(f"  cascade_count: {cascade_count}")
                    print(f"  assigned_category: {risk_cat}")
                    print(f"  score_breakdown: {breakdown}")
                    print(f"  final_score: {exec_score}")
                    print(f"==============================")
                    
            else:
                in_scope_activities.append({
                    "activity": canonical_title,
                    "classification": "IN_SCOPE",
                    "deliverable": canonical_title,
                    "confidence": 100,
                })

        # Deduplicate tracker_items by deliverable name
        def dedup_items(items_list, key_field):
            merged = {}
            for item in items_list:
                key = _normalize(item[key_field])
                if key not in merged:
                    merged[key] = item
                else:
                    # Merge logic: take max score, combine mom_evidence
                    existing = merged[key]
                    if item['risk_score'] > existing['risk_score']:
                        new_evidence = existing.get('mom_evidence', '')
                        item_evidence = item.get('mom_evidence', '')
                        if item_evidence and item_evidence not in new_evidence:
                            new_evidence += f"\n\n{item_evidence}"
                        item['mom_evidence'] = new_evidence
                        merged[key] = item
                    else:
                        existing_evidence = existing.get('mom_evidence', '')
                        item_evidence = item.get('mom_evidence', '')
                        if item_evidence and item_evidence not in existing_evidence:
                            existing_evidence += f"\n\n{item_evidence}"
                        existing['mom_evidence'] = existing_evidence
                        
            final_list = list(merged.values())
            for item in final_list:
                formatted = RiskScoringEngine.format_reasoning(
                    score=item.get("risk_score"),
                    severity=item.get("risk") or item.get("risk_level"),
                    category=item.get("category"),
                    entity_type=item.get("entity_type"),
                    status=item.get("current_status"),
                    progress=item.get("progress"),
                    is_root_cause=item.get("is_root_cause"),
                    cascade_count=item.get("cascade_count"),
                    blocked_by=item.get("blockers"),
                    blocking=item.get("blocking_names"),
                    direct_blocking=item.get("direct_blocking_names"),
                    breakdown=item.get("score_breakdown"),
                    mom_evidence=item.get("mom_evidence"),
                    original_contract_sentence=item.get("original_contract_sentence")
                )
                if 'reasoning' in item:
                    item['reasoning'] = formatted
                if 'reason' in item:
                    item['reason'] = formatted
                    
            return final_list

        out_of_scope_activities = dedup_items(out_of_scope_activities, "activity")
        tracker_items = dedup_items(tracker_items, "deliverable")

        # 4. Risk Ranking Engine
        timeline_deliverables = RiskRankingEngine.rank_risks(tracker_items, category_priorities)
        
        # Stamp priority_order on each item so the DB query can ORDER BY it
        for rank_idx, item in enumerate(timeline_deliverables):
            item["priority_order"] = rank_idx + 1
            item["action_priority_score"] = item.get("execution_priority_score", 0)

        # --- DEBUG: confirm category-first ordering ---
        print("[RankEngine] Ranked order:",
              [(r["deliverable"], r["category"], r["execution_priority_score"]) for r in timeline_deliverables])

        in_scope_result = {"activities": in_scope_activities}
        out_of_scope_result = {"activities": out_of_scope_activities}
        timeline_result = {"deliverables": timeline_deliverables}

        # STEP 7: Aggregation — LLM CALL #3
        _emit("Calculating Risk Score", 80)
        from core.prompts import get_risk_aggregation_prompt
        aggregation_prompt = get_risk_aggregation_prompt(
            in_scope_count=len(in_scope_activities),
            deterministic_count=len(deterministic_in_scope),
            out_of_scope_activities=out_of_scope_activities,
            timeline_deliverables=timeline_deliverables
        )
        final_assessment = LLMService.generate_json(aggregation_prompt)

        overall_risk = final_assessment.get("overallRisk", "LOW")
        risk_score = final_assessment.get("riskScore", 0)
        summary = final_assessment.get("summary", "")
        highest_action_priority = final_assessment.get("highestActionPriority")
        if highest_action_priority:
            # We can prepend the top priority to recommendations or keep it in the summary object
            pass
        recommendations = final_assessment.get("recommendations", [])

        sub_agent_results = {
            "in_scope": in_scope_result,
            "out_of_scope": out_of_scope_result,
            "timeline": timeline_result,
            "highestActionPriority": highest_action_priority
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
        
        risk_eval_id = db_cursor.lastrowid

        # Fetch stakeholders for alerts
        db_cursor.execute("SELECT email, role FROM stakeholders WHERE project_id = %s", (project_id,))
        stakeholders = db_cursor.fetchall()
        
        # Determine baseline version for progress tracking
        db_cursor.execute("SELECT version FROM scope_baselines WHERE project_id = %s AND status = 'APPROVED' ORDER BY id DESC LIMIT 1", (project_id,))
        bv_row = db_cursor.fetchone()
        baseline_version = bv_row["version"] if bv_row and "version" in bv_row else 1

        # Evaluate Deliverable Progress (Execution Status)
        _emit("Evaluating Deliverable Progress", 95)
        try:
            from agents.risk_evaluator_subagents import DeliverableTimelineEvaluationAgent
            from repositories.baseline_repository import BaselineRepository
            progress_records = DeliverableTimelineEvaluationAgent.evaluate_progress(
                approved_baseline_items=scope_items, 
                document_text=document_text, 
                risk_eval_output=sub_agent_results
            )
            for pr in progress_records:
                BaselineRepository.insert_deliverable_progress(
                    db=db_cursor._connection, 
                    project_id=project_id,
                    scope_item_id=pr.get("scope_item_id"),
                    source_document_id=document_id,
                    risk_evaluation_id=risk_eval_id,
                    baseline_version=baseline_version,
                    status_code=pr.get("progress_status", "UNKNOWN"),
                    progress_percentage=pr.get("progress_percentage"),
                    execution_summary=pr.get("execution_summary", ""),
                    dependencies=pr.get("dependencies", []),
                    confidence=pr.get("confidence", 1.0),
                    evidence_text=pr.get("evidence_text", "")
                )
            db_cursor._connection.commit()
        except Exception as e:
            import traceback
            traceback.print_exc()
            print(f"Error evaluating deliverable progress: {e}")

        # Persist Out-of-Scope risks to tracker
        for oos_item in out_of_scope_result.get("activities", []):
            oos_name = oos_item.get('activity', 'Unknown')  # Already canonical from pipeline

            card_title = oos_name

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

            card_title = deliv_name

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

            actual_risk_cat = deliv.get('dependency_status', 'DELAY')
            item_type = 'BLOCKER' if actual_risk_cat in ['BLOCKED', 'DEPENDENCY'] else 'ACTION_ITEM'

            TrackerAuditAgent.persist_tracker_item(
                db_cursor, project_id, document_id, item_type,
                False, item_risk_score, item_risk_level, actual_risk_cat,
                1.0, full_reasoning, True,
                title=card_title, reference_id=ref_id,
                priority_order=deliv.get('priority_order')
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
            "highestActionPriority": highest_action_priority,
            "recommendations": recommendations,
            "subAgentResults": sub_agent_results
        }
