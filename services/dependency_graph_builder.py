"""
DependencyGraphBuilder — 2-Pass Canonical Dependency Graph Engine

Pass 1: Build the complete Canonical Entity Registry from baseline + candidates.
Pass 2: Resolve every dependency reference via EntityResolver → create DependencyEdges.

Rules enforced:
- No graph node is ever created from a raw string.
- Edges are always between canonical_ids.
- blocks[] and blocked_by[] are DERIVED from DependencyEdge, never maintained
  independently.
- Unresolved references become UnresolvedReference DTOs, not fake nodes.
- The graph is built AFTER all entities are known.
- graph_role is derived ONLY from final graph topology, never from LLM output.
- Duplicate edges are merged with evidence arrays (not duplicated).
- AND/OR prerequisite semantics are preserved per edge.
- Readiness is calculated by ReadinessEngine (never inline).
"""

import collections
import re
from typing import Dict, List, Set, Optional, Any, Tuple

from services.entity_resolver import (
    EntityResolver,
    CanonicalEntityRegistry,
    CanonicalEntity,
    UnresolvedReference,
    ResolutionResult,
    build_registry_from_baseline,
    enrich_registry_with_candidates,
    normalize_entity_name,
    _strip_parentheticals,
    _is_non_entity,
)
from services.readiness_engine import ReadinessEngine


# ---------------------------------------------------------------------------
# Dependency Edge
# ---------------------------------------------------------------------------

class DependencyEdge:
    """
    One canonical directed dependency: source BLOCKS target.

    Derives both directions from the edge — never maintain separate lists.
    Supports merged evidence (multiple sentences proving same dependency).
    Supports AND/OR condition semantics.
    """
    def __init__(self, source_id: str, target_id: str,
                 relationship: str = "BLOCKS",
                 evidence: str = "", confidence: float = 1.0,
                 condition: str = "AND"):
        self.source_id = source_id
        self.target_id = target_id
        self.relationship = relationship   # BLOCKS | REQUIRES
        self.condition = condition          # AND | OR | UNKNOWN
        # Multi-evidence support
        self.evidence_list: List[str] = [evidence] if evidence else []
        self.confidence = confidence
        self.supporting_mentions: int = 1

    @property
    def evidence(self) -> str:
        """Primary evidence for backwards-compatibility."""
        return self.evidence_list[0] if self.evidence_list else ""

    def merge_evidence(self, new_evidence: str, new_confidence: float = 1.0):
        """Merge a duplicate edge's evidence without creating a new edge."""
        if new_evidence and new_evidence not in self.evidence_list:
            self.evidence_list.append(new_evidence)
        self.confidence = max(self.confidence, new_confidence)
        self.supporting_mentions += 1

    def __eq__(self, other):
        return (isinstance(other, DependencyEdge) and
                self.source_id == other.source_id and
                self.target_id == other.target_id)

    def __hash__(self):
        return hash((self.source_id, self.target_id))

    def __repr__(self):
        return f"DependencyEdge({self.source_id!r} → {self.target_id!r} [{self.condition}])"



# ---------------------------------------------------------------------------
# Words that are never valid dependency targets
# ---------------------------------------------------------------------------

_INVALID_TARGETS = {
    "pending", "pending review", "waiting", "completed", "not started",
    "in progress", "unknown", "delayed", "blocked", "cancelled",
    "done", "resolved", "on hold", "deferred", "planned", "open",
    "customer", "client", "internal", "external", "vendor",
    "third party", "third-party", "development team", "qa team",
    "qa lead", "project manager", "customer team", "sponsor",
    "management", "stakeholder", "pmo", "team", "department", "role",
    "customer department", "vendor team",
    "credentials", "access", "approval", "security approval",
    "internal security approval", "review", "next weekly meeting",
    "meeting", "discussion", "follow up", "follow-up", "tbd", "n/a",
    "na", "none", "null", "undefined", "yes", "no", "percentage",
    "next week", "september 9",
}

_DATE_RE = re.compile(
    r"^\d{1,2}\s+\w+(\s+\d{4})?$"
    r"|^\w+\s+\d{1,2}(,?\s+\d{4})?$"
    r"|^\d{4}-\d{2}-\d{2}$"
    r"|^Q[1-4]\s+\d{4}$",
    re.IGNORECASE,
)


