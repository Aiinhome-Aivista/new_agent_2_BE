"""
LangGraph Orchestration — evaluation_graph.py

Defines EvaluationState (the typed pipeline state) and the compiled
evaluation graph singleton used by RiskEvaluationAgent.evaluate_document().

CURRENT STATUS:
  The graph is wired and the public evaluate_document() calls it.
  Nodes 1-3 are fully active (load baseline, extract, detect closure).
  Nodes 4-8 currently delegate to _evaluate_document_legacy() for the
  portions of the monolith not yet extracted into standalone helpers.

  This is intentional — it lets the graph run end-to-end TODAY with
  zero behavior change, while clearly marking which nodes are ready
  for full extraction in future iterations.

EXTRACTION ROADMAP (do one at a time, test after each):
  Phase A: Extract closure handler → _execute_project_closure()   [L1025-L1179]
  Phase B: Extract scoring block   → _run_scoring_and_graph()     [L1401-L2390]
  Phase C: Extract persist block   → _persist_all_tracker_items() [L2441-L2730]
  Phase D: Extract aggregation     → _run_final_aggregation()     [L2392-L2440]
  Phase E: Replace node delegates with direct calls; delete legacy
"""

import traceback
from langgraph.graph import StateGraph, END
from typing import TypedDict, Optional, Any
from services.telemetry_service import telemetry
from core.structured_logger import agent_logger


# ── Pipeline State ─────────────────────────────────────────────────────────────
# Single source of truth for everything the pipeline passes between nodes.

class EvaluationState(TypedDict):
    # ── Inputs (set before graph starts) ──────────────────────────────────────
    project_id: int
    document_id: int
    document_text: str
    db_cursor: Any
    activity_map: dict
    request_map: dict
    emit: Optional[Any]

    # ── Intermediate results populated by nodes ────────────────────────────────
    risk_params: Optional[dict]
    risk_thresholds: Optional[dict]
    scope_items: Optional[list]
    all_baseline_items: Optional[list]
    dependency_graph: Optional[dict]
    dependency_context_block: Optional[str]
    extraction_result: Optional[dict]
    raw_activities: Optional[list]
    resolved_items: Optional[list]

    # ── Routing flags ──────────────────────────────────────────────────────────
    is_project_closed: bool
    pipeline_error: Optional[str]

    # ── Final output ───────────────────────────────────────────────────────────
    final_result: Optional[dict]


# ── NODE 1: Load baseline — DB reads, no LLM ──────────────────────────────────
def node_load_baseline(state: EvaluationState) -> EvaluationState:
    """
    Loads risk config, scope items, full baseline, dependency graph.
    Pure DB reads. No LLM call.
    STATUS: Fully active.
    """
    with telemetry.span("node_load_baseline", {"project_id": state.get('project_id'), "document_id": state.get('document_id')}):
        try:
            from services.risk_config_service import RiskConfigurationService
            from services.project_knowledge_service import ProjectKnowledgeService
            from services.milestone_dependency_service import MilestoneDependencyService

            db  = state['db_cursor']
            pid = state['project_id']

            state['risk_params']      = RiskConfigurationService.get_parameters(db)
            state['risk_thresholds']  = RiskConfigurationService.get_thresholds(db)
            state['scope_items']      = ProjectKnowledgeService.get_approved_baseline(db, pid)
            state['all_baseline_items'] = ProjectKnowledgeService.get_full_baseline(db, pid)

            dep_graph = MilestoneDependencyService.build_rich_dependency_graph(db, pid)
            state['dependency_graph']        = dep_graph
            state['dependency_context_block'] = ProjectKnowledgeService.get_dependency_context_block(dep_graph)

            if state.get('emit'):
                state['emit']("Loading Project Baseline", 10)

        except Exception as e:
            state['pipeline_error'] = f"node_load_baseline: {e}"
            agent_logger.error(f"[Graph] ERROR in node_load_baseline: {e}", exc_info=True)
            print(f"  [Graph] ERROR in node_load_baseline: {traceback.format_exc()}")
        return state


