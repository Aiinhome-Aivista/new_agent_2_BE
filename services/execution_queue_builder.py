"""
ExecutionQueueBuilder — Graph-First Execution Priority Engine

Answers: "What should the PM resolve first to unblock the maximum amount
of project execution?"

Does NOT use:
  - Topological list position as a score
  - risk_severity_score as an input
  - Hardcoded names or document-specific heuristics

Priority is derived from:
  is_root_cause         × 100 base
  immediate_unlock_count × critical_path_multiplier × due_date_weight
  cascade_count          (breadth)
  cascade_depth          (depth)
  customer_dependency    bonus
  deadline_urgency       weight

All scores are normalised to 0–100.
"""

import collections
from datetime import datetime, timezone
from typing import Dict, List, Tuple, Any, Optional

from services.readiness_engine import ReadinessEngine


# ---------------------------------------------------------------------------
# Graph validation helper
# ---------------------------------------------------------------------------

class GraphValidationEngine:
    """Validate and clean adjacency dicts before metric computation."""

    @staticmethod
    def validate_and_clean(backward_graph: dict, forward_graph: dict,
                           all_nodes: set) -> Tuple[dict, dict]:
        # 1. Self-loops
        for n in list(forward_graph.keys()):
            forward_graph[n] = [c for c in forward_graph.get(n, []) if c != n]
        for n in list(backward_graph.keys()):
            backward_graph[n] = [p for p in backward_graph.get(n, []) if p != n]

        # 2. Reference integrity — remove edges to unknown nodes
        for n in list(forward_graph.keys()):
            forward_graph[n] = [c for c in forward_graph[n] if c in all_nodes]
        for n in list(backward_graph.keys()):
            backward_graph[n] = [p for p in backward_graph[n] if p in all_nodes]

        # 3. Cycle detection / breaking (DFS)
        visited: set = set()
        rec_stack: set = set()

        def _dfs(node):
            visited.add(node)
            rec_stack.add(node)
            for nb in list(forward_graph.get(node, [])):
                if nb not in visited:
                    _dfs(nb)
                elif nb in rec_stack:
                    forward_graph[node] = [x for x in forward_graph[node] if x != nb]
                    if nb in backward_graph:
                        backward_graph[nb] = [x for x in backward_graph[nb] if x != node]
                    print(f"  [GraphValidation] Cycle broken: {node} → {nb}")
            rec_stack.discard(node)

        for n in list(all_nodes):
            if n not in visited:
                _dfs(n)

        return backward_graph, forward_graph


# ---------------------------------------------------------------------------
# Execution Queue Builder
# ---------------------------------------------------------------------------