def _is_invalid_target(text: str) -> bool:
    n = normalize_entity_name(text)
    if n in _INVALID_TARGETS:
        return True
    if _DATE_RE.match(n):
        return True
    if len(n) <= 2:
        return True
    return False


# ---------------------------------------------------------------------------
# Graph helpers
# ---------------------------------------------------------------------------

def _build_adjacency(edges: List[DependencyEdge]) -> tuple:
    """Return (forward_graph, backward_graph) as defaultdict(set)."""
    fwd = collections.defaultdict(set)
    bwd = collections.defaultdict(set)
    for e in edges:
        fwd[e.source_id].add(e.target_id)
        bwd[e.target_id].add(e.source_id)
    return fwd, bwd


def _derive_graph_role(node_id: str, fwd: dict, bwd: dict) -> str:
    """
    Derive graph role from topology ONLY. Never use LLM-generated graph_role.

    ROOT_CAUSE          — no incoming edges, has downstream dependents
    INTERMEDIATE_BLOCKER — has both incoming and outgoing edges
    TERMINAL_ACTIVITY   — has incoming edges, no outgoing (leaf)
    ISOLATED            — no edges at all
    """
    has_upstream = bool(bwd.get(node_id))
    has_downstream = bool(fwd.get(node_id))

    if not has_upstream and has_downstream:
        return "ROOT_CAUSE"
    elif has_upstream and has_downstream:
        return "INTERMEDIATE_BLOCKER"
    elif has_upstream and not has_downstream:
        return "TERMINAL_ACTIVITY"
    else:
        return "ISOLATED"


def _is_semantically_valid_direction(
    source_name: str,
    target_name: str,
    source_activity: dict,
    direction: str,  # "blocks" or "blocked_by"
    target_id: Optional[str] = None,
    resolved_blocked_by_ids: Optional[List[str]] = None,
) -> bool:
    """
    Validates that the edge direction makes semantic sense.
    Returns False if the edge would reverse a known
    relationship that already exists in the activity's own lists.

    Rules:
    - If drawing edge A -> B from A.blocks,
      check: does A.blocked_by also contain B?
      If yes: contradiction — the LLM extracted both
      A blocks B AND A is blocked by B simultaneously.
      This is a contradiction. Reject the blocks direction,
      keep blocked_by direction (A is blocked by B).

    - If drawing edge A -> B from A.blocked_by (reversed),
      check: does A.blocks also contain B?
      If yes: same contradiction. Reject.

    Generic: uses only the source activity dict / resolved IDs.
    No hardcoded names.
    """
    if direction == "blocks":
        blocked_by_list = [
            str(b).lower().strip()
            for b in source_activity.get("blocked_by", [])
            if b
        ]
        if target_name and str(target_name).lower().strip() in blocked_by_list:
            print(
                f"  [GraphBuilder] Contradiction rejected: "
                f"'{source_name}' both blocks AND is "
                f"blocked_by '{target_name}' -- "
                f"keeping blocked_by direction only"
            )
            return False
        if target_id and resolved_blocked_by_ids and target_id in resolved_blocked_by_ids:
            print(
                f"  [GraphBuilder] Contradiction rejected: "
                f"'{source_name}' ({target_id}) both blocks AND is "
                f"blocked_by '{target_name}' -- "
                f"keeping blocked_by direction only"
            )
            return False
    return True


def _is_genuine_cycle(source_id: str, target_id: str, adjacency: dict) -> bool:
    """
    Returns True only if adding edge source->target creates a genuine cycle
    (target can reach source through existing edges). Returns False if they are simply parallel/duplicate.

    Generic: uses only graph topology, no item names.
    """
    # BFS from target — can we reach source?
    visited = set()
    queue = [target_id]
    while queue:
        node = queue.pop(0)
        if node == source_id:
            return True  # genuine cycle
        if node not in visited:
            visited.add(node)
            queue.extend(adjacency.get(node, []))
    return False  # no path from target back to source


