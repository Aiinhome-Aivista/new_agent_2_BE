"""
ReadinessEngine — Centralized AND/OR Prerequisite Readiness Evaluation

Single source of truth for determining whether an activity can proceed
based on its dependency prerequisites.

NEVER compute readiness inline in DependencyGraphBuilder or ExecutionQueueBuilder.
Always call ReadinessEngine.evaluate().
"""

from typing import List, Dict, Optional, Any
from dataclasses import dataclass, field


# ---------------------------------------------------------------------------
# Readiness Result
# ---------------------------------------------------------------------------

@dataclass
class ReadinessResult:
    """
    Result of evaluating whether an activity can proceed.

    status:
        READY              — all required AND-prerequisites satisfied
        BLOCKED            — one or more required AND-prerequisites unsatisfied
        WAITING_ON_EXTERNAL — blocked by external party (Customer/Vendor)
        NO_PREREQUISITES   — no upstream dependency edges

    blocking_prerequisites: IDs of unsatisfied prerequisite nodes
    satisfied_prerequisites: IDs of satisfied prerequisite nodes
    all_and_satisfied: True only if ALL required AND-prerequisites are done
    """
    status: str
    blocking_prerequisites: List[str] = field(default_factory=list)
    satisfied_prerequisites: List[str] = field(default_factory=list)
    all_and_satisfied: bool = False
    blocking_names: List[str] = field(default_factory=list)
    reason: str = ""


# ---------------------------------------------------------------------------
# Completed / resolved statuses
# ---------------------------------------------------------------------------

_DONE_STATUSES = frozenset({
    "COMPLETED", "RESOLVED", "DONE", "CLOSED", "ACCEPTED",
    "DELIVERED", "SIGNED_OFF", "APPROVED",
})

_EXTERNAL_STATUSES = frozenset({
    "WAITING_ON_CUSTOMER", "WAITING_ON_VENDOR", "WAITING_ON_EXTERNAL",
})


def _is_done(status: str) -> bool:
    return (status or "").upper() in _DONE_STATUSES


# ---------------------------------------------------------------------------
# ReadinessEngine
# ---------------------------------------------------------------------------

