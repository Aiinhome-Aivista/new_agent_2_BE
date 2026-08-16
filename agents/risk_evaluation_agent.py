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
    def _pm_decision(cls, priority: int, owner: str, is_root_cause: bool = False, longest_path: list = None) -> str:
        chain = " -> ".join([str(x) for x in longest_path]) if longest_path else ""
        
        if is_root_cause and priority >= 80:
            if owner == "Customer": 
                rec = "Escalate to customer immediately. Request ETA. Current project execution is blocked."
            elif owner == "Vendor": 
                rec = "Escalate to vendor immediately. Current project execution is blocked."
            else: 
                rec = "Assign internal engineering resource ASAP. Current project execution is blocked."
                
            rec += " Target completion dates may slip if unresolved."
                
            if chain:
                rec += f"\nExpected unlock: {chain}"
            return rec
            
        if priority >= 80:
            if owner == "Customer": return "Escalate to customer today. Target completion dates may slip if unresolved."
            elif owner == "Vendor": return "Review vendor SLA and follow up. Target completion dates may slip if unresolved."
            else: return "Assign internal engineering resource ASAP. Target completion dates may slip if unresolved."
        elif priority >= 60:
            return "Monitor and align resources for upcoming sprint"
        else:
            return "Track execution progress"

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
        
        # If the new pipeline already extracted facts, use them!
        if 'pre_extracted_activities' in activity_map:
            extraction_result = activity_map['pre_extracted_activities']
        else:
            # Get active tracker items to assist LLM in matching
            db_cursor.execute("SELECT id, title, risk_category, status FROM tracker_items WHERE project_id = %s AND status = 'OPEN'", (project_id,))
            active_tracker_items = db_cursor.fetchall()
            active_items_list = []
            for r in active_tracker_items:
                title = r['title'] if isinstance(r, dict) else r[1]
                cat = r['risk_category'] if isinstance(r, dict) else r[2]
                active_items_list.append(f"- {title} (Category: {cat})")
            active_tracker_block = "\n".join(active_items_list) if active_items_list else "None"
            
            extraction_result = ActivityExtractorAgent.extract_activities(document_text, active_tracker_block)
            
        raw_activities = extraction_result.get("activities", [])
        if not raw_activities and "extractions" in extraction_result:
            raw_activities = extraction_result.get("extractions", [])
            
        resolved_items = extraction_result.get("resolved_items", [])

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
            name = str(item.get("activity") or item.get("statement") or "").strip()
            classification = str(item.get("classification_type", "RISK")).strip().upper()
            
            if not name:
                continue

            # ── PHASE 1 CLASSIFICATION GATE (EARLY INTERCEPT) ──
            if classification == "PROGRESS_UPDATE":
                print(f"  [Gate] Dropping PROGRESS_UPDATE: {name}")
                continue
                
            # ───────────────────────────────────────────────────

            # Resolve canonical title against full baseline RIGHT NOW, before anything else
            canonical_title, is_in_scope = _resolve_tracker_title(
                name, None, scope_items, all_baseline_items
            )

            # Dedup key is the canonical title
            dedup_key = _normalize(canonical_title)
            if dedup_key in seen_canonical:
                print(f"  [Dedup] Merged '{name}' → already seen as '{canonical_title}'")
                continue
            seen_canonical.add(dedup_key)

            # ── Preserve execution_status separately from risk_status ──
            # LLM may extract WAITING_ON_CUSTOMER, NOT_STARTED, DELAYED, etc.
            # These must survive as execution_status and must NOT be flattened to UNKNOWN.
            raw_exec_status = str(item.get("status") or item.get("execution_status") or "").strip().upper()
            if not raw_exec_status or raw_exec_status in ("UNKNOWN", ""):
                raw_exec_status = "NOT_STARTED"

            cleaned_activities.append({
                "activity": name,
                "classification_type": classification,
                "canonical_title": canonical_title,
                "is_in_scope": is_in_scope,
                "source_sentence": item.get("source_sentence", name),
                "extraction_confidence": item.get("confidence", 100),
                # Raw dependency refs — passed to DependencyGraphBuilder for
                # canonical resolution via EntityResolver (NOT compared as strings)
                "blocked_by": item.get("blocked_by", []),
                "blocks": item.get("blocks", []),
                # Preserved execution status — kept independent of risk_status
                "execution_status": raw_exec_status,
                "status": raw_exec_status,
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
            extraction_confidence = act_item["extraction_confidence"]
            matched_si, confidence, match_type = _deterministic_match(activity_name, scope_items)

            # Also try matching on canonical_title if activity_name didn't hit
            if confidence < cls.DETERMINISTIC_CONFIDENCE_THRESHOLD:
                matched_si2, confidence2, match_type2 = _deterministic_match(canonical_title, scope_items)
                if confidence2 > confidence:
                    matched_si, confidence, match_type = matched_si2, confidence2, match_type2

            if confidence >= cls.DETERMINISTIC_CONFIDENCE_THRESHOLD or is_already_in_scope:
                resolved_name = matched_si["name"] if matched_si else canonical_title
                final_title, _ = _resolve_tracker_title(activity_name, resolved_name, scope_items, all_baseline_items)
                print(f"  [Deterministic] '{activity_name}' -> mapped as '{final_title}' (confidence: {confidence}%)")
                ambiguous_activities.append({
                    "activity": activity_name,
                    "classification_type": act_item["classification_type"],
                    "canonical_title": final_title,
                    "source_sentence": source_sentence,
                    "matched_si": matched_si,
                    "confidence": confidence,
                    "extraction_confidence": extraction_confidence,
                    "blocked_by": act_item.get("blocked_by", []),
                    "blocks": act_item.get("blocks", []),
                    "execution_status": act_item.get("execution_status", "NOT_STARTED")
                })
            else:
                # Ambiguous — needs deeper analysis
                ambiguous_activities.append({
                    "activity": activity_name,
                    "classification_type": act_item["classification_type"],
                    "canonical_title": canonical_title,
                    "source_sentence": source_sentence,
                    "matched_si": matched_si,
                    "confidence": confidence,
                    "extraction_confidence": extraction_confidence,
                    "blocked_by": act_item.get("blocked_by", []),
                    "blocks": act_item.get("blocks", []),
                    "execution_status": act_item.get("execution_status", "NOT_STARTED")
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
                "classification_type": item["classification_type"],
                "canonical_title": item.get("canonical_title"),
                "source_sentence": item.get("source_sentence", activity_name),
                "context": context,
                "matched_si": matched_si,
                "extraction_confidence": item.get("extraction_confidence", 100),
                "blocked_by": item.get("blocked_by", []),
                "blocks": item.get("blocks", []),
                "execution_status": item.get("execution_status", "NOT_STARTED")
            })

        # STEP 5: Batch LLM Risk Evaluation — LLM CALL #2
        _emit("Running In-Scope Evaluation Agent", 55)
        llm_risk_results = []
        if activities_with_contexts:
            print(f"  [LLM] Batch-evaluating {len(activities_with_contexts)} ambiguous activities...")
            import json
            try:
                print(f"[DEBUG] activities_with_contexts = {json.dumps(activities_with_contexts, indent=2)}")
            except:
                print(f"[DEBUG] activities_with_contexts = {activities_with_contexts}")
            
            # Retrieve milestone progress block to inject into the LLM prompt
            milestone_progress_block = ProjectKnowledgeService.calculate_milestone_progress(db_cursor, project_id)
            print(f"  [Info] Injected into prompt: {milestone_progress_block}")

            # Combine milestone progress + dependency context for richer LLM awareness
            combined_context = milestone_progress_block
            if dependency_context_block:
                combined_context += "\n\n" + dependency_context_block
            
            llm_risk_results = BatchActivityRiskAgent.evaluate_batch(activities_with_contexts, combined_context)
            
            print(f"=== [DEBUG] llm_risk_results ===")
            import json
            try:
                print(json.dumps(llm_risk_results, indent=2))
            except:
                print(llm_risk_results)
            print(f"==================================")


        # ===========================================================
        # STEP 6: Deterministic Risk & Execution Priority Analysis
        # ===========================================================
        _emit("Calculating Execution Priorities", 70)
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
                new_status = result.get("status", "UNKNOWN").upper()
                # Status precedence: COMPLETED > BLOCKED > IN_PROGRESS > PENDING
                status_rank = {"COMPLETED": 4, "BLOCKED": 3, "IN_PROGRESS": 2, "PENDING": 1, "UNKNOWN": 0}
                existing_status = milestone_status_map.get(m_id, "UNKNOWN")
                
                if status_rank.get(new_status, 0) > status_rank.get(existing_status, 0):
                    milestone_status_map[m_id] = new_status

        from agents.execution_pipeline import (
            MilestoneExecutionStateManager, 
            ProjectStateSnapshot, 
            DependencyExecutionStateResolver, 
            DerivedExecutionState
        )
        
        # 2. Milestone Execution State Manager
        # Updates DB using the TransitionValidator
        MilestoneExecutionStateManager.update_milestones(db_cursor, project_id, milestone_status_map)
        
        # 3. Execution Prerequisite Manager (Sub-deliverables & Prerequisites)
        # NOTE: We collect progress records here but insert them AFTER risk_eval_id
        # is obtained (further below), because risk_evaluation_id is NOT NULL in DB.
        _emit("Evaluating Execution Prerequisites", 55)
        pending_progress_records = []
        try:
            from agents.risk_evaluator_subagents import DeliverableTimelineEvaluationAgent
            from repositories.baseline_repository import BaselineRepository
            progress_records = DeliverableTimelineEvaluationAgent.evaluate_progress(
                approved_baseline_items=scope_items, 
                document_text=document_text, 
                risk_eval_output=[]  # Running without risk engine bias to capture PROGRESS_UPDATEs
            )
            pending_progress_records = progress_records
        except Exception as e:
            import traceback
            traceback.print_exc()
            print(f"Error evaluating deliverable progress: {e}")
            
        # 4. Project State Snapshot (Immutable)
        state_snapshot = ProjectStateSnapshot(db_cursor, project_id)
        
        # 4a. Commitment Monitoring Engine (Proactive Risk Synthesis)
        from services.commitment_monitoring_engine import CommitmentMonitoringEngine
        commitment_risks = CommitmentMonitoringEngine.evaluate(state_snapshot, llm_risk_results, all_baseline_items, project_id)
        if commitment_risks:
            print(f"  [CommitmentMonitor] Synthesized {len(commitment_risks)} missing update risks.")
            llm_risk_results.extend(commitment_risks)
            
            # Pad activities_with_contexts to align with llm_risk_results indices
            for cr in commitment_risks:
                activities_with_contexts.append({
                    "activity": cr["activity"],
                    "canonical_title": cr["_canonical_title"],
                    "classification_type": "RISK",
                    "extraction_confidence": 100,
                    "is_in_scope": True,
                    "matched_si": {"name": cr["_canonical_title"]}
                })
        
        # 4b. Dependency Execution State Resolver (Static Graph)
        forward_graph, backward_graph = MilestoneDependencyService.build_dependency_graph(db_cursor, project_id)
        dep_analysis_results = DependencyExecutionStateResolver.analyze_static_graph(state_snapshot, backward_graph)
        
        # 4a. Deterministic Graph Completion for Prerequisites
        # If an extracted activity is semantically matched to a milestone, but isn't that exact milestone,
        # we treat it as a prerequisite blocking that milestone.
        for i, result in enumerate(llm_risk_results):
            canonical_title = result.get("_canonical_title", "")
            activity_name = result.get("activity", "")
            m_id = get_milestone_id(canonical_title)
            
            if m_id and canonical_title:
                matched_name = state_snapshot.milestone_id_to_name.get(m_id)
                # Compare canonical_title vs matched_name to see if it's a prerequisite rather than the milestone itself
                if matched_name and canonical_title.strip().lower() != matched_name.strip().lower():
                    # It's a prerequisite blocking m_id!
                    v_node = f"VIRTUAL_{canonical_title}"
                    
                    if m_id not in forward_graph.get(v_node, []):
                        forward_graph.setdefault(v_node, []).append(m_id)
                    if v_node not in backward_graph.get(m_id, []):
                        backward_graph.setdefault(m_id, []).append(v_node)
                        
                    # Also record its status in snapshot
                    v_status = result.get("status", "UNKNOWN").upper().replace(" ", "_")
                    state_snapshot.milestone_statuses[v_node] = v_status
                    state_snapshot.milestone_id_to_name[v_node] = canonical_title
                    
        # Re-run static analysis to include virtual nodes
        dep_analysis_results = DependencyExecutionStateResolver.analyze_static_graph(state_snapshot, backward_graph)
        
        # 4b. Graph-First PMO Execution Queue Ordering
        from services.execution_queue_builder import ExecutionQueueBuilder
        execution_queue_order, node_metrics = ExecutionQueueBuilder.build_queue(state_snapshot, backward_graph, forward_graph)
        
        # Add ExecutionQueueBuilder metrics into dep_analysis_results so they reach the scoring engine
        for m_id, metrics in node_metrics.items():
            if m_id not in dep_analysis_results:
                dep_analysis_results[m_id] = {}
            dep_analysis_results[m_id].update({
                "immediate_unlocks": metrics.get("immediate_unlocks", 0),
                "cascade_count": metrics.get("cascade_nodes", 0),
                "critical_path": metrics.get("critical_path", False),
                "downstream_chain_length": metrics.get("critical_path_length", 0),
                "longest_path": [state_snapshot.milestone_id_to_name.get(p, p) for p in metrics.get("longest_path", [])],
                "execution_level": metrics.get("execution_level", 0),
                "execution_index": metrics.get("execution_index", 0),
                "days_remaining": metrics.get("days_remaining", 999),
                "earliest_root_cause": metrics.get("is_root", False)
            })
        
        # 5. Derived Execution State
        derived_states = DerivedExecutionState.compute_derived_status(state_snapshot, backward_graph)
        
        # ── RISK RECONCILIATION ENGINE ───────────────────────────────────────
        # Deterministically resolve existing OPEN risks if their originating condition has cleared.
        db_cursor.execute("SELECT * FROM tracker_items WHERE project_id = %s AND status = 'OPEN'", (project_id,))
        # Fix: map tuples to dictionaries since db_cursor is a standard tuple cursor
        columns = [col[0] for col in db_cursor.description]
        open_tracker_items = [dict(zip(columns, row)) for row in db_cursor.fetchall()]
        
        from services.risk_reconciliation_engine import RiskReconciliationEngine
        current_state = {
            "derived_states": derived_states,
            "resolved_items": extraction_result.get("resolved_items", [])
        }
        
        risks_to_resolve = RiskReconciliationEngine.reconcile_open_risks(open_tracker_items, current_state)
        
        for risk, reason, res_type in risks_to_resolve:
            TrackerAuditAgent.persist_tracker_item(
                db_cursor, project_id, document_id, risk.get("item_type", "ACTIVITY"),
                False, 0, 'LOW', 'RESOLVED',
                1.0, f"[Type: {res_type}]\nReason: {reason}", False,
                title=risk.get("title"), reference_id=risk.get("reference_id"),
                status='RESOLVED', resolve_only=True
            )
            print(f"  [Reconciliation] Auto-resolved stale risk: {risk.get('title')} ({res_type})")
        # ─────────────────────────────────────────────────────────────────────

        tracker_items = []
        out_of_scope_activities = []
        in_scope_activities = list(deterministic_in_scope)
        
        # 3. Execution Priority Analysis & Risk Scoring
        from datetime import datetime
        today = datetime.now().date()
        category_priorities = RiskConfigurationService.get_category_priorities(db_cursor)

        # ── PHASE A: PRE-PROCESSING & CANDIDATE GENERATION ──
        all_activities = []
        for i, result in enumerate(llm_risk_results):
            activity_name = result.get("activity", "Unknown")
            canonical_title = result["_canonical_title"]

            # Use execution_status preserved from LLM extraction (never UNKNOWN)
            # risk_status is managed separately by the risk lifecycle engine.
            execution_status = (
                result.get("execution_status") or
                result.get("status") or
                "NOT_STARTED"
            ).upper().strip()
            if not execution_status or execution_status == "UNKNOWN":
                execution_status = "NOT_STARTED"
            status = execution_status  # status used downstream for graph/scoring

            # Pull canonical-resolved blocked_by from DependencyGraphBuilder enrichment
            # (if graph builder ran on this batch; else fall back to raw LLM output)
            context_i = activities_with_contexts[i] if i < len(activities_with_contexts) else {}
            blocked_by = result.get("_resolved_blocked_by",
                         context_i.get("_resolved_blocked_by",
                         result.get("blocked_by", [])))
            evidence = result.get("evidence_text", "")
            
            # Resolve scope matching using deterministic logic, not just LLM output
            context = activities_with_contexts[i] if i < len(activities_with_contexts) else {}
            matched_si = context.get("matched_si")
            is_confirmed_in_scope = context.get("is_in_scope", False)
            
            entity_type = result.get("entity_type", "MILESTONE").upper()
            
            m_id = get_milestone_id(canonical_title)
            
            # Deterministic Source of Truth for Entity Type
            if matched_si and matched_si.get("category"):
                db_category = str(matched_si.get("category")).upper()
                if db_category == "FUNCTIONAL":
                    if matched_si.get("milestone"):
                        entity_type = "MILESTONE"
                    else:
                        entity_type = "ACTION_ITEM"
                else:
                    entity_type = db_category
            elif (is_confirmed_in_scope or m_id is not None) and entity_type in ["SCOPE_REQUEST", "RISK"]:
                entity_type = "MILESTONE"
                
            dependency_source = None
            is_direct_blocker = False

            if m_id and m_id in derived_states:
                d_state = derived_states[m_id]
                derived_status = d_state["status"]

                if derived_status == "BLOCKED" and status not in ["COMPLETED", "RESOLVED"]:
                    status = "BLOCKED"
                    blocked_by = d_state["blockers"]
                    is_direct_blocker = True
                elif derived_status == "WAITING":
                    pass  
            
            # Classification Gate
            classification_type = context.get("classification_type", "RISK")
            blocks = context.get("blocks", [])
            
            if classification_type == "DEPENDENCY" and not is_direct_blocker and len(blocks) == 0 and status not in ["COMPLETED", "RESOLVED"]:
                existing_ref_id = get_milestone_id(canonical_title)
                TrackerAuditAgent.persist_tracker_item(
                    db_cursor, project_id, document_id, "DEPENDENCY",
                    False, 0, 'LOW', 'GENERAL',
                    1.0, f"Captured as DEPENDENCY (Status: {status})", False,
                    title=canonical_title, reference_id=existing_ref_id, status='NOT_STARTED', risk_source='OBSERVED'
                )
                print(f"  [Gate] Bypassed Risk Engine for non-blocking DEPENDENCY: {canonical_title}")
                continue

            m_id_for_metrics = m_id
            v_node = f"VIRTUAL_{canonical_title}"
            if v_node in dep_analysis_results:
                m_id_for_metrics = v_node
                
            dep_data = dep_analysis_results.get(m_id_for_metrics, {})
            cascade_count = dep_data.get("cascade_count", 0)
            earliest_root_cause = dep_data.get("earliest_root_cause", False)
            critical_path = dep_data.get("critical_path", False)
            downstream_chain_length = dep_data.get("downstream_chain_length", 0)
            distance_to_next_executable = dep_data.get("distance_to_next_executable", 999)
            longest_path = dep_data.get("longest_path", [])
            
            if cascade_count == 0 and len(blocks) > 0:
                cascade_count = len(blocks)
                earliest_root_cause = True
                if "downstream_milestones" not in dep_data:
                    dep_data["downstream_milestones"] = blocks

            if status == "COMPLETED":
                earliest_root_cause = False
                blocked_by = []
                dependency_source = None
                cascade_count = 0
                is_direct_blocker = False
                
                existing_ref_id = get_milestone_id(canonical_title)
                TrackerAuditAgent.persist_tracker_item(
                    db_cursor, project_id, document_id, 'ACTIVITY',
                    False, 0, 'LOW', 'RESOLVED',
                    1.0, f"Milestone completed.\n\nEvidence: {evidence}", False,
                    title=canonical_title, reference_id=existing_ref_id, status='RESOLVED', resolve_only=True
                )
                print(f"  [COMPLETED] '{canonical_title}' -> early-exit RESOLVED. Skipping risk scoring.")
                continue

            # Removed CategoryAssignmentEngine logic. We will use ValidationService instead.
            risk_cat = "GENERAL"
            
            is_execution_blocker = False
            if entity_type == "ACTION_ITEM" and (cascade_count > 0 or earliest_root_cause):
                is_execution_blocker = True
                risk_cat = "EXECUTION_BLOCKER"
                
            is_scope_creep = False
            # Scope Creep Scenario 1: Completely missing from baseline (Rogue work)
            if not matched_si and not is_confirmed_in_scope:
                if entity_type in ["SCOPE_REQUEST", "MILESTONE"]:
                    is_scope_creep = True
                    risk_cat = "SCOPE_CREEP"
            # Scope Creep Scenario 2: Matches an item explicitly listed as OUT_OF_SCOPE in the contract
            elif matched_si and not is_confirmed_in_scope:
                if matched_si.get("type", "").upper() == "OUT_OF_SCOPE" or matched_si.get("category", "").upper() == "OUT_OF_SCOPE":
                    is_scope_creep = True
                    risk_cat = "SCOPE_CREEP"

            downstream_names = []

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

            direct_downstream = dep_data.get("direct_downstream_milestones", [])
            all_downstream = dep_data.get("downstream_milestones", [])
            immediate_unlocks = [milestone_id_to_name.get(m, str(m)) for m in direct_downstream]
            future_unlocks = [milestone_id_to_name.get(m, str(m)) for m in all_downstream if m not in direct_downstream]
            
            next_milestone_name = None
            next_milestone_date_str = None
            days_to_next_milestone = None
            earliest_date = None
            for dm in direct_downstream:
                dm_date_str = milestone_details.get(dm, {}).get("planned_date")
                if dm_date_str:
                    try:
                        dm_date = datetime.strptime(str(dm_date_str).split(' ')[0], "%Y-%m-%d").date()
                        if earliest_date is None or dm_date < earliest_date:
                            earliest_date = dm_date
                            next_milestone_name = milestone_id_to_name.get(dm, str(dm))
                            next_milestone_date_str = str(dm_date_str).split(' ')[0]
                    except Exception:
                        pass
                        
            if earliest_date:
                days_to_next_milestone = (earliest_date - today).days
                
            # Override next milestone with accurate data from the graph if available
            graph_next_date = dep_data.get("next_downstream_date")
            if graph_next_date:
                next_milestone_name = dep_data.get("next_downstream_name")
                next_milestone_date_str = str(graph_next_date)
                days_to_next_milestone = (graph_next_date - today).days

            original_contract_sentence = ""
            for si in all_baseline_items:
                si_norm = _normalize(si["name"])
                if si_norm == c_norm or si_norm in c_norm or c_norm in si_norm:
                    original_contract_sentence = si.get("name", "")
                    break

            reasoning = result.get("reasoning", "")
            direct_blocking = dep_data.get("direct_downstream_milestones", [])
            direct_blocking_names = [milestone_id_to_name.get(m, str(m)) for m in direct_blocking]

            llm_confidence = context.get("extraction_confidence", 100)
            try:
                llm_confidence = float(llm_confidence)
                if llm_confidence <= 1.0:
                    llm_confidence *= 100
            except:
                llm_confidence = 100

            should_create_risk = False
            # Statuses that block risk creation:
            #   COMPLETED, RESOLVED — item is done
            #   WAITING is NOT excluded; WAITING_ON_CUSTOMER, DELAYED, BLOCKED,
            #   NOT_STARTED are all valid risk conditions.
            non_risk_statuses = {"COMPLETED", "RESOLVED"}
            if status.upper() not in non_risk_statuses:
                # Threshold: 60 (not 80). LLM typically returns 0.7–0.95 confidence.
                # An extraction_confidence of 1.0 (100%) is the default when no
                # confidence was returned — should always create a risk.
                if llm_confidence >= 60:
                    should_create_risk = True
                else:
                    print(f"  [Gate] '{canonical_title}' skipped — low extraction confidence: {llm_confidence}%")
            else:
                print(f"  [Gate] '{canonical_title}' skipped — status is '{status}' (completed/resolved)")

            all_activities.append({
                "activity": canonical_title,
                "canonical_title": canonical_title,
                "entity_type": entity_type,
                "status": status,
                "blocked_by": blocked_by,
                "blocks": blocks,
                "is_root_cause": earliest_root_cause,
                "cascade_count": cascade_count,
                "days_overdue": days_overdue,
                "days_until_due": days_until_due,
                "is_scope_creep": is_scope_creep,
                "risk_cat": risk_cat,
                "immediate_unlocks": immediate_unlocks,
                "future_unlocks": future_unlocks,
                "next_milestone_name": next_milestone_name,
                "next_milestone_date": next_milestone_date_str,
                "days_to_next_milestone": days_to_next_milestone,
                "original_contract_sentence": original_contract_sentence,
                "reasoning": result.get("reasoning", ""),
                "narratives": result.get("narratives", {}),
                "direct_blocking_names": direct_blocking_names,
                "downstream_names": downstream_names,
                "evidence": evidence,
                "progress": result.get("progress"),
                "recommended_action": result.get("recommended_action"),
                "p_date_str": p_date_str,
                "llm_confidence": llm_confidence,
                "should_create_risk": should_create_risk,
                "critical_path": critical_path,
                "downstream_chain_length": downstream_chain_length,
                "distance_to_next_executable": distance_to_next_executable,
                "is_execution_blocker": is_execution_blocker,
                "earliest_executable_work": immediate_unlocks,
                "longest_path": longest_path,
                "m_id": m_id_for_metrics
            })

        # ── PHASE B: DEDUPLICATION & VALIDATION GATE ──
        # Deduplicate activities by canonical_title before building the dependency graph
        deduplicated_activities = {}
        for activity in all_activities:
            title = activity["canonical_title"]
            if title not in deduplicated_activities:
                deduplicated_activities[title] = activity
            else:
                # Merge logic if duplicate found (e.g. prioritize active status)
                existing = deduplicated_activities[title]
                if existing["status"] in ["COMPLETED", "RESOLVED"] and activity["status"] not in ["COMPLETED", "RESOLVED"]:
                    deduplicated_activities[title] = activity
        
        unique_activities = list(deduplicated_activities.values())
        
        from services.validation_service import ValidationService
        print(f"  [ValidationGate] Enriching {len(unique_activities)} unique activities...")
        enriched_activities = ValidationService.enrich_candidates(unique_activities, scope_items=all_baseline_items)

                                
        # ── PHASE C: RISK SCORING & AGGREGATION ──
        for idx, item in enumerate(enriched_activities):
            if item["status"] in ["COMPLETED", "RESOLVED"]:
                continue
            if not item["should_create_risk"]:
                if not item["is_scope_creep"]:
                    in_scope_activities.append({
                        "activity": item["canonical_title"],
                        "classification": "IN_SCOPE",
                        "deliverable": item["canonical_title"],
                        "confidence": 100,
                    })
                continue


            # Determine dynamic business impact based on graph blocked_work_count
            cascade = item.get("blocked_work_count", 0)
            if cascade >= 4:
                b_impact = "CRITICAL"
            elif cascade >= 2:
                b_impact = "HIGH"
            elif cascade == 1:
                b_impact = "MEDIUM"
            else:
                b_impact = "LOW"

            score_result = RiskScoringEngine.calculate(
                status=item["status"],
                blocked_by=item["blocked_by"],
                earliest_root_cause=item.get("is_root_cause", False),
                cascade_depth=item.get("cascade_depth", 0),
                blocked_work_count=item.get("blocked_work_count", 0),
                execution_unlock_count=item.get("execution_unlock_count", 0),
                critical_chain=item.get("critical_chain", False),
                dependency_source=item.get("dependency_source", "ENGINEERING"),
                days_overdue=item.get("days_overdue", 0),
                days_until_due=item.get("days_until_due", 999),
                is_scope_creep=item.get("is_scope_creep", False),
                confidence=item.get("llm_confidence", 1.0),
                business_impact=item.get("business_impact", "MEDIUM"),
                params=risk_params,
                impact_matrix=impact_matrix,
                item_name=item["canonical_title"],
                category=item["risk_cat"],
                immediate_unlocks=item.get("immediate_unlocks", []),
                future_unlocks=item.get("future_unlocks", []),
                next_milestone_name=item.get("next_milestone_name", None),
                next_milestone_date=item.get("next_milestone_date", None),
                days_to_next_milestone=item.get("days_to_next_milestone", None),
                critical_path=item.get("critical_path", False),
                distance_to_next_executable=item.get("distance_to_next_executable", 999),
                dependency_owner=item.get("dependency_owner", "Internal"),
                resolution_effort=item.get("resolution_effort", "M"),
                business_criticality=item.get("business_criticality", "Medium"),
                business_phase=item.get("business_phase", "Execution"),
                criticality_score=item.get("criticality_score", 0.0),
                parallel_stream=item.get("parallel_stream", "Stream 1")
            )
            
            exec_prio = score_result["execution_priority"]
            risk_sev = score_result["risk_severity"]
            
            breakdown = score_result["score_breakdown"]
            execution_priority = exec_prio
            cascade_priority = score_result.get("cascade_priority", 0)
            schedule_priority = score_result.get("schedule_priority", 0)
            execution_reasons = score_result.get("execution_reasons", [])
            severity = RiskConfigurationService.classify_severity(risk_sev, risk_thresholds)
            
            full_reasoning = RiskScoringEngine.format_reasoning(
                score=exec_prio,
                severity=severity,
                category=item["risk_cat"],
                entity_type=item["entity_type"],
                status=item["status"],
                progress=item["progress"],
                earliest_root_cause=item["is_root_cause"],
                cascade_count=item["cascade_count"],
                blocked_by=item["blocked_by"],
                blocking=item["downstream_names"],
                direct_blocking=item.get("direct_blocking_names", []),
                breakdown=breakdown,
                mom_evidence=item["evidence"],
                original_contract_sentence=item.get("original_contract_sentence", ""),
                immediate_unlocks=item.get("immediate_unlocks", []),
                future_unlocks=item.get("future_unlocks", []),
                longest_path=item.get("longest_path", []),
                next_milestone_name=item.get("next_milestone_name", None),
                next_milestone_date=item.get("next_milestone_date", None),
                days_to_next_milestone=item.get("days_to_next_milestone", None),
                execution_priority=execution_priority,
                cascade_priority=cascade_priority,
                schedule_priority=schedule_priority,
                execution_reasons=execution_reasons
            )
            
            queue_order = 9999
            if m_id_for_metrics in execution_queue_order:
                queue_order = execution_queue_order.index(m_id_for_metrics)
            
            if item["is_scope_creep"]:
                out_of_scope_activities.append({
                    "activity": item["canonical_title"],
                    "entity_type": item["entity_type"],
                    "classification": "OUT_OF_SCOPE" if severity in ["HIGH", "CRITICAL"] else "POSSIBLE_SCOPE_CREEP",
                    "reason": full_reasoning,
                    "similar_deliverable": item["canonical_title"],
                    "confidence": 100,
                    "risk_score": risk_sev,
                    "risk_level": severity,
                    "category": item["risk_cat"],
                    "current_status": item["status"],
                    "progress": item["progress"],
                    "is_root_cause": item["is_root_cause"],
                    "cascade_count": item["cascade_count"],
                    "blockers": item["blocked_by"],
                    "blocking_names": item["downstream_names"],
                    "direct_blocking_names": item.get("direct_blocking_names", []),
                    "immediate_unlocks": item.get("immediate_unlocks", []),
                    "future_unlocks": item.get("future_unlocks", []),
                    "longest_path": item.get("longest_path", []),
                    "next_milestone_name": item["next_milestone_name"],
                    "next_milestone_date": item["next_milestone_date"],
                    "days_to_next_milestone": item["days_to_next_milestone"],
                    "score_breakdown": breakdown,
                    "execution_priority": execution_priority,
                    "cascade_priority": cascade_priority,
                    "schedule_priority": schedule_priority,
                    "execution_reasons": execution_reasons,
                    "mom_evidence": item["evidence"],
                    "original_contract_sentence": item.get("original_contract_sentence", ""),
                    "narratives": item.get("narratives", {})
                })
            else:
                # Correct entity_type if LLM misclassified an in-scope item as SCOPE_REQUEST
                if item.get("matched_baseline_item") and item.get("entity_type") == "SCOPE_REQUEST":
                    item["entity_type"] = "MILESTONE"

                # ── Decoupled status fields ──
                # execution_status: preserves the operational fact from the document
                # Override with graph readiness if blocked by unresolved/external deps
                raw_exec_status = item.get("execution_status", item.get("status", "NOT_STARTED"))
                readiness = item.get("readiness_status", "")
                
                if readiness in ("BLOCKED_UNRESOLVED_DEPENDENCY", "WAITING_ON_EXTERNAL"):
                    final_exec_status = readiness
                else:
                    final_exec_status = raw_exec_status

                tracker_items.append({
                    "deliverable": item["canonical_title"],
                    "entity_type": item["entity_type"],
                    "expected_date": str(item["p_date_str"]) if item["p_date_str"] else "Unknown",
                    "execution_status": final_exec_status,
                    "current_status": final_exec_status,
                    "risk_status": "OPEN",
                    "progress": item["progress"],
                    "delay_days": item["days_overdue"],
                    "blockers": item["blocked_by"],
                    "blocked_by_ids": item.get("_blocked_by_ids", []),
                    "blocks_ids": item.get("_blocks_ids", []),
                    "confidence": item["llm_confidence"],
                    # ── Decoupled score fields ──
                    # execution_priority_score: graph-derived (root cause, unlock count, cascade)
                    # risk_score / risk_severity_score: risk severity (separate)
                    "execution_priority_score": exec_prio,
                    "risk_severity_score": risk_sev,
                    "dependency_status": item["risk_cat"],
                    "category": item["risk_cat"],
                    "graph_role": item.get("graph_role", "DOWNSTREAM_ACTIVITY"),
                    "canonical_id": item.get("_canonical_id", ""),
                    "risk_source": "DERIVED" if item["risk_cat"] in ["EXECUTION_BLOCKER", "ROOT_CAUSE", "TECHNICAL_DEPENDENCY"] else "OBSERVED",
                    "is_root_cause": item["is_root_cause"],
                    "m_id": item.get("m_id"),
                    "cascade_count": item["cascade_count"],
                    "days_overdue": item["days_overdue"],
                    "days_until_due": item["days_until_due"],
                    "risk": severity,
                    "risk_score": risk_sev,
                    "reasoning": full_reasoning,
                    "blocking_names": item["downstream_names"],
                    "direct_blocking_names": item.get("direct_blocking_names", []),
                    "immediate_unlocks": item["immediate_unlocks"],
                    "future_unlocks": item["future_unlocks"],
                    "next_milestone_name": item["next_milestone_name"],
                    "days_to_next_milestone": item["days_to_next_milestone"],
                    "score_breakdown": breakdown,
                    "execution_priority": execution_priority,
                    "cascade_priority": cascade_priority,
                    "schedule_priority": schedule_priority,
                    "execution_reasons": execution_reasons,
                    "mom_evidence": item["evidence"],
                    "original_contract_sentence": item.get("original_contract_sentence", ""),
                    "narratives": item.get("narratives", {}),
                    "recommended_action": item.get("recommended_action") or cls._pm_decision(
                        execution_priority,
                        item.get("dependency_owner", "Internal"),
                        item["is_root_cause"],
                        item.get("longest_path", [])
                    ),
                    "business_phase": item.get("business_phase"),
                    "queue_order": queue_order,
                    "longest_path": item.get("longest_path", []),
                    "dependency_owner": item.get("dependency_owner"),
                    "parallel_stream": item.get("parallel_stream"),
                    "unresolved_external_dependencies":
                        item.get("unresolved_external_dependencies", []),
                })

        # PM Decision Engine Helper
        # ... Wait, I can't easily inject a class method here if I just append. I will inject a local helper above the loop, or use a lambda.
        # Actually, let's just do it inline here for safety since I am replacing inside the loop.

        # Deduplicate tracker_items by deliverable name
        def dedup_items(items_list, key_field):
            merged = {}
            for item in items_list:
                key = _normalize(item[key_field])
                if key not in merged:
                    merged[key] = item
                else:
                    existing = merged[key]
                    if item['risk_score'] > existing['risk_score']:
                        new_evidence = existing.get('mom_evidence', '')
                        item_evidence = item.get('mom_evidence', '')
                        if item_evidence and item_evidence not in new_evidence:
                            new_evidence += f"\n\n{item_evidence}"
                        item['mom_evidence'] = new_evidence
                        item['execution_priority'] = max(item.get('execution_priority', 0), existing.get('execution_priority', 0))
                        item['cascade_priority'] = max(item.get('cascade_priority', 0), existing.get('cascade_priority', 0))
                        item['schedule_priority'] = max(item.get('schedule_priority', 0), existing.get('schedule_priority', 0))
                        
                        merged_reasons = list(item.get('execution_reasons', []))
                        for r in existing.get('execution_reasons', []):
                            if r not in merged_reasons:
                                merged_reasons.append(r)
                        item['execution_reasons'] = merged_reasons
                        
                        merged[key] = item
                    else:
                        existing_evidence = existing.get('mom_evidence', '')
                        item_evidence = item.get('mom_evidence', '')
                        if item_evidence and item_evidence not in existing_evidence:
                            existing_evidence += f"\n\n{item_evidence}"
                        existing['mom_evidence'] = existing_evidence
                        existing['execution_priority'] = max(existing.get('execution_priority', 0), item.get('execution_priority', 0))
                        existing['cascade_priority'] = max(existing.get('cascade_priority', 0), item.get('cascade_priority', 0))
                        existing['schedule_priority'] = max(existing.get('schedule_priority', 0), item.get('schedule_priority', 0))
                        
                        merged_reasons = list(existing.get('execution_reasons', []))
                        for r in item.get('execution_reasons', []):
                            if r not in merged_reasons:
                                merged_reasons.append(r)
                        existing['execution_reasons'] = merged_reasons
                        
            final_list = list(merged.values())
            return final_list

        out_of_scope_activities = dedup_items(out_of_scope_activities, "activity")
        tracker_items = dedup_items(tracker_items, "deliverable")
        # 4. Risk Ranking Engine
        timeline_deliverables = RiskRankingEngine.rank_risks(tracker_items, category_priorities)
        
        # Stamp priority_order on each item so the DB query can ORDER BY it,
        # and generate PM recommendations / formatted reasoning using finalized priorities.
        for rank_idx, item in enumerate(timeline_deliverables):
            item["priority_order"] = rank_idx + 1
            item["action_priority_score"] = item.get("execution_priority_score", 0)
            
            ep = item.get("execution_priority_score", 0)
            item['recommended_action'] = cls._pm_decision(ep, item.get('dependency_owner', 'Internal'), item.get('is_root_cause', False), item.get('longest_path', []))
            
            e_type = item.get("entity_type", "")
            cp = item.get("cascade_priority", 0)
            sp = item.get("schedule_priority", 0)
            
            if e_type in ["SCOPE_REQUEST", "CHANGE_REQUEST"]:
                item["category"] = "SCOPE_CHANGE"
            elif ep >= 90:
                item["category"] = "EXECUTION_BLOCKER"
            elif cp > 0:
                item["category"] = "CRITICAL_PATH_RISK"
            elif sp >= 50:
                item["category"] = "SCHEDULE_RISK"
            elif e_type == "ACTION_ITEM":
                item["category"] = "ACTION_ITEM"
                
            formatted = RiskScoringEngine.format_reasoning(
                score=item.get("risk_score"),
                severity=item.get("risk") or item.get("risk_level"),
                category=item.get("category"),
                entity_type=item.get("entity_type"),
                status=item.get("current_status"),
                progress=item.get("progress"),
                earliest_root_cause=item.get("is_root_cause"),
                cascade_count=item.get("cascade_count"),
                blocked_by=item.get("blockers"),
                blocking=item.get("blocking_names"),
                direct_blocking=item.get("direct_blocking_names"),
                breakdown=item.get("score_breakdown"),
                mom_evidence=item.get("mom_evidence"),
                original_contract_sentence=item.get("original_contract_sentence"),
                narratives=item.get("narratives", {}),
                immediate_unlocks=item.get("immediate_unlocks"),
                future_unlocks=item.get("future_unlocks"),
                longest_path=item.get("longest_path"),
                next_milestone_name=item.get("next_milestone_name"),
                next_milestone_date=item.get("next_milestone_date"),
                days_to_next_milestone=item.get("days_to_next_milestone"),
                execution_priority=ep,
                cascade_priority=cp,
                schedule_priority=sp,
                execution_reasons=item.get("execution_reasons", [])
            )
            if 'reasoning' in item:
                item['reasoning'] = formatted
            if 'reason' in item:
                item['reason'] = formatted

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
        project_executive_summary = final_assessment.get("project_executive_summary")
        if highest_action_priority:
            # We can prepend the top priority to recommendations or keep it in the summary object
            pass
        recommendations = final_assessment.get("recommendations", [])

        sub_agent_results = {
            "in_scope": in_scope_result,
            "out_of_scope": out_of_scope_result,
            "timeline": timeline_result,
            "highestActionPriority": highest_action_priority,
            "project_executive_summary": project_executive_summary
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

        # Deliverable Progress: now insert with the valid risk_eval_id
        from repositories.baseline_repository import BaselineRepository
        for pr in pending_progress_records:
            try:
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
                    resolved_items=resolved_items,
                    confidence=pr.get("confidence", 1.0),
                    evidence_text=pr.get("evidence_text", "")
                )
            except Exception as e:
                print(f"Warning: Could not persist deliverable progress record: {e}")


        # Fetch the visibility threshold once
        visibility_threshold = RiskConfigurationService.get_visibility_threshold()

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
            
            is_resolved = (item_risk_score == 0) or (oos_item.get('current_status') in ['COMPLETED', 'RESOLVED'])
            
            if is_resolved:
                target_status = 'RESOLVED'
            elif item_risk_score < visibility_threshold:
                target_status = 'RESOLVED'
            else:
                target_status = 'OPEN'

            TrackerAuditAgent.persist_tracker_item(
                db_cursor, project_id, document_id, 'ACTIVITY',
                True, item_risk_score, item_risk_level, 'SCOPE_CREEP',
                confidence_val, full_reasoning, True,
                title=card_title, reference_id=ref_id,
                status=target_status
            )

            # Use alert threshold from DB config (not hardcoded 70)
            oos_alert_rule = alert_rules.get(item_risk_level, {})
            if oos_alert_rule.get('send_email') and item_risk_score >= oos_alert_rule.get('min_score_threshold', 70):
                AlertingAgent.dispatch_alert(
                    project_id, f"Scope Creep Risk: {card_title}",
                    full_reasoning, stakeholders, db_cursor=db_cursor
                )

        # Persist Timeline / Delay risks to tracker
        for deliv in tracker_items:
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
            # Preserve the LLM's original classification for logic, but map to valid DB ENUM
            raw_entity_type = deliv.get('entity_type', 'ACTIVITY').upper()
            if raw_entity_type == 'MILESTONE':
                item_type = 'ACTIVITY'
            elif raw_entity_type in ['SCOPE_REQUEST', 'SCOPE_CREEP', 'CHANGE_REQUEST']:
                item_type = 'NEW_REQUEST'
            elif raw_entity_type == 'DEPENDENCY':
                # Map to BLOCKER only if it actually blocks something or is explicitly flagged
                if actual_risk_cat in ['WAITING_DEPENDENCY', 'EXECUTION_BLOCKER', 'ROOT_CAUSE_BLOCKER']:
                    item_type = 'BLOCKER'
                else:
                    item_type = 'ACTIVITY'
            elif raw_entity_type in ['ACTION_ITEM', 'DECISION', 'RISK_MENTIONED']:
                item_type = raw_entity_type
            else:
                item_type = 'ACTIVITY'
            
            # Auto-resolve logic: if score drops to 0, dependency status is RESOLVED, or status is COMPLETED
            is_resolved = (item_risk_score == 0) or (actual_risk_cat == 'RESOLVED') or (deliv.get('current_status') == 'COMPLETED')
            
            if is_resolved:
                target_status = 'RESOLVED'
            else:
                target_status = 'OPEN'

            TrackerAuditAgent.persist_tracker_item(
                db_cursor, project_id, document_id, item_type,
                False, item_risk_score, item_risk_level, actual_risk_cat,
                1.0, full_reasoning, True,
                title=card_title, reference_id=ref_id,
                priority_order=deliv.get('priority_order'),
                status=target_status,
                risk_source=deliv.get('risk_source', 'OBSERVED'),
                recommended_action=deliv.get('recommended_action'),
                execution_priority_score=deliv.get('execution_priority',
                    deliv.get('execution_priority_score',
                    deliv.get('action_priority_score', 0))),
                # New decoupled fields
                execution_status=deliv.get('execution_status', deliv.get('current_status')),
                risk_status=deliv.get('risk_status', 'OPEN'),
                graph_role=deliv.get('graph_role', 'DOWNSTREAM_ACTIVITY'),
                canonical_id=deliv.get('canonical_id', ''),
                risk_severity_score=deliv.get('risk_severity_score',
                    deliv.get('risk_score', item_risk_score)),
            )

            # Use alert threshold from DB config (not hardcoded 70)
            if not is_resolved:
                delay_alert_rule = alert_rules.get(item_risk_level, {})
                if delay_alert_rule.get('send_email') and item_risk_score >= delay_alert_rule.get('min_score_threshold', 70):
                    AlertingAgent.dispatch_alert(
                        project_id, f"Delay Risk: {card_title}",
                        full_reasoning, stakeholders, db_cursor=db_cursor
                    )
                    
        # Task 3: Risk Resolution by Type
        # A) Explicit Evidence (resolved_items) for Customer Dependencies / Technical Risks
        for resolved in resolved_items:
            res_name = resolved.get("name", "")
            res_evidence = resolved.get("resolution_evidence", "No evidence provided")
            res_confidence = resolved.get("confidence", 0)
            
            # Only resolve if confidence > 85 (High Confidence)
            if res_confidence > 85:
                ref_id = None
                res_name_clean = res_name.lower().strip()
                for name, act_id in activity_map.items():
                    if name in res_name_clean or res_name_clean in name:
                        ref_id = act_id
                        break
                        
                TrackerAuditAgent.persist_tracker_item(
                    db_cursor, project_id, document_id, 'ACTIVITY',
                    False, 0, 'LOW', 'RESOLVED',
                    1.0, f"Explicitly resolved in document.\n\nEvidence: {res_evidence}", False,
                    title=res_name, reference_id=ref_id,
                    status='RESOLVED', resolve_only=True
                )
                
        # B) Execution Blockers: Auto-resolve if associated milestone completes
        completed_milestone_ids = [m_id for m_id, status in milestone_status_map.items() if status == "COMPLETED"]
        if completed_milestone_ids:
            format_strings = ','.join(['%s'] * len(completed_milestone_ids))
            db_cursor.execute(f"""
                SELECT id, title, risk_category FROM tracker_items 
                WHERE project_id = %s 
                AND status = 'OPEN' 
                AND reference_id IN ({format_strings})
                AND risk_category IN ('ROOT_CAUSE', 'EXECUTION_BLOCKER', 'TECHNICAL_DEPENDENCY')
            """, (project_id, *completed_milestone_ids))
            
            completed_risks = db_cursor.fetchall()
            for r in completed_risks:
                r_id = r['id'] if isinstance(r, dict) else r[0]
                r_title = r['title'] if isinstance(r, dict) else r[1]
                TrackerAuditAgent.persist_tracker_item(
                    db_cursor, project_id, document_id, 'ACTIVITY',
                    False, 0, 'LOW', 'RESOLVED',
                    1.0, f"Execution milestone completed.", False,
                    title=r_title, reference_id=None, # Already matching by title
                    status='RESOLVED', resolve_only=True
                )
                
        # General Risks remain open unless mentioned (handled by not updating them)

        import json
        db_cursor.execute("""
            INSERT INTO risk_evaluations 
            (project_id, document_id, overall_risk_score, overall_risk_level, summary, recommendations, sub_agent_results)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
        """, (
            project_id, document_id, risk_score, overall_risk, summary,
            json.dumps(recommendations), json.dumps(sub_agent_results)
        ))
        
        risk_eval_id = db_cursor.lastrowid

        # Determine baseline version for progress tracking
        db_cursor.execute("SELECT version FROM scope_baselines WHERE project_id = %s AND status = 'APPROVED' ORDER BY id DESC LIMIT 1", (project_id,))
        bv_row = db_cursor.fetchone()
        baseline_version = bv_row["version"] if bv_row and "version" in bv_row else 1

        # Deliverable Progress: now insert with the valid risk_eval_id
        from repositories.baseline_repository import BaselineRepository
        for pr in pending_progress_records:
            try:
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
                    resolved_items=extraction_result.get("resolved_items", []),
                    confidence=pr.get("confidence", 1.0),
                    evidence_text=pr.get("evidence_text", "")
                )
            except Exception as e:
                print(f"Warning: Could not persist deliverable progress record: {e}")

        _emit("Completed", 100)
        return {
            "overallRisk": overall_risk,
            "riskScore": risk_score,
            "summary": summary,
            "highestActionPriority": highest_action_priority,
            "recommendations": recommendations,
            "subAgentResults": sub_agent_results
        }