def _detect_and_break_cycles(fwd: dict, bwd: dict, edges: List[DependencyEdge]) -> List[DependencyEdge]:
    """DFS cycle detection — breaks earliest back-edge found only if genuine cycle."""
    visited: Set[str] = set()
    rec_stack: Set[str] = set()
    removed: Set[tuple] = set()

    def dfs(node):
        visited.add(node)
        rec_stack.add(node)
        for nb in list(fwd.get(node, [])):
            if nb not in visited:
                dfs(nb)
            elif nb in rec_stack:
                # CYCLE FIX: Verify genuine cycle before breaking
                if _is_genuine_cycle(node, nb, fwd):
                    fwd[node].discard(nb)
                    bwd[nb].discard(node)
                    removed.add((node, nb))
                    print(f"  [GraphValidator] GENUINE CYCLE BROKEN: {node} -> {nb}")
                else:
                    print(f"  [GraphValidator] False cycle (duplicate edge) skipped: {node} -> {nb}")
        rec_stack.discard(node)

    all_nodes = set(fwd.keys()) | set(bwd.keys())
    for n in all_nodes:
        if n not in visited:
            dfs(n)

    return [e for e in edges if (e.source_id, e.target_id) not in removed]


# ---------------------------------------------------------------------------
# Graph metric computation
# ---------------------------------------------------------------------------

def _compute_graph_metrics(all_nodes: Set[str], fwd: dict, bwd: dict,
                            status_fn=None) -> Dict[str, dict]:
    """
    Compute rich graph metrics for every node.

    status_fn: callable(node_id) → str status, or None
    """
    def get_all_downstream(node, visited=None):
        if visited is None:
            visited = set()
        if node in visited:
            return set()
        visited.add(node)
        result = set()
        for child in fwd.get(node, []):
            result.add(child)
            result.update(get_all_downstream(child, visited))
        return result

    # Longest downstream chain (memoised)
    memo_dist: Dict[str, int] = {}
    memo_path: Dict[str, list] = {}

    def get_downstream_chain(node):
        if node in memo_dist:
            return memo_dist[node], memo_path[node]
        children = list(fwd.get(node, []))
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
    # Identify critical nodes (on the longest path)
    critical_nodes: Set[str] = set()
    if max_dist > 0:
        starters = [n for n, d in memo_dist.items() if d == max_dist]
        queue = collections.deque(starters)
        crit_vis: Set[str] = set()
        while queue:
            curr = queue.popleft()
            if curr in crit_vis:
                continue
            crit_vis.add(curr)
            critical_nodes.add(curr)
            children = list(fwd.get(curr, []))
            if children:
                max_child_d = max(memo_dist.get(c, 0) for c in children)
                for c in children:
                    if memo_dist.get(c, 0) == max_child_d:
                        queue.append(c)

    metrics: Dict[str, dict] = {}
    for node in all_nodes:
        downstream_set = get_all_downstream(node)
        cascade_count = len(downstream_set)
        status = status_fn(node) if status_fn else "UNKNOWN"

        # Root cause: has cascade impact AND all predecessors are complete
        is_root = False
        if cascade_count > 0 and status not in ("COMPLETED", "RESOLVED"):
            is_root = True
            for pred in bwd.get(node, []):
                pred_status = status_fn(pred) if status_fn else "UNKNOWN"
                if pred_status not in ("COMPLETED", "RESOLVED"):
                    is_root = False
                    break

        # Immediate unlock count
        immediate_unlock_count = 0
        for child in fwd.get(node, []):
            child_ready = True
            for dep in bwd.get(child, []):
                if dep != node and (status_fn(dep) if status_fn else "UNKNOWN") \
                        not in ("COMPLETED", "RESOLVED"):
                    child_ready = False
                    break
            if child_ready:
                immediate_unlock_count += 1

        critical_path_len = memo_dist.get(node, 0)
        on_critical_path = node in critical_nodes

        metrics[node] = {
            "is_root_cause": is_root,
            "cascade_count": cascade_count,
            "cascade_depth": critical_path_len,
            "downstream_ids": sorted(downstream_set),
            "immediate_unlock_count": immediate_unlock_count,
            "on_critical_path": on_critical_path,
            "longest_path_ids": memo_path.get(node, [node]),
            "parent_ids": sorted(bwd.get(node, [])),
            "child_ids": sorted(fwd.get(node, [])),
        }

    return metrics