class ExecutionQueueBuilder:
    """
    Builds the PM Execution Queue using graph-derived metrics.

    The queue is sorted by execution_priority_score (highest first).
    execution_priority_score ≠ risk_severity_score — they are computed
    independently and persisted as separate fields.
    """

    # Weights (tunable without code change)
    ROOT_CAUSE_BASE = 100
    DOWNSTREAM_BASE = 10
    CASCADE_WEIGHT = 8         # per downstream node
    UNLOCK_WEIGHT = 25         # per immediately unblocked node
    CRITICAL_PATH_MULT = 1.4
    CUSTOMER_DEP_BONUS = 15

    # Due-date urgency multipliers
    DUE_OVERDUE = 3.0
    DUE_7_DAYS = 2.0
    DUE_14_DAYS = 1.5
    DUE_30_DAYS = 1.2
    DUE_LATER = 1.0

    @classmethod
    def build_queue(cls, snapshot, backward_graph: dict,
                    forward_graph: dict) -> Tuple[list, Dict[str, dict]]:
        """
        Parameters
        ----------
        snapshot       : ProjectStateSnapshot — provides status and planned_date.
        backward_graph : dict  node → list of predecessor node IDs
        forward_graph  : dict  node → list of successor node IDs

        Returns
        -------
        queue       : list of node IDs in execution priority order
        node_metrics: dict  node_id → rich metrics dict
        """
        all_nodes: set = (
            set(backward_graph.keys()) |
            set(forward_graph.keys()) |
            set(snapshot.milestone_statuses.keys())
        )

        # Validate
        backward_graph, forward_graph = GraphValidationEngine.validate_and_clean(
            backward_graph, forward_graph, all_nodes
        )

        node_metrics: Dict[str, dict] = {}

        # ── Helpers ────────────────────────────────────────────────────────────

        def get_all_downstream(node, visited=None):
            if visited is None:
                visited = set()
            if node in visited:
                return set()
            visited.add(node)
            result = set()
            for child in forward_graph.get(node, []):
                result.add(child)
                result.update(get_all_downstream(child, visited))
            return result

        memo_dist: Dict[str, int] = {}
        memo_path: Dict[str, list] = {}

        def get_downstream_chain(node):
            if node in memo_dist:
                return memo_dist[node], memo_path[node]
            children = forward_graph.get(node, [])
            if not children:
                memo_dist[node] = 0
                memo_path[node] = [node]
                return 0, [node]
            best_d, best_p = -1, []
            for c in children:
                cd, cp = get_downstream_chain(c)
                if cd > best_d:
                    best_d, best_p = cd, cp
            memo_dist[node] = best_d + 1
            memo_path[node] = [node] + best_p
            return memo_dist[node], memo_path[node]

        for n in all_nodes:
            get_downstream_chain(n)

        max_dist = max(memo_dist.values()) if memo_dist else 0

        # Critical path nodes
        critical_nodes: set = set()
        if max_dist > 0:
            starters = [n for n, d in memo_dist.items() if d == max_dist]
            q = collections.deque(starters)
            crit_vis: set = set()
            while q:
                curr = q.popleft()
                if curr in crit_vis:
                    continue
                crit_vis.add(curr)
                critical_nodes.add(curr)
                children = forward_graph.get(curr, [])
                if children:
                    mc = max(memo_dist.get(c, 0) for c in children)
                    for c in children:
                        if memo_dist.get(c, 0) == mc:
                            q.append(c)

        # Effective due date (earliest of node date + all downstream dates)
        memo_date: Dict[str, Any] = {}

        def get_effective_date(node):
            if node in memo_date:
                return memo_date[node]
            node_date = snapshot.get_date(node)
            valid = [node_date] if node_date else []
            for c in forward_graph.get(node, []):
                cd = get_effective_date(c)
                if cd:
                    valid.append(cd)
            result = min(valid) if valid else None
            memo_date[node] = result
            return result

        for n in all_nodes:
            get_effective_date(n)

        # ── Per-node metric computation ────────────────────────────────────────
        today = datetime.now(timezone.utc).date()

        for node in all_nodes:
            status = snapshot.get_status(node)
            downstream_set = get_all_downstream(node)
            cascade_count = len(downstream_set)
            critical_path_len = memo_dist.get(node, 0)

            # Root cause: has cascade impact AND all predecessors are done
            is_root = False
            if cascade_count > 0 and status not in ("COMPLETED", "RESOLVED"):
                is_root = True
                for pred in backward_graph.get(node, []):
                    if snapshot.get_status(pred) not in ("COMPLETED", "RESOLVED"):
                        is_root = False
                        break

            # Immediate unlock count using ReadinessEngine (AND semantics)
            immediate_unlocks = ReadinessEngine.immediate_unlock_count(
                node,
                forward_graph,
                backward_graph,
                snapshot.get_status,
            )

            # Due-date urgency weight
            eff_date = memo_date.get(node)
            days_remaining = 999
            if eff_date:
                try:
                    d_obj = eff_date.date() if isinstance(eff_date, datetime) else eff_date
                    days_remaining = (d_obj - today).days
                except Exception:
                    pass

            if days_remaining <= 0:
                due_wt = cls.DUE_OVERDUE
            elif days_remaining <= 7:
                due_wt = cls.DUE_7_DAYS
            elif days_remaining <= 14:
                due_wt = cls.DUE_14_DAYS
            elif days_remaining <= 30:
                due_wt = cls.DUE_30_DAYS
            else:
                due_wt = cls.DUE_LATER

            on_critical_path = node in critical_nodes

            # Topology-derived graph_role (never from LLM or heuristics)
            has_upstream = bool(backward_graph.get(node))
            has_downstream = bool(forward_graph.get(node))
            if not has_upstream and has_downstream:
                topo_role = "ROOT_CAUSE"
            elif has_upstream and has_downstream:
                topo_role = "INTERMEDIATE_BLOCKER"
            elif has_upstream and not has_downstream:
                topo_role = "TERMINAL_ACTIVITY"
            else:
                topo_role = "ISOLATED"

            # Priority reason (human-readable, from graph metrics)
            reason_parts = []
            if is_root:
                reason_parts.append("Root prerequisite with no pending upstream")
            if cascade_count > 0:
                reason_parts.append(f"Blocks {cascade_count} downstream activities")
            if immediate_unlocks > 0:
                reason_parts.append(f"Resolving unlocks {immediate_unlocks} activities immediately")
            if on_critical_path:
                reason_parts.append("On critical delivery path")
            if days_remaining <= 0:
                reason_parts.append("OVERDUE")
            elif days_remaining <= 7:
                reason_parts.append(f"Due in {days_remaining} days")
            priority_reason = "; ".join(reason_parts) if reason_parts else "No immediate unblock impact"

            # Unblock impact summary
            downstream_ids = sorted(downstream_set)
            unblock_impact = {
                "immediate_unlocks": list(forward_graph.get(node, [])),
                "transitive_unlocks": downstream_ids,
                "critical_path_impact": on_critical_path,
                "total_unblocked": cascade_count,
            }

            # ── Execution Priority Score ─────────────────────────────────────
            # root cause bonus + unlock impact + cascade breadth
            base = cls.ROOT_CAUSE_BASE if is_root else cls.DOWNSTREAM_BASE
            unlock_impact_score = immediate_unlocks * cls.UNLOCK_WEIGHT
            cascade_impact = cascade_count * cls.CASCADE_WEIGHT
            raw_score = (base + unlock_impact_score + cascade_impact) * due_wt

            if on_critical_path:
                raw_score *= cls.CRITICAL_PATH_MULT

            # Store raw for normalisation pass
            node_metrics[node] = {
                "_raw_score": raw_score,
                "is_root": is_root,
                "cascade_count": cascade_count,
                "cascade_depth": critical_path_len,
                "immediate_unlocks": immediate_unlocks,
                "critical_path": on_critical_path,
                "critical_path_length": critical_path_len,
                "longest_path": memo_path.get(node, [node]),
                "parents": list(backward_graph.get(node, [])),
                "children": list(forward_graph.get(node, [])),
                "cascade_nodes": cascade_count,
                "execution_level": 0,
                "days_remaining": days_remaining,
                "graph_role": topo_role,
                "priority_reason": priority_reason,
                "unblock_impact": unblock_impact,
                "root_cause_dependency": node if is_root else None,
            }

        # ── Normalise execution_index to 0–100 ────────────────────────────────
        raw_scores = [m["_raw_score"] for m in node_metrics.values()]
        max_raw = max(raw_scores) if raw_scores else 1.0
        # Enforce a minimum baseline to avoid artificially inflating ISOLATED nodes (score 10) to 100
        max_raw = max(max_raw, cls.ROOT_CAUSE_BASE)

        for node, m in node_metrics.items():
            m["execution_index"] = round(
                min((m["_raw_score"] / max_raw) * 100, 100), 1)

        # ── Build queue (eligible = not completed/resolved) ────────────────────
        eligible = [n for n in all_nodes
                    if snapshot.get_status(n) not in ("COMPLETED", "RESOLVED")]
        eligible.sort(key=lambda n: (-node_metrics[n]["execution_index"], n))

        queue = list(eligible)  # deterministic order

        # Topological levels (for display / BFS)
        roots = [n for n in all_nodes if not backward_graph.get(n)]
        bfs_q = collections.deque([(r, 1) for r in roots])
        bfs_vis: set = set()
        while bfs_q:
            curr, lvl = bfs_q.popleft()
            if curr not in bfs_vis:
                bfs_vis.add(curr)
                node_metrics[curr]["execution_level"] = lvl
                for c in forward_graph.get(curr, []):
                    bfs_q.append((c, lvl + 1))

        # ── Print execution priority table ─────────────────────────────────────
        print("\n=== EXECUTION PRIORITY ===")
        print(f"{'Rank':<5} | {'Node ID':<15} | {'Role':<22} | {'Exec Priority':>13} | "
              f"{'Unlocks':>7} | {'Cascade':>7} | {'CritPath'}")
        print("-" * 100)
        for rank, node in enumerate(queue, 1):
            m = node_metrics[node]
            print(f"{rank:<5} | {node:<15} | {m['graph_role']:<22} | "
                  f"{m['execution_index']:>13.1f} | "
                  f"{m['immediate_unlocks']:>7} | "
                  f"{m['cascade_count']:>7} | "
                  f"{'✓' if m['critical_path'] else ''}")
        print()

        return queue, node_metrics