# ── NODE 2: Extract activities — LLM CALL #1 ──────────────────────────────────
def node_extract_activities(state: EvaluationState) -> EvaluationState:
    """
    Calls ActivityExtractorAgent (uses structured output via LLMService.generate_structured).
    Applies 3-tier completion normalizer.
    STATUS: Fully active.
    """
    with telemetry.span("node_extract_activities", {"project_id": state.get('project_id'), "document_id": state.get('document_id')}):
        try:
            from agents.risk_evaluator_subagents import ActivityExtractorAgent
            from agents.risk_evaluation_agent import _normalize_completion_signals

            if 'pre_extracted_activities' in (state.get('activity_map') or {}):
                extraction_result = state['activity_map']['pre_extracted_activities']
            else:
                db  = state['db_cursor']
                pid = state['project_id']
                db.execute(
                    "SELECT id, title, risk_category, status FROM tracker_items "
                    "WHERE project_id = %s AND status = 'OPEN'", (pid,)
                )
                active_items = db.fetchall() or []
                active_tracker_block = "\n".join(
                    [f"- {it.get('title', '')}" for it in active_items]
                ) or "None"

                extraction_result = ActivityExtractorAgent.extract_activities(
                    document_text=state['document_text'],
                    active_tracker_block=active_tracker_block,
                )

            extraction_result = _normalize_completion_signals(
                extraction_result,
                db_cursor=state['db_cursor'],
                project_id=state['project_id'],
                document_id=state['document_id'],
            )

            state['extraction_result'] = extraction_result
            state['raw_activities'] = (
                extraction_result.get('raw_activities') or
                extraction_result.get('activities') or
                extraction_result.get('extractions') or []
            )
            state['resolved_items'] = extraction_result.get('resolved_items', [])

            if state.get('emit'):
                state['emit']("Extracting Activities", 35)

        except Exception as e:
            state['pipeline_error'] = f"node_extract_activities: {e}"
            agent_logger.error(f"[Graph] ERROR in node_extract_activities: {e}", exc_info=True)
            print(f"  [Graph] ERROR in node_extract_activities: {traceback.format_exc()}")
        return state


# ── NODE 3A: Closure detection — routing node, no LLM ─────────────────────────
def node_detect_closure(state: EvaluationState) -> EvaluationState:
    """
    Runs _detect_project_closure(). Sets state['is_project_closed'].
    STATUS: Fully active.
    """
    with telemetry.span("node_detect_closure", {"project_id": state.get('project_id'), "document_id": state.get('document_id')}):
        try:
            from agents.risk_evaluation_agent import _detect_project_closure
            state['is_project_closed'] = _detect_project_closure(
                state.get('extraction_result', {}),
                state['db_cursor'],
                state['project_id'],
            )
        except Exception as e:
            state['is_project_closed'] = False
            agent_logger.warning(f"[Graph] WARNING in node_detect_closure: {e}")
            print(f"  [Graph] WARNING in node_detect_closure: {e}")
        return state


# ── NODE 3B + 4-8: Main pipeline — delegates to legacy ────────────────────────
def node_run_pipeline(state: EvaluationState) -> EvaluationState:
    """
    Runs the full evaluation pipeline from context-building through
    aggregation and DB persist.

    CURRENT STATUS: Delegates to _evaluate_document_legacy() for the
    scoring, persist, and aggregation sections (nodes 4-8).
    This is intentional — these sections are deeply intertwined inside the
    legacy monolith. They will be extracted one-by-one per the roadmap above.

    When closure IS detected, this node is SKIPPED entirely (graph routes to END).
    When closure is NOT detected, this node handles:
      - Context building (canonical resolution, dedup, matching)
      - LLM batch risk evaluation
      - Scoring + dependency graph
      - Tracker persistence
      - Final aggregation + DB writes
    """
    with telemetry.span("node_run_pipeline", {"project_id": state.get('project_id'), "document_id": state.get('document_id')}):
        try:
            from agents.risk_evaluation_agent import RiskEvaluationAgent

            result = RiskEvaluationAgent._evaluate_document_legacy(
                project_id=state['project_id'],
                document_id=state['document_id'],
                document_text=state['document_text'],
                db_cursor=state['db_cursor'],
                activity_map={
                    **(state.get('activity_map') or {}),
                    # Pass already-extracted activities so legacy skips LLM call #1
                    'pre_extracted_activities': state.get('extraction_result') or {},
                },
                request_map=state.get('request_map') or {},
                emit=state.get('emit'),
            )
            state['final_result'] = result

        except Exception as e:
            state['pipeline_error'] = f"node_run_pipeline: {e}"
            agent_logger.error(f"[Graph] ERROR in node_run_pipeline: {e}", exc_info=True)
            print(f"  [Graph] ERROR in node_run_pipeline: {traceback.format_exc()}")
        return state


