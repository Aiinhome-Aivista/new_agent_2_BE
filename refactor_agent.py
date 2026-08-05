import os
import re

FILE_PATH = r"c:\Users\ADMIN\Desktop\Agent-2\new_agent_2_BE\agents\risk_evaluation_agent.py"

with open(FILE_PATH, "r", encoding="utf-8") as f:
    code = f.read()

# We want to keep everything before `class RiskEvaluationAgent:` intact.
prefix_match = re.search(r'(class RiskEvaluationAgent:.*?DETERMINISTIC_CONFIDENCE_THRESHOLD = 85\n)', code, re.DOTALL)
prefix = code[:prefix_match.end()]

new_class_body = """
    @classmethod
    def evaluate_document(cls, project_id: int, document_id: int, document_text: str, db_cursor,
                          activity_map: dict = None, request_map: dict = None,
                          emit=None) -> dict:
        activity_map = activity_map or {}
        
        def _emit(step: str, pct: int):
            if emit: emit(step, pct)

        _emit("Loading Project Baseline", 10)
        from services.risk_config_service import RiskConfigurationService
        from services.project_knowledge_service import ProjectKnowledgeService
        from services.milestone_dependency_service import MilestoneDependencyService
        
        risk_params = RiskConfigurationService.get_parameters(db_cursor)
        risk_thresholds = RiskConfigurationService.get_thresholds(db_cursor)
        impact_matrix = RiskConfigurationService.get_impact_matrix(db_cursor)
        alert_rules = RiskConfigurationService.get_alert_rules(db_cursor)
        
        scope_items = ProjectKnowledgeService.get_approved_baseline(db_cursor, project_id)
        all_baseline_items = ProjectKnowledgeService.get_full_baseline(db_cursor, project_id)
        dependency_graph = MilestoneDependencyService.build_rich_dependency_graph(db_cursor, project_id)
        dependency_context_block = ProjectKnowledgeService.get_dependency_context_block(dependency_graph)

        _emit("Extracting Activities", 35)
        extraction_result, cleaned_activities = cls._extract_and_normalize(
            project_id, document_id, document_text, db_cursor, activity_map, scope_items, all_baseline_items
        )

        _emit("Running In-Scope Evaluation Agent", 55)
        llm_risk_results = cls._classify_and_diagnose(
            project_id, cleaned_activities, scope_items, db_cursor, dependency_context_block
        )

        _emit("Calculating Execution Priorities", 70)
        state_snapshot = cls._analyze_execution_state(
            project_id, scope_items, llm_risk_results, extraction_result, db_cursor
        )
        
        _, backward_graph = MilestoneDependencyService.build_dependency_graph(db_cursor, project_id)
        from services.dependency_execution_state_resolver import DependencyExecutionStateResolver
        from services.derived_execution_state import DerivedExecutionState
        dep_analysis_results = DependencyExecutionStateResolver.analyze_static_graph(state_snapshot, backward_graph)
        derived_states = DerivedExecutionState.compute_derived_status(state_snapshot, backward_graph)

        _emit("Risk Reconciliation", 80)
        cls._reconcile_existing_risks(
            project_id, document_id, db_cursor, derived_states, extraction_result
        )

        _emit("Scoring and Persistence", 90)
        overall_risk = cls._score_and_persist_risks(
            project_id, document_id, db_cursor, state_snapshot, dep_analysis_results,
            risk_params, impact_matrix, alert_rules, risk_thresholds
        )
        
        _emit("Pipeline Complete", 100)
        return {"status": "success"}

    @classmethod
    def _extract_and_normalize(cls, project_id, document_id, document_text, db_cursor, activity_map, scope_items, all_baseline_items):
        from agents.risk_evaluator_subagents import ActivityExtractorAgent
        from agents.tracker_audit_agent import TrackerAuditAgent
        
        if 'pre_extracted_activities' in activity_map:
            extraction_result = activity_map['pre_extracted_activities']
        else:
            db_cursor.execute("SELECT id, title, risk_category, status FROM tracker_items WHERE project_id = %s AND status = 'OPEN'", (project_id,))
            active_items_list = [f"- {r['title'] if isinstance(r, dict) else r[1]} (Category: {r['risk_category'] if isinstance(r, dict) else r[2]})" for r in db_cursor.fetchall()]
            extraction_result = ActivityExtractorAgent.extract_activities(document_text, "\\n".join(active_items_list) if active_items_list else "None")
            
        raw_activities = extraction_result.get("activities", []) or extraction_result.get("extractions", [])
        
        seen_canonical = set()
        cleaned_activities = []
        for item in raw_activities:
            name = str(item.get("activity") or item.get("statement") or "").strip()
            classification = str(item.get("classification_type", "RISK")).strip().upper()
            if not name or classification == "PROGRESS_UPDATE": continue
                
            if classification in ["ACTION_ITEM", "DECISION", "CHANGE_REQUEST"]:
                item_type = "ACTION_ITEM" if classification != "CHANGE_REQUEST" else "NEW_REQUEST"
                if classification == "DECISION": item_type = "DECISION"
                TrackerAuditAgent.persist_tracker_item(
                    db_cursor, project_id, document_id, item_type, False, 0, 'LOW', 'GENERAL', 1.0, f"Captured as {classification}", False, title=name, status='OPEN'
                )
                continue

            canonical_title, is_in_scope = _resolve_tracker_title(name, None, scope_items, all_baseline_items)
            dedup_key = _normalize(canonical_title)
            if dedup_key in seen_canonical: continue
            seen_canonical.add(dedup_key)

            cleaned_activities.append({
                "activity": name, "classification_type": classification, "canonical_title": canonical_title,
                "is_in_scope": is_in_scope, "source_sentence": item.get("source_sentence", name),
                "extraction_confidence": item.get("confidence", 100)
            })
            
        return extraction_result, cleaned_activities

    @classmethod
    def _classify_and_diagnose(cls, project_id, cleaned_activities, scope_items, db_cursor, dependency_context_block):
        from services.project_knowledge_service import ProjectKnowledgeService
        from agents.risk_evaluator_subagents import BatchActivityRiskAgent
        
        ambiguous_activities = []
        for act_item in cleaned_activities:
            matched_si, confidence, _ = _deterministic_match(act_item["activity"], scope_items)
            if confidence < cls.DETERMINISTIC_CONFIDENCE_THRESHOLD:
                matched_si2, confidence2, _ = _deterministic_match(act_item["canonical_title"], scope_items)
                if confidence2 > confidence:
                    matched_si, confidence = matched_si2, confidence2
            ambiguous_activities.append({**act_item, "matched_si": matched_si, "confidence": confidence})
            
        activities_with_contexts = []
        for item in ambiguous_activities:
            context = ProjectKnowledgeService.get_activity_context(project_id, item["activity"], item["matched_si"])
            activities_with_contexts.append({**item, "context": context})
            
        if activities_with_contexts:
            milestone_progress_block = ProjectKnowledgeService.calculate_milestone_progress(db_cursor, project_id)
            combined_context = milestone_progress_block + ("\\n\\n" + dependency_context_block if dependency_context_block else "")
            return BatchActivityRiskAgent.evaluate_batch(activities_with_contexts, combined_context)
        return []

    @classmethod
    def _analyze_execution_state(cls, project_id, scope_items, llm_risk_results, extraction_result, db_cursor):
        state_snapshot = {}
        for db_scope_name, m_id in {r['scope_name'].lower(): r['milestone_id'] for r in _fetch_scope_mappings(db_cursor, project_id)}.items():
            state_snapshot[m_id] = {"status": "UNKNOWN", "blocked_by": [], "progress": None, "mom_evidence": ""}
            
        for result in llm_risk_results:
            c_title = result.get("matched_baseline_item") or result.get("activity")
            m_id = _get_milestone_id(c_title, db_cursor, project_id)
            if m_id:
                state_snapshot[m_id] = {
                    "status": str(result.get("status", "UNKNOWN")).upper(),
                    "blocked_by": result.get("blocked_by", []),
                    "progress": result.get("progress"),
                    "mom_evidence": result.get("evidence_text", ""),
                    "recommended_action": result.get("recommended_action")
                }
                
        from agents.risk_evaluator_subagents import DeliverableTimelineEvaluationAgent
        timeline_deliverables = DeliverableTimelineEvaluationAgent.evaluate_progress(scope_items, "", extraction_result.get("resolved_items", []))
        for t in timeline_deliverables:
            m_id = t.get("scope_item_id")
            if m_id and m_id in state_snapshot:
                state_snapshot[m_id]["status"] = str(t.get("progress_status", state_snapshot[m_id]["status"])).upper()
                state_snapshot[m_id]["mom_evidence"] += "\\n" + t.get("evidence_text", "")
                
        return state_snapshot

    @classmethod
    def _reconcile_existing_risks(cls, project_id, document_id, db_cursor, derived_states, extraction_result):
        from services.risk_reconciliation_engine import RiskReconciliationEngine
        from agents.tracker_audit_agent import TrackerAuditAgent
        
        db_cursor.execute("SELECT * FROM tracker_items WHERE project_id = %s AND status = 'OPEN'", (project_id,))
        columns = [col[0] for col in db_cursor.description]
        open_tracker_items = [dict(zip(columns, row)) for row in db_cursor.fetchall()]
        
        current_state = {"derived_states": derived_states, "resolved_items": extraction_result.get("resolved_items", [])}
        risks_to_resolve = RiskReconciliationEngine.reconcile_open_risks(open_tracker_items, current_state)
        
        for risk, reason, res_type in risks_to_resolve:
            TrackerAuditAgent.persist_tracker_item(
                db_cursor, project_id, document_id, risk.get("item_type", "ACTIVITY"),
                False, 0, 'LOW', 'RESOLVED', 1.0, f"[Type: {res_type}]\\nReason: {reason}", False,
                title=risk.get("title"), reference_id=risk.get("reference_id"), status='RESOLVED', resolve_only=True
            )

    @classmethod
    def _score_and_persist_risks(cls, project_id, document_id, db_cursor, state_snapshot, dep_analysis_results,
                                 risk_params, impact_matrix, alert_rules, risk_thresholds):
        from services.risk_scoring_engine import RiskScoringEngine
        from services.risk_ranking_engine import RiskRankingEngine
        from services.category_assignment_engine import CategoryAssignmentEngine
        from agents.tracker_audit_agent import TrackerAuditAgent
        from agents.alerting_agent import AlertingAgent
        
        tracker_items = []
        milestone_details = {r['id']: r for r in _fetch_milestones(db_cursor, project_id)}
        
        for m_id, state in state_snapshot.items():
            if state["status"] in ["UNKNOWN", "COMPLETED", "NOT_STARTED"]: continue
            
            dep_res = dep_analysis_results.get(m_id, {})
            is_root = dep_res.get("is_root_cause", False)
            cascade_c = dep_res.get("cascade_count", 0)
            imm_unlocks = dep_res.get("immediate_downstream_names", [])
            fut_unlocks = dep_res.get("all_downstream_names", [])
            
            risk_cat = CategoryAssignmentEngine.assign_category(
                [], "MILESTONE", state["status"], "UNKNOWN", is_root, cascade_c, fut_unlocks, len(imm_unlocks) > 0, milestone_details[m_id]['name']
            )
            
            score_res = RiskScoringEngine.calculate(
                status=state["status"], blocked_by=state["blocked_by"], is_root_cause=is_root, cascade_count=cascade_c,
                days_overdue=0, days_until_due=10, is_scope_creep=False, confidence=1.0, business_impact="HIGH",
                params=risk_params, impact_matrix=impact_matrix, category=risk_cat, immediate_unlocks=imm_unlocks, future_unlocks=fut_unlocks
            )
            
            item_score = score_res["risk_score"]
            if item_score > 0:
                tracker_items.append({
                    "title": milestone_details[m_id]['name'], "reference_id": m_id, "status": state["status"],
                    "item_type": "ACTIVITY", "risk_category": risk_cat, "risk_score": item_score,
                    "risk_level": score_res["severity"], "reasoning_data": score_res, "state": state
                })
                
        ranked_items = RiskRankingEngine.rank_items(tracker_items)
        for item in ranked_items:
            full_reasoning = RiskScoringEngine.format_reasoning(
                status=item["status"], progress=item["state"]["progress"], blocked_by=item["state"]["blocked_by"],
                entity_type=item["item_type"], category=item["risk_category"], next_milestone_name=None,
                next_milestone_date=None, days_to_next_milestone=None, breakdown=item["reasoning_data"]["score_breakdown"],
                mom_evidence=item["state"]["mom_evidence"], original_contract_sentence=None,
                immediate_unlocks=item["reasoning_data"].get("immediate_unlocks", []), future_unlocks=item["reasoning_data"].get("future_unlocks", [])
            )
            
            TrackerAuditAgent.persist_tracker_item(
                db_cursor, project_id, document_id, item["item_type"], False, item["risk_score"], item["risk_level"],
                item["risk_category"], 1.0, full_reasoning, True, title=item["title"], reference_id=item["reference_id"],
                priority_order=item.get("priority_order"), status='OPEN', risk_source='DERIVED', recommended_action=item["state"].get("recommended_action")
            )
            
            AlertingAgent.evaluate_and_trigger(item, alert_rules, project_id, db_cursor)
            
        return "HIGH" if ranked_items else "LOW"

def _fetch_scope_mappings(db_cursor, project_id):
    db_cursor.execute("SELECT m.id as milestone_id, s.name as scope_name FROM scope_items s JOIN scope_milestone_mapping mmap ON s.id = mmap.scope_item_id JOIN project_milestones m ON mmap.milestone_id = m.id WHERE s.project_id = %s", (project_id,))
    return db_cursor.fetchall()

def _fetch_milestones(db_cursor, project_id):
    db_cursor.execute("SELECT id, name, planned_date, status FROM project_milestones WHERE project_id = %s", (project_id,))
    return db_cursor.fetchall()

def _get_milestone_id(title, db_cursor, project_id):
    norm_title = _normalize(title)
    for r in _fetch_scope_mappings(db_cursor, project_id):
        if _normalize(r['scope_name']) in norm_title or norm_title in _normalize(r['scope_name']):
            return r['milestone_id']
    for r in _fetch_milestones(db_cursor, project_id):
        if _normalize(r['name']) in norm_title or norm_title in _normalize(r['name']):
            return r['id']
    return None
"""

final_code = prefix + new_class_body
with open(FILE_PATH, "w", encoding="utf-8") as f:
    f.write(final_code)
print("Successfully refactored RiskEvaluationAgent")