class ReadinessEngine:
    """
    Evaluate activity readiness from the dependency graph.

    Usage:
        result = ReadinessEngine.evaluate(
            node_id="cand_5",
            bwd=backward_graph,        # {target_id: [source_ids]}
            status_fn=lambda nid: candidate_dict[nid]["status"],
            owner_fn=lambda nid: candidate_dict[nid].get("owner", "INTERNAL"),
            edge_conditions=edge_conditions,  # {(src,tgt): "AND"|"OR"|"UNKNOWN"}
            name_fn=lambda nid: candidate_dict[nid].get("activity", nid),
        )
    """

    @staticmethod
    def evaluate(
        node_id: str,
        bwd: Dict[str, List[str]],
        status_fn,
        owner_fn=None,
        edge_conditions: Optional[Dict[tuple, str]] = None,
        name_fn=None,
        unresolved_count: int = 0,
    ) -> ReadinessResult:
        """
        Determine readiness of `node_id` from its upstream prerequisites.

        AND prerequisites: activity is READY only when ALL are satisfied.
        OR prerequisites: activity is READY when ANY ONE is satisfied.
        UNKNOWN: treated as AND (conservative).

        Parameters
        ----------
        node_id      : canonical ID of the activity to evaluate
        bwd          : backward graph {target_id -> [source_ids]}
        status_fn    : callable(node_id) -> status string
        owner_fn     : callable(node_id) -> owner string, or None
        edge_conditions : dict {(src_id, tgt_id) -> "AND"|"OR"|"UNKNOWN"}
        name_fn      : callable(node_id) -> display_name, or None
        """
        prereq_ids = list(bwd.get(node_id, []))

        if not prereq_ids:
            return ReadinessResult(
                status="NO_PREREQUISITES",
                all_and_satisfied=True,
                reason="No upstream dependencies",
            )

        if edge_conditions is None:
            edge_conditions = {}

        # Separate AND / OR prerequisites
        and_prereqs = []
        or_prereqs = []
        for pid in prereq_ids:
            cond = edge_conditions.get((pid, node_id), "AND")
            if cond == "OR":
                or_prereqs.append(pid)
            else:
                and_prereqs.append(pid)  # AND + UNKNOWN treated as AND

        # Evaluate AND prerequisites (ALL must be satisfied)
        blocking_and = []
        satisfied_and = []
        for pid in and_prereqs:
            status = status_fn(pid)
            if _is_done(status):
                satisfied_and.append(pid)
            else:
                blocking_and.append(pid)

        # Evaluate OR prerequisites (at least ONE must be satisfied)
        satisfied_or = []
        blocking_or = []
        for pid in or_prereqs:
            status = status_fn(pid)
            if _is_done(status):
                satisfied_or.append(pid)
            else:
                blocking_or.append(pid)

        # OR is satisfied if at least one is done (or empty)
        or_satisfied = (not or_prereqs) or (len(satisfied_or) > 0)

        # AND is satisfied if nothing is blocking
        and_satisfied = len(blocking_and) == 0

        all_and_satisfied = and_satisfied and or_satisfied

        # Determine effective blocking list
        blocking = list(blocking_and)
        if not or_satisfied:
            blocking += list(blocking_or)

        satisfied = list(satisfied_and) + list(satisfied_or)

        # Resolve display names
        blocking_names = []
        if name_fn:
            blocking_names = [name_fn(bid) for bid in blocking]

        # Check if any blocker is external
        is_external = False
        if owner_fn and blocking:
            for bid in blocking:
                owner = (owner_fn(bid) or "").upper()
                if owner in ("CUSTOMER", "VENDOR", "THIRD_PARTY", "EXTERNAL"):
                    is_external = True
                    break

        if not blocking:
            if unresolved_count > 0:
                status = "BLOCKED_UNRESOLVED_DEPENDENCY"
            else:
                status = "READY"
        elif is_external:
            status = "WAITING_ON_EXTERNAL"
        else:
            status = "BLOCKED"

        reason_parts = []
        if blocking_and:
            reason_parts.append(
                f"AND prerequisites not satisfied: {blocking_names or blocking_and}"
            )
        if not or_satisfied:
            reason_parts.append("No OR prerequisite satisfied")
        if not blocking and unresolved_count > 0:
            reason_parts.append(f"Blocked by {unresolved_count} unresolved dependencies")

        return ReadinessResult(
            status=status,
            blocking_prerequisites=blocking,
            satisfied_prerequisites=satisfied,
            all_and_satisfied=all_and_satisfied,
            blocking_names=blocking_names,
            reason="; ".join(reason_parts) if reason_parts else "All prerequisites satisfied",
        )

    @staticmethod
    def evaluate_all(
        node_ids: List[str],
        bwd: Dict[str, List[str]],
        status_fn,
        owner_fn=None,
        edge_conditions: Optional[Dict[tuple, str]] = None,
        name_fn=None,
        unresolved_count_fn=None,
    ) -> Dict[str, ReadinessResult]:
        """Evaluate readiness for all given nodes. Returns {node_id: ReadinessResult}."""
        return {
            nid: ReadinessEngine.evaluate(
                nid, bwd, status_fn, owner_fn,
                edge_conditions, name_fn,
                unresolved_count_fn(nid) if unresolved_count_fn else 0
            )
            for nid in node_ids
        }

    @staticmethod
    def immediate_unlock_count(
        node_id: str,
        fwd: Dict[str, List[str]],
        bwd: Dict[str, List[str]],
        status_fn,
        edge_conditions: Optional[Dict[tuple, str]] = None,
    ) -> int:
        """
        Count how many downstream children become READY if `node_id` is resolved.

        A child counts as 'immediately unlocked' ONLY when:
        - `node_id` is the LAST unsatisfied AND-prerequisite for that child.
        - All other prerequisites of the child are already satisfied.

        This implements proper AND-prerequisite semantics: resolving one of N
        prerequisites does NOT unlock the child unless all others are already done.
        """
        count = 0
        if edge_conditions is None:
            edge_conditions = {}

        for child in fwd.get(node_id, []):
            # Simulate node_id as completed, re-evaluate child readiness
            def simulated_status(nid):
                if nid == node_id:
                    return "COMPLETED"
                return status_fn(nid)

            result = ReadinessEngine.evaluate(
                child, bwd, simulated_status,
                edge_conditions=edge_conditions,
            )
            if result.all_and_satisfied:
                count += 1

        return count
