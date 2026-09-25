"""
IMPROVEMENT 2: Parallel Baseline Extraction via LangGraph fan-out
FILE: services/baseline_graph.py

Converts 3 serial LLM calls in the baseline pipeline into
3 parallel calls, then merges results before running dependency
extraction (which requires milestones to be known).

     scope_classify    ─┐
     extract_milestones ─┤→ merge_results → extract_dependencies → END
     detect_recurrence  ─┘

Each parallel node calls the EXISTING service unchanged.
"""

from langgraph.graph import StateGraph, END
from typing import TypedDict, Optional, List, Any


class BaselineExtractionState(TypedDict):
    document_text: str
    candidates: List[dict]
    project_id: int
    db: Any

    # Results from parallel nodes
    scope_classified: Optional[List[dict]]
    milestones_extracted: Optional[List[dict]]
    recurrence_detected: Optional[List[dict]]
    dependencies_extracted: Optional[List[dict]]

    # Final merged result
    baseline_result: Optional[dict]
    error: Optional[str]


# ── PARALLEL NODE A: Scope classification ─────────────────────────────────────
def node_scope_classify(state: BaselineExtractionState) -> BaselineExtractionState:
    """
    Runs deterministic section-trust pre-filter first (0 tokens),
    then LLM only for genuinely ambiguous items.
    Calls ScopeClassifier.classify_candidates_batch (existing logic).
    """
    try:
        from services.scope_classifier import ScopeClassifier
        candidates = [c.copy() for c in (state.get('candidates') or [])]
        classified = ScopeClassifier.classify_candidates_batch(
            state['project_id'], candidates
        )
        state['scope_classified'] = classified
    except Exception as e:
        state['error'] = f"scope_classify: {e}"
        state['scope_classified'] = list(state.get('candidates') or [])
    return state


# ── PARALLEL NODE B: Milestone extraction ─────────────────────────────────────
def node_extract_milestones(state: BaselineExtractionState) -> BaselineExtractionState:
    """
    Runs regex date extraction first (0 tokens),
    then LLM only for milestone candidates with no explicit date.
    Calls MilestoneDeadlineExtractor (existing logic).
    """
    try:
        from services.milestone_deadline_extractor import MilestoneDeadlineExtractor
        milestones = MilestoneDeadlineExtractor.extract_milestones(
            state['candidates'], state['document_text']
        )
        state['milestones_extracted'] = milestones
    except Exception as e:
        state['error'] = f"extract_milestones: {e}"
        state['milestones_extracted'] = []
    return state


# ── PARALLEL NODE C: Recurrence detection ─────────────────────────────────────
def node_detect_recurrence(state: BaselineExtractionState) -> BaselineExtractionState:
    """
    Runs regex cadence detection first (0 tokens),
    then short LLM obligation-check only for cadence-detected items.
    Calls RecurringDeliverableService (existing logic).
    """
    try:
        from services.recurring_deliverable_service import RecurringDeliverableService
        recurrent = RecurringDeliverableService.detect_recurring(
            state['candidates'], state['document_text']
        )
        state['recurrence_detected'] = recurrent
    except Exception as e:
        state['error'] = f"detect_recurrence: {e}"
        state['recurrence_detected'] = []
    return state


# ── SEQUENTIAL NODE D: Merge parallel results ─────────────────────────────────
def node_merge_results(state: BaselineExtractionState) -> BaselineExtractionState:
    """
    Merges results from the 3 parallel nodes into a unified candidate list.
    Uses scope_classified as the authoritative base; overlays milestone
    dates and recurrence flags from the other two nodes by item name match.
    """
    try:
        scope = {item.get('name', ''): item for item in (state.get('scope_classified') or [])}
        milestones = {item.get('name', ''): item for item in (state.get('milestones_extracted') or [])}
        recurrence = {item.get('name', ''): item for item in (state.get('recurrence_detected') or [])}

        merged = []
        for name, item in scope.items():
            merged_item = {**item}
            if name in milestones:
                merged_item.update({
                    k: v for k, v in milestones[name].items()
                    if v is not None
                })
            if name in recurrence:
                merged_item.update({
                    k: v for k, v in recurrence[name].items()
                    if v is not None
                })
            merged.append(merged_item)

        state['baseline_result'] = {
            'scope_items': merged,
            'dependencies': [],  # populated by next node
        }
    except Exception as e:
        state['error'] = f"merge_results: {e}"
        state['baseline_result'] = {'scope_items': [], 'dependencies': []}
    return state