# ── NODE 3B: Handle project closure ───────────────────────────────────────────
def node_handle_closure(state: EvaluationState) -> EvaluationState:
    """
    Project is closed — runs the closure branch of _evaluate_document_legacy.
    STATUS: Delegates to legacy (closure block at L1025-L1179).
    """
    with telemetry.span("node_handle_closure", {"project_id": state.get('project_id'), "document_id": state.get('document_id')}):
        try:
            from agents.risk_evaluation_agent import RiskEvaluationAgent

            # Re-run legacy with the same extraction_result — legacy will
            # detect closure again (deterministic) and execute the closure path.
            result = RiskEvaluationAgent._evaluate_document_legacy(
                project_id=state['project_id'],
                document_id=state['document_id'],
                document_text=state['document_text'],
                db_cursor=state['db_cursor'],
                activity_map={
                    **(state.get('activity_map') or {}),
                    'pre_extracted_activities': state.get('extraction_result') or {},
                },
                request_map=state.get('request_map') or {},
                emit=state.get('emit'),
            )
            state['final_result'] = result

            if state.get('emit'):
                state['emit']("Completed", 100)

        except Exception as e:
            state['pipeline_error'] = f"node_handle_closure: {e}"
            agent_logger.error(f"[Graph] ERROR in node_handle_closure: {e}", exc_info=True)
            print(f"  [Graph] ERROR in node_handle_closure: {traceback.format_exc()}")
        return state


# ── ROUTING FUNCTIONS ──────────────────────────────────────────────────────────

def route_after_extraction(state: EvaluationState) -> str:
    """Skip to END if extraction failed, otherwise detect closure."""
    if state.get('pipeline_error'):
        return END
    return 'detect_closure'


def route_after_closure_detection(state: EvaluationState) -> str:
    """
    Project closed  → handle_closure → END
    Normal document → run_pipeline   → END
    Error           → END (skip remaining nodes)
    """
    if state.get('pipeline_error'):
        return END
    if state.get('is_project_closed'):
        return 'handle_closure'
    return 'run_pipeline'


# ── BUILD THE GRAPH ────────────────────────────────────────────────────────────

def build_evaluation_graph():
    """
    Builds and compiles the evaluation graph.
    Call once at module load; reuse the compiled graph for every document.

    Current graph topology:
      load_baseline → extract_activities → detect_closure ─┬→ handle_closure → END
                                                            └→ run_pipeline   → END
    """
    graph = StateGraph(EvaluationState)

    graph.add_node('load_baseline',      node_load_baseline)
    graph.add_node('extract_activities', node_extract_activities)
    graph.add_node('detect_closure',     node_detect_closure)
    graph.add_node('handle_closure',     node_handle_closure)
    graph.add_node('run_pipeline',       node_run_pipeline)

    graph.set_entry_point('load_baseline')
    graph.add_edge('load_baseline', 'extract_activities')

    graph.add_conditional_edges(
        'extract_activities',
        route_after_extraction,
        {END: END, 'detect_closure': 'detect_closure'},
    )

    graph.add_conditional_edges(
        'detect_closure',
        route_after_closure_detection,
        {
            'handle_closure': 'handle_closure',
            'run_pipeline':   'run_pipeline',
            END: END,
        },
    )

    graph.add_edge('handle_closure', END)
    graph.add_edge('run_pipeline',   END)

    return graph.compile()


# ── Module-level singleton ─────────────────────────────────────────────────────
# Built once on first call, reused for every document (avoids recompile overhead).

_EVALUATION_GRAPH = None


def get_evaluation_graph():
    """Returns the compiled evaluation graph singleton."""
    global _EVALUATION_GRAPH
    if _EVALUATION_GRAPH is None:
        _EVALUATION_GRAPH = build_evaluation_graph()
    return _EVALUATION_GRAPH
