# services/graph_rag_service.py
import json
import collections
from typing import Dict, List, Any, Optional, Tuple, Set

class GraphRAGService:
    """
    Graph-Augmented RAG Service.
    Performs topological graph traversals (BFS/DFS) on project dependency structures
    to provide deterministic upstream root-cause lineage, downstream impact analysis,
    and 'what-if' unblock scenario simulations.
    """

    @classmethod
    def get_graph_rag_context(cls, db_cursor, project_id: int, query: str) -> str:
        """
        Builds a comprehensive Graph-Augmented context block tailored to the user query.
        """
        context_parts = []

        try:
            # 1. Fetch live tracker items & build in-memory graph
            graph_data = cls._build_project_graph(db_cursor, project_id)
            if not graph_data["nodes"]:
                return "No graph dependencies found for this project."

            # 2. Check for 'What-If' simulation intent in the query
            sim_result = cls._try_simulate_what_if(query, graph_data)
            if sim_result:
                context_parts.append(sim_result)

            # 3. Check for specific activity lineage in the query
            lineage_result = cls._find_activity_lineage(query, graph_data)
            if lineage_result:
                context_parts.append(lineage_result)

            # 4. Attach General Graph Topology & Root Cause Summary
            summary_block = cls._build_graph_summary(graph_data)
            if summary_block:
                context_parts.append(summary_block)

        except Exception as e:
            print(f"GraphRAG generation warning: {e}")
            return ""

        return "\n\n".join(context_parts)

    @classmethod
    def _build_project_graph(cls, db_cursor, project_id: int) -> dict:
        """
        Reconstructs the active project dependency graph from MySQL tracker items and milestone dependencies.
        """
        db_cursor.execute("""
            SELECT id, title, item_type, risk_category, risk_level, status,
                   execution_priority_score, risk_score, graph_role, reasoning,
                   recommended_action, canonical_id
            FROM tracker_items
            WHERE project_id = %s
            ORDER BY id ASC
        """, (project_id,))
        rows = db_cursor.fetchall() or []

        nodes: Dict[str, dict] = {}
        title_to_id: Dict[str, str] = {}
        fwd_adj: Dict[str, Set[str]] = collections.defaultdict(set)
        bwd_adj: Dict[str, Set[str]] = collections.defaultdict(set)

        for r in rows:
            nid = str(r["id"] if isinstance(r, dict) else r[0])
            title = r["title"] if isinstance(r, dict) else r[1]
            status = r["status"] if isinstance(r, dict) else r[5]
            role = r.get("graph_role") if isinstance(r, dict) else r[8]
            score = r.get("execution_priority_score") if isinstance(r, dict) else r[6]
            reasoning = r.get("reasoning") if isinstance(r, dict) else r[9]
            action = r.get("recommended_action") if isinstance(r, dict) else r[10]

            node_dict = {
                "id": nid,
                "title": title or f"Item {nid}",
                "status": status,
                "role": role or "ACTIVITY",
                "score": score or 0,
                "action": action or "",
                "reasoning_raw": reasoning or "",
                "blocked_by": [],
                "blocks": []
            }

            # Parse JSON reasoning if available to extract blocked_by & blocks
            if reasoning and isinstance(reasoning, str) and reasoning.strip().startswith("{"):
                try:
                    parsed = json.loads(reasoning)
                    if isinstance(parsed, dict):
                        node_dict["blocked_by"] = parsed.get("blocked_by", [])
                        node_dict["blocks"] = parsed.get("blocks", [])
                        if parsed.get("executive_summary"):
                            node_dict["summary"] = parsed.get("executive_summary")
                except Exception:
                    pass

            nodes[nid] = node_dict
            if title:
                title_to_id[title.lower().strip()] = nid

        # Map edges between nodes using title lookups and fuzzy tokens
        for nid, node in nodes.items():
            # Process blocked_by
            for b_name in node["blocked_by"]:
                b_norm = str(b_name).lower().strip()
                matched_id = cls._match_title(b_norm, title_to_id)
                if matched_id and matched_id != nid:
                    fwd_adj[matched_id].add(nid)
                    bwd_adj[nid].add(matched_id)

            # Process blocks
            for bl_name in node["blocks"]:
                bl_norm = str(bl_name).lower().strip()
                matched_id = cls._match_title(bl_norm, title_to_id)
                if matched_id and matched_id != nid:
                    fwd_adj[nid].add(matched_id)
                    bwd_adj[matched_id].add(nid)

        # Also pull from milestone_dependencies table if present
        try:
            db_cursor.execute("""
                SELECT p.name AS parent_name, c.name AS child_name
                FROM milestone_dependencies md
                JOIN project_milestones p ON md.parent_milestone_id = p.id
                JOIN project_milestones c ON md.child_milestone_id = c.id
                WHERE md.project_id = %s
            """, (project_id,))
            m_deps = db_cursor.fetchall() or []
            for md in m_deps:
                p_name = (md["parent_name"] if isinstance(md, dict) else md[0]).lower().strip()
                c_name = (md["child_name"] if isinstance(md, dict) else md[1]).lower().strip()
                p_id = cls._match_title(p_name, title_to_id)
                c_id = cls._match_title(c_name, title_to_id)
                if p_id and c_id and p_id != c_id:
                    fwd_adj[p_id].add(c_id)
                    bwd_adj[c_id].add(p_id)
        except Exception:
            pass

        return {
            "nodes": nodes,
            "title_to_id": title_to_id,
            "fwd": fwd_adj,
            "bwd": bwd_adj
        }

    @classmethod
    def _match_title(cls, target_norm: str, title_to_id: Dict[str, str]) -> Optional[str]:
        """Fuzzy and substring matching against known node titles."""
        if not target_norm:
            return None
        if target_norm in title_to_id:
            return title_to_id[target_norm]

        # Check containment
        for title_str, nid in title_to_id.items():
            if target_norm in title_str or title_str in target_norm:
                return nid

        # Word overlap
        t_words = set(target_norm.split())
        best_match = None
        best_overlap = 0
        for title_str, nid in title_to_id.items():
            words = set(title_str.split())
            overlap = len(t_words & words)
            if overlap > best_overlap and overlap >= 2:
                best_overlap = overlap
                best_match = nid

        return best_match

    @classmethod
    def _find_activity_lineage(cls, query: str, graph: dict) -> Optional[str]:
        """
        Identifies if an activity is referenced in the query and traces its upstream & downstream path.
        """
        q_lower = query.lower()
        matched_nid = None

        # Find best matching node in query
        for title_str, nid in graph["title_to_id"].items():
            if title_str in q_lower:
                matched_nid = nid
                break

        if not matched_nid:
            # Try keyword search
            for title_str, nid in graph["title_to_id"].items():
                words = [w for w in title_str.split() if len(w) > 3]
                if words and any(w in q_lower for w in words):
                    matched_nid = nid
                    break

        if not matched_nid or matched_nid not in graph["nodes"]:
            return None

        node = graph["nodes"][matched_nid]
        fwd = graph["fwd"]
        bwd = graph["bwd"]

        # 1. Upstream BFS (Prerequisites & Root Causes)
        upstream_visited = set()
        upstream_queue = [matched_nid]
        upstream_chain = []

        while upstream_queue:
            curr = upstream_queue.pop(0)
            for parent in bwd.get(curr, []):
                if parent not in upstream_visited and parent in graph["nodes"]:
                    upstream_visited.add(parent)
                    upstream_queue.append(parent)
                    p_node = graph["nodes"][parent]
                    upstream_chain.append(p_node)

        # 2. Downstream BFS (Downstream Impact)
        downstream_visited = set()
        downstream_queue = [matched_nid]
        downstream_chain = []

        while downstream_queue:
            curr = downstream_queue.pop(0)
            for child in fwd.get(curr, []):
                if child not in downstream_visited and child in graph["nodes"]:
                    downstream_visited.add(child)
                    downstream_queue.append(child)
                    c_node = graph["nodes"][child]
                    downstream_chain.append(c_node)

        lines = [
            f"=== TARGET ACTIVITY DEPENDENCY LINEAGE: '{node['title']}' ===",
            f"• Activity Status: {node['status']} | Graph Role: {node['role']} | Priority Score: {node['score']}"
        ]

        if upstream_chain:
            lines.append("• Upstream Prerequisites / Blockers (Must be resolved first):")
            for idx, up in enumerate(upstream_chain, 1):
                role_tag = f" [{up['role']}]" if up['role'] != 'ACTIVITY' else ""
                lines.append(f"   {idx}. {up['title']} (Status: {up['status']}){role_tag}")
            
            # Root causes among upstream
            root_causes = [up['title'] for up in upstream_chain if up['role'] == 'ROOT_CAUSE' or not bwd.get(up['id'])]
            if root_causes:
                lines.append(f"• Root Cause Blocker(s): {', '.join(root_causes)}")
        else:
            lines.append("• Upstream Prerequisites: None (This activity has no blocking dependencies).")

        if downstream_chain:
            lines.append("• Downstream Impact (Activities waiting on this item):")
            for idx, down in enumerate(downstream_chain, 1):
                lines.append(f"   {idx}. {down['title']} (Status: {down['status']})")
        else:
            lines.append("• Downstream Impact: Leaf activity (No other activities are blocked by this item).")

        return "\n".join(lines)

    @classmethod
    def _try_simulate_what_if(cls, query: str, graph: dict) -> Optional[str]:
        """
        Detects what-if or simulation queries (e.g. 'if X is resolved, will Y unblock?')
        and calculates downstream readiness.
        """
        q_lower = query.lower()
        is_what_if = any(trigger in q_lower for trigger in [
            "if ", "what happens if", "suppose", "assume", "will it unblock", "will it complete", "can we start"
        ])
        if not is_what_if:
            return None

        # Find hypothetical resolved node
        resolved_nid = None
        for title_str, nid in graph["title_to_id"].items():
            if title_str in q_lower:
                resolved_nid = nid
                break

        if not resolved_nid:
            return None

        res_node = graph["nodes"][resolved_nid]
        fwd = graph["fwd"]
        bwd = graph["bwd"]
        direct_children = fwd.get(resolved_nid, set())

        lines = [
            f"=== WHAT-IF UNBLOCK SIMULATION: If '{res_node['title']}' is Resolved ===",
            f"Hypothetical Event: '{res_node['title']}' is marked COMPLETED / PROVIDED."
        ]

        if not direct_children:
            lines.append(f"Outcome: '{res_node['title']}' has no downstream dependents; resolving it does not directly unblock other activities.")
            return "\n".join(lines)

        lines.append("Impact on Downstream Activities:")
        for child_id in direct_children:
            if child_id not in graph["nodes"]:
                continue
            child = graph["nodes"][child_id]
            # Check other prerequisites for this child
            all_prereqs = bwd.get(child_id, set())
            remaining_blockers = [
                graph["nodes"][p]["title"]
                for p in all_prereqs
                if p != resolved_nid and graph["nodes"].get(p, {}).get("status") != "RESOLVED"
            ]

            if not remaining_blockers:
                lines.append(f"  • {child['title']}: FREELY UNBLOCKED -> Ready to begin immediately.")
            else:
                lines.append(f"  • {child['title']}: PARTIALLY UNBLOCKED -> Still blocked by remaining prerequisite(s): {', '.join(remaining_blockers)}")

        return "\n".join(lines)

    @classmethod
    def _build_graph_summary(cls, graph: dict) -> str:
        """
        Builds the summary of active Root Causes and Critical Unblock Order.
        """
        nodes = graph["nodes"]
        fwd = graph["fwd"]
        bwd = graph["bwd"]

        root_causes = [
            n for n in nodes.values()
            if n["status"] != "RESOLVED" and (n["role"] == "ROOT_CAUSE" or (fwd.get(n["id"]) and not bwd.get(n["id"])))
        ]

        if not root_causes:
            return ""

        lines = ["=== ACTIVE ROOT CAUSE BLOCKERS (CRITICAL UNBLOCK QUEUE) ==="]
        for rc in sorted(root_causes, key=lambda x: -x["score"]):
            blocked_count = len(fwd.get(rc["id"], set()))
            lines.append(f"• {rc['title']} | Priority Score: {rc['score']} | Directly Blocks: {blocked_count} activities")
            if rc["action"]:
                lines.append(f"  Action Required: {rc['action']}")

        return "\n".join(lines)