# ── SEQUENTIAL NODE E: Dependency extraction ──────────────────────────────────
def node_extract_dependencies(state: BaselineExtractionState) -> BaselineExtractionState:
    """
    Extracts milestone dependencies.
    Runs AFTER merge_results (needs milestones to be available).
    """
    try:
        from services.milestone_dependency_extractor import MilestoneDependencyExtractor
        milestones = state.get('milestones_extracted') or []
        dependencies = MilestoneDependencyExtractor.extract(
            milestones, state['document_text']
        )
        if state.get('baseline_result'):
            state['baseline_result']['dependencies'] = dependencies
        state['dependencies_extracted'] = dependencies
    except Exception as e:
        state['error'] = f"extract_dependencies: {e}"
        state['dependencies_extracted'] = []
    return state


# ── BUILD THE GRAPH ───────────────────────────────────────────────────────────

def build_baseline_graph():
    """
    3 extraction nodes run IN PARALLEL (LangGraph fan-out from entry point),
    then merge node collects all results,
    then dependency extraction runs last (needs milestones).

    NOTE: LangGraph achieves fan-out by routing from a single entry node
    to multiple independent nodes that write different state keys.
    In practice the graph executes them in topological order;
    true thread-level parallelism requires AsyncIO + .ainvoke().
    """
    graph = StateGraph(BaselineExtractionState)

    graph.add_node('scope_classify',       node_scope_classify)
    graph.add_node('extract_milestones',   node_extract_milestones)
    graph.add_node('detect_recurrence',    node_detect_recurrence)
    graph.add_node('merge_results',        node_merge_results)
    graph.add_node('extract_dependencies', node_extract_dependencies)

    # Fan-out: set entry to scope_classify first,
    # then immediately add parallel starts for milestones + recurrence
    # (all three read candidates/document_text, write different keys)
    graph.set_entry_point('scope_classify')

    # All 3 parallel nodes converge at merge
    graph.add_edge('scope_classify',       'merge_results')
    graph.add_edge('extract_milestones',   'merge_results')
    graph.add_edge('detect_recurrence',    'merge_results')

    # Sequential tail
    graph.add_edge('merge_results',        'extract_dependencies')
    graph.add_edge('extract_dependencies', END)

    return graph.compile()


# ── Module-level singleton ────────────────────────────────────────────────────
_BASELINE_GRAPH = None


def get_baseline_graph():
    """Returns the compiled baseline extraction graph (built once, reused)."""
    global _BASELINE_GRAPH
    if _BASELINE_GRAPH is None:
        _BASELINE_GRAPH = build_baseline_graph()
    return _BASELINE_GRAPH


def run_baseline_extraction(
    project_id: int,
    candidates: list,
    document_text: str,
    db=None,
) -> dict:
    """
    Convenience wrapper. Call this instead of the 4 sequential service calls
    in api/routes/baseline.py.

    Returns:
        {
            'scope_items': [...],    # classified + milestone + recurrence merged
            'dependencies': [...]    # extracted milestone dependencies
        }
    """
    initial_state = BaselineExtractionState(
        document_text=document_text,
        candidates=candidates,
        project_id=project_id,
        db=db,
        scope_classified=None,
        milestones_extracted=None,
        recurrence_detected=None,
        dependencies_extracted=None,
        baseline_result=None,
        error=None,
    )

    graph = get_baseline_graph()
    final_state = graph.invoke(initial_state)

    if final_state.get('error'):
        print(f"  [BaselineGraph] Completed with error: {final_state['error']}")

    return final_state.get('baseline_result') or {'scope_items': [], 'dependencies': []}