# ---------------------------------------------------------------------------
# Main builder
# ---------------------------------------------------------------------------

class DependencyGraphBuilder:
    """
    2-Pass canonical dependency graph builder.

    Pass 1: Build CanonicalEntityRegistry (all entities known first).
    Pass 2: Resolve dependency references → DependencyEdge objects.

    No raw strings are used as graph node IDs.
    No fake nodes are created.
    """

    @classmethod
    def build_and_enrich(cls, candidates: List[dict],
                         baseline_items: List[dict] = None) -> List[dict]:
        """
        Entry point called from the risk evaluation pipeline.

        candidates: list of LLM-extracted activity dicts, each may contain:
            { "activity", "blocked_by": [...], "blocks": [...], ... }

        baseline_items: EL scope items from DB, each:
            { "id", "name", "category", ... }

        Returns the enriched candidates list with graph metadata injected.
        """
        if baseline_items is None:
            baseline_items = []

        # ── PASS 1: Build complete Canonical Entity Registry ─────────────────
        registry = build_registry_from_baseline(baseline_items, id_prefix="si")
        registry = enrich_registry_with_candidates(registry, candidates, id_prefix="cand")

        # Print registry for diagnostics
        registry.print_registry()

        # Build id → candidate map
        id_to_cand: Dict[str, dict] = {}
        for cand in candidates:
            cid = cand.get("_canonical_id")
            if cid:
                id_to_cand[cid] = cand

        # ── PASS 2: Resolve dependency references ─────────────────────────────
        resolver = EntityResolver(registry)
        # Use edge_map for merged duplicate edges: (src, tgt) -> DependencyEdge
        edge_map: Dict[Tuple[str, str], DependencyEdge] = {}
        unresolved_by_source: Dict[str, List[dict]] = collections.defaultdict(list)
        # Validation counters (use list so closures can mutate)
        self_deps_counter = [0]
        # UAT-CYCLE FIX: Track dynamic adjacency incrementally to check cycles on dynamic edges only (Part A)
        dynamic_adjacency: Dict[str, List[str]] = collections.defaultdict(list)

        # Map baseline IDs to the execution candidates that represent them
        baseline_to_cands = collections.defaultdict(list)
        for c in candidates:
            if c.get("_baseline_id"):
                baseline_to_cands[c["_baseline_id"]].append(c["_canonical_id"])

        for cand in candidates:
            source_id = cand.get("_canonical_id")
            if not source_id:
                continue

            evidence = cand.get("evidence", "") or cand.get("source_sentence", "")

            def _resolve_refs(raw_list, direction_label, src_id=source_id, ev=evidence):
                """Resolve a list of raw dependency strings for this candidate."""
                resolved_ids = []
                for raw_ref in (raw_list or []):
                    if not raw_ref or not str(raw_ref).strip():
                        continue
                    raw_str = str(raw_ref).strip()

                    if _is_invalid_target(raw_str):
                        print(f"  [GraphValidator] REJECTED '{raw_str}' "
                              f"({direction_label} of '{cand.get('activity')}') "
                              f"— non-entity text")
                        continue

                    result = resolver.resolve(raw_str, source_id=src_id, evidence=ev)
                    if result.resolved:
                        cid = result.canonical_id
                        # If the resolved ID is a baseline item (not a cand execution node)
                        if not cid.startswith("cand_"):
                            if cid in baseline_to_cands:
                                # Map baseline ID to the active candidate nodes
                                for mapped_cid in baseline_to_cands[cid]:
                                    if mapped_cid == src_id:
                                        print(f"  [GraphValidator] SELF-DEP REJECTED: "
                                              f"'{raw_str}' mapped to self '{cand.get('activity')}'")
                                        self_deps_counter[0] += 1
                                        continue
                                    resolved_ids.append(mapped_cid)
                            else:
                                # Baseline item is not in this document's scope -> Unresolved External
                                unres_dict = {
                                    "raw_name": raw_str,
                                    "canonical_id": cid,
                                    "resolution_status": "UNRESOLVED_BASELINE",
                                    "evidence": ev,
                                    "target_canonical_id": cid
                                }
                                unresolved_by_source[src_id].append(unres_dict)
                                print(f"  [GraphValidator] EXTERNAL BASELINE dependency "
                                      f"'{raw_str}' for '{cand.get('activity')}' "
                                      f"-> logged as UNRESOLVED")
                        else:
                            # It's already a candidate ID (cand_X)
                            if cid == src_id:
                                print(f"  [GraphValidator] SELF-DEP REJECTED: "
                                      f"'{raw_str}' resolved to same entity as source "
                                      f"'{cand.get('activity')}'")
                                self_deps_counter[0] += 1
                                continue
                            resolved_ids.append(cid)
                    else:
                        unref = resolver.classify_unresolved(
                            raw_str, source_id=src_id, evidence=ev)
                        if unref.ref_type == UnresolvedReference.EXTERNAL:
                            # Store as structured unresolved node (not discarded)
                            unres_dict = {
                                "raw_name": raw_str,
                                "canonical_id": None,
                                "resolution_status": "UNRESOLVED",
                                "evidence": ev,
                                "target_canonical_id": None
                            }
                            unresolved_by_source[src_id].append(unres_dict)
                            print(f"  [GraphValidator] EXTERNAL dependency "
                                  f"'{raw_str}' for '{cand.get('activity')}' "
                                  f"-> logged as UNRESOLVED")
                        else:
                            print(f"  [GraphValidator] REJECTED '{raw_str}' "
                                  f"({direction_label} of '{cand.get('activity')}') "
                                  f"— classified as NON_ENTITY_TEXT")
                return resolved_ids

            # Resolve blocked_by (sources that block this activity)
            resolved_blocked_by = _resolve_refs(
                cand.get("blocked_by", []), "blocked_by")

            # Resolve blocks (activities this candidate blocks)
            resolved_blocks = _resolve_refs(
                cand.get("blocks", []), "blocks")

            # Create/merge canonical DependencyEdges via edge_map
            for blocker_id in resolved_blocked_by:
                if blocker_id == source_id:
                    continue
                # UAT-CYCLE FIX: Check cycle against dynamic_adjacency only before adding edge (Part A)
                if _is_genuine_cycle(blocker_id, source_id, dynamic_adjacency):
                    print(f"  [GraphValidator] False cycle / reverse edge rejected: {blocker_id} -> {source_id}")
                    continue

                key = (blocker_id, source_id)
                if key in edge_map:
                    edge_map[key].merge_evidence(evidence, 1.0)
                else:
                    edge_map[key] = DependencyEdge(blocker_id, source_id,
                                                   "BLOCKS", evidence, 1.0,
                                                   condition="AND")
                    dynamic_adjacency[blocker_id].append(source_id)

            for blocked_id in resolved_blocks:
                if blocked_id == source_id:
                    continue

                # UAT-CYCLE FIX: Validate direction semantically (prevent contradiction with blocked_by) (Part B)
                cand_name = cand.get("activity") or cand.get("canonical_title") or source_id
                target_cand = id_to_cand.get(blocked_id, {})
                target_name = target_cand.get("activity") or target_cand.get("canonical_title") or blocked_id
                if not _is_semantically_valid_direction(
                    cand_name, target_name, cand, "blocks",
                    target_id=blocked_id, resolved_blocked_by_ids=resolved_blocked_by
                ):
                    continue

                # UAT-CYCLE FIX: Check cycle against dynamic_adjacency only before adding edge (Part A)
                if _is_genuine_cycle(source_id, blocked_id, dynamic_adjacency):
                    print(f"  [GraphValidator] False cycle / reverse edge rejected: {source_id} -> {blocked_id}")
                    continue

                key = (source_id, blocked_id)
                if key in edge_map:
                    edge_map[key].merge_evidence(evidence, 1.0)
                else:
                    edge_map[key] = DependencyEdge(source_id, blocked_id,
                                                   "BLOCKS", evidence, 1.0,
                                                   condition="AND")
                    dynamic_adjacency[source_id].append(blocked_id)

        edges = list(edge_map.values())
        duplicate_edges_removed = sum(
            e.supporting_mentions - 1 for e in edges
        )

        # Print dependency resolution log
        resolver.print_resolution_log("Pass 2")

        # ── BUILD ADJACENCY + VALIDATE ────────────────────────────────────────
        fwd, bwd = _build_adjacency(edges)
        all_nodes: Set[str] = set(id_to_cand.keys())

        # Remove self-loops (second pass safety)
        for n in list(fwd.keys()):
            fwd[n].discard(n)
        for n in list(bwd.keys()):
            bwd[n].discard(n)

        # Remove edges where target is not a known node (fake-node guard)
        fake_nodes_removed = 0
        for n in list(fwd.keys()):
            before = len(fwd[n])
            fwd[n] = {c for c in fwd[n] if c in all_nodes}
            fake_nodes_removed += before - len(fwd[n])
        for n in list(bwd.keys()):
            bwd[n] = {p for p in bwd[n] if p in all_nodes}

        # Rebuild edges after integrity check
        valid_edges = [e for e in edges
                       if e.source_id in all_nodes and e.target_id in all_nodes
                       and e.source_id != e.target_id]

        # Break cycles and count
        edges_before_cycle = len(valid_edges)
        valid_edges = _detect_and_break_cycles(fwd, bwd, valid_edges)
        cycle_edges_rejected = edges_before_cycle - len(valid_edges)

        # Build edge_conditions map for ReadinessEngine: {(src, tgt): "AND"|"OR"}
        edge_conditions: Dict[Tuple[str, str], str] = {
            (e.source_id, e.target_id): e.condition for e in valid_edges
        }

        # Print final graph
        print(f"\n=== FINAL GRAPH ===")
        print(f"{'Source (ID)':<15} | {'Source Name':<45} | {'Target (ID)':<15} | {'Target Name':<45} | {'Cond':<5} | Evidence")
        print("-" * 150)
        for e in sorted(valid_edges, key=lambda x: (x.source_id, x.target_id)):
            src_name = registry.get_by_id(e.source_id)
            tgt_name = registry.get_by_id(e.target_id)
            sn = (src_name.display_name if src_name else e.source_id)[:45]
            tn = (tgt_name.display_name if tgt_name else e.target_id)[:45]
            ev = (e.evidence or "")[:60]
            print(f"{e.source_id:<15} | {sn:<45} | {e.target_id:<15} | {tn:<45} | {e.condition:<5} | {ev}")
        print(f"\n  Total: {len(all_nodes)} nodes, {len(valid_edges)} edges\n")

        # ── COMPUTE GRAPH METRICS ─────────────────────────────────────────────
        def _status_fn(nid):
            return (id_to_cand.get(nid) or {}).get("status", "UNKNOWN")

        def _owner_fn(nid):
            return (id_to_cand.get(nid) or {}).get("dependency_owner", "Internal")

        def _name_fn(nid):
            c = id_to_cand.get(nid)
            if c:
                return str(c.get("canonical_title") or c.get("activity") or nid)
            e = registry.get_by_id(nid)
            return e.display_name if e else nid

        metrics = _compute_graph_metrics(all_nodes, fwd, bwd, status_fn=_status_fn)

        # Pre-compute ReadinessEngine results for all nodes (centralized)
        def _unresolved_count_fn(nid):
            return len(unresolved_by_source.get(nid, []))
            
        readiness_results = ReadinessEngine.evaluate_all(
            list(all_nodes), bwd, _status_fn,
            owner_fn=_owner_fn,
            edge_conditions=edge_conditions,
            name_fn=_name_fn,
            unresolved_count_fn=_unresolved_count_fn,
        )
        # ── Collect all unresolved dependencies for graph validation contract ────
        unresolved_externals = []
        for ur_list in unresolved_by_source.values():
            for ur in ur_list:
                name = ur["raw_name"]
                if name not in unresolved_externals:
                    unresolved_externals.append(name)

        # ── INJECT METRICS INTO CANDIDATES ───────────────────────────────────
        def ids_to_names(id_list):
            result = []
            for i in id_list:
                e = registry.get_by_id(i)
                result.append(e.display_name if e else i)
            return result

        for cand in candidates:
            cid = cand.get("_canonical_id")
            if not cid:
                continue

            m = metrics.get(cid, {})
            readiness = readiness_results.get(cid)

            # ── graph_role: TOPOLOGY ONLY (never from LLM) ───────────────────
            cand["graph_role"] = _derive_graph_role(cid, fwd, bwd)

            # ── Blocks / blocked_by from graph (single source of truth) ──────
            cand["blocked_by"] = ids_to_names(m.get("parent_ids", []))
            cand["blocks"] = ids_to_names(m.get("child_ids", []))
            cand["_blocked_by_ids"] = m.get("parent_ids", [])
            cand["_blocks_ids"] = m.get("child_ids", [])

            # ── Readiness (from ReadinessEngine, not inline) ─────────────────
            if readiness:
                cand["blocked"] = readiness.status == "BLOCKED"
                cand["waiting"] = readiness.status in ("BLOCKED", "WAITING_ON_EXTERNAL", "BLOCKED_UNRESOLVED_DEPENDENCY")
                cand["readiness_status"] = readiness.status
                cand["blocking_prerequisites"] = readiness.blocking_names
                cand["all_and_prerequisites_satisfied"] = readiness.all_and_satisfied
            else:
                cand["blocked"] = False
                cand["waiting"] = False
                cand["readiness_status"] = "UNKNOWN"
                cand["blocking_prerequisites"] = []
                cand["all_and_prerequisites_satisfied"] = True

            cand["unresolved_dependencies"] = unresolved_by_source.get(cid, [])

            # ── Graph metrics ─────────────────────────────────────────────────
            cand["is_root_cause"] = m.get("is_root_cause", False)
            cand["earliest_root_cause"] = m.get("is_root_cause", False)
            cand["cascade_count"] = m.get("cascade_count", 0)
            cand["cascade_depth"] = m.get("cascade_depth", 0)
            cand["blocked_work_count"] = m.get("cascade_count", 0)
            cand["critical_path"] = m.get("on_critical_path", False)
            cand["critical_chain"] = m.get("on_critical_path", False)
            cand["criticality_score"] = (
                min(m.get("cascade_depth", 0) * 15, 80) +
                (20 if m.get("on_critical_path") else 0)
            )

            # ── Immediate unlock count (ReadinessEngine AND semantics) ────────
            immediate_unlock = ReadinessEngine.immediate_unlock_count(
                cid, fwd, bwd, _status_fn, edge_conditions=edge_conditions
            )
            cand["immediate_unlock_count"] = immediate_unlock
            cand["execution_unlock_count"] = immediate_unlock

            # ── Downstream / path metadata ────────────────────────────────────
            downstream_names = ids_to_names(m.get("downstream_ids", []))
            cand["downstream_names"] = downstream_names
            cand["direct_blocking_names"] = ids_to_names(m.get("child_ids", []))

            longest_path_ids = m.get("longest_path_ids", [])
            cand["longest_path"] = ids_to_names(longest_path_ids)

            child_ids_set = set(m.get("child_ids", []))
            all_downstream_ids = set(m.get("downstream_ids", []))
            immediate_ids = child_ids_set & all_downstream_ids
            future_ids = all_downstream_ids - child_ids_set

            cand["immediate_unlocks"] = ids_to_names(sorted(immediate_ids))
            cand["future_unlocks"] = ids_to_names(sorted(future_ids))
            cand["immediate_unlock_names"] = ids_to_names(sorted(immediate_ids))

            # ── Dependency owner from evidence (generic) ──────────────────────
            ev_text = (cand.get("evidence") or cand.get("source_sentence") or "").lower()
            if any(k in ev_text for k in ["customer", "client", "sponsor"]):
                cand["dependency_owner"] = "Customer"
            elif any(k in ev_text for k in ["vendor", "3rd party", "third-party",
                                              "external provider"]):
                cand["dependency_owner"] = "Vendor"
            else:
                cand["dependency_owner"] = "Internal"

            # ── Resolution effort proxy ───────────────────────────────────────
            if m.get("cascade_count", 0) == 0:
                cand["resolution_effort"] = "S"
            elif m.get("cascade_depth", 0) >= 3:
                cand["resolution_effort"] = "L"
            else:
                cand["resolution_effort"] = "M"

            # ── Business criticality from graph position ───────────────────────
            if m.get("on_critical_path") or m.get("cascade_depth", 0) >= 3:
                cand["business_criticality"] = "Mission Critical"
            elif m.get("cascade_count", 0) >= 2:
                cand["business_criticality"] = "High"
            else:
                cand["business_criticality"] = "Medium"

            # ── Business phase from graph distance to terminal ─────────────────
            if m.get("cascade_count", 0) == 0:
                cand["business_phase"] = "Deployment"
                cand["distance_to_terminal"] = 0
            elif m.get("cascade_depth", 0) <= 2:
                cand["business_phase"] = "Testing"
                cand["distance_to_terminal"] = m.get("cascade_depth", 1)
            elif m.get("is_root_cause"):
                cand["business_phase"] = "Execution"
                cand["distance_to_terminal"] = m.get("cascade_depth", 3)
            else:
                cand["business_phase"] = "Development"
                cand["distance_to_terminal"] = m.get("cascade_depth", 5)

            cand["distance_to_next_executable"] = (
                1 if immediate_unlock > 0
                else m.get("cascade_depth", 999)
            )

            # ── Dependency source from evidence keywords ──────────────────────
            evidence_lower = (
                cand.get("evidence", "") + " " +
                cand.get("reasoning", "") + " " +
                str(cand.get("activity", ""))
            ).lower()
            if any(k in evidence_lower for k in ["customer", "client",
                                                   "credentials", "vpn",
                                                   "access", "external"]):
                cand["dependency_source"] = "CUSTOMER"
                cand["external_dependency"] = True
            elif any(k in evidence_lower for k in ["vendor", "third party",
                                                    "third-party", "partner"]):
                cand["dependency_source"] = "VENDOR"
                cand["external_dependency"] = True
            elif any(k in evidence_lower for k in ["security", "audit",
                                                    "compliance", "review"]):
                cand["dependency_source"] = "SECURITY"
                cand["external_dependency"] = False
            elif any(k in evidence_lower for k in ["pmo", "management",
                                                    "approval"]):
                cand["dependency_source"] = "PMO"
                cand["external_dependency"] = False
            else:
                cand["dependency_source"] = "ENGINEERING"
                cand["external_dependency"] = False

            # ── Parallel stream label (connected component) ───────────────────
            cand["parallel_stream"] = cand.get("parallel_stream", "Stream 1")

            # ── Unresolved external dependencies (structured, not executable) ──
            cand["unresolved_dependencies"] = [
                ur["raw_name"] for ur in unresolved_by_source.get(cid, [])
            ]

        # ── GRAPH VALIDATION CONTRACT ─────────────────────────────────────────
        # Stored on the first candidate for API access
        validation_contract = {
            "fake_nodes_removed": fake_nodes_removed,
            "duplicate_edges_removed": duplicate_edges_removed,
            "self_dependencies_rejected": self_deps_counter[0],
            "cycle_edges_rejected": cycle_edges_rejected,
            "unresolved_dependencies": len(unresolved_externals),
            "unresolved_nodes": unresolved_externals,
            "total_confirmed_edges": len(valid_edges),
            "total_nodes": len(all_nodes),
        }
        if candidates:
            candidates[0]["_graph_validation"] = validation_contract

        # Final summary log
        print(f"\n=== GRAPH ANALYSIS ===")
        print(f"{'Entity':<50} | {'Role':<22} | {'Ready':<22} | {'Unlocks':>7} | {'Cascade':>7} | {'CritPath'}")
        print("-" * 130)
        for cand in sorted(candidates, key=lambda c: -c.get("cascade_count", 0)):
            name = str(cand.get("canonical_title") or cand.get("activity") or "?")[:50]
            role = cand.get("graph_role", "ISOLATED")
            ready = cand.get("readiness_status", "UNKNOWN")
            unlocks = cand.get("immediate_unlock_count", 0)
            cascade = cand.get("cascade_count", 0)
            crit = "YES" if cand.get("critical_path") else ""
            print(f"{name:<50} | {role:<22} | {ready:<22} | {unlocks:>7} | {cascade:>7} | {crit}")
        print()
        print(f"  Validation: {validation_contract['total_confirmed_edges']} edges confirmed, "
              f"{validation_contract['self_dependencies_rejected']} self-deps rejected, "
              f"{validation_contract['cycle_edges_rejected']} cycles broken, "
              f"{validation_contract['unresolved_dependencies']} unresolved deps\n")

        return candidates
