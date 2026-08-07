import json
from datetime import datetime
from typing import Dict, List, Any, Optional

class TransitionValidator:
    """
    Validates document freshness and legal status transitions.
    """
    ALLOWED_TRANSITIONS = {
        "PENDING": ["IN_PROGRESS", "BLOCKED", "COMPLETED", "PENDING"],
        "IN_PROGRESS": ["BLOCKED", "COMPLETED", "IN_PROGRESS"],
        "BLOCKED": ["IN_PROGRESS", "COMPLETED", "BLOCKED"],
        "COMPLETED": ["COMPLETED"], # Terminal state
        "UNKNOWN": ["PENDING", "IN_PROGRESS", "BLOCKED", "COMPLETED", "UNKNOWN"]
    }

    @classmethod
    def validate_document_order(cls, db_cursor, project_id: int, incoming_document_id: int) -> bool:
        db_cursor.execute("SELECT uploaded_at FROM documents WHERE id = %s", (incoming_document_id,))
        incoming = db_cursor.fetchone()
        if not incoming: return True
        incoming_date = incoming['uploaded_at'] if isinstance(incoming, dict) else incoming[0]

        # Get latest processed document date for this project
        db_cursor.execute("""
            SELECT MAX(d.uploaded_at) as latest 
            FROM tracker_items t 
            JOIN documents d ON t.source_document_id = d.id 
            WHERE t.project_id = %s
        """, (project_id,))
        latest = db_cursor.fetchone()
        if not latest: return True
        
        latest_date = latest['latest'] if isinstance(latest, dict) else latest[0]
        
        if latest_date and incoming_date and incoming_date < latest_date:
            return False # Stale document
        return True

    @classmethod
    def is_valid_transition(cls, current_status: str, incoming_status: str) -> bool:
        current = (current_status or "UNKNOWN").upper()
        incoming = (incoming_status or "UNKNOWN").upper()
        
        if incoming in cls.ALLOWED_TRANSITIONS.get(current, []):
            return True
            
        # Strict terminal enforcement.
        if current == "COMPLETED":
            return False
            
        return False


class MilestoneExecutionStateManager:
    @classmethod
    def update_milestones(cls, db_cursor, project_id: int, incoming_statuses: Dict[int, str]) -> Dict[int, str]:
        """
        Updates project_milestones and returns the final accepted status map.
        """
        final_statuses = {}
        for m_id, new_status in incoming_statuses.items():
            db_cursor.execute("SELECT status FROM project_milestones WHERE id = %s", (m_id,))
            current_row = db_cursor.fetchone()
            if current_row:
                current_status = current_row['status'].upper() if isinstance(current_row, dict) else current_row[0].upper()
                if TransitionValidator.is_valid_transition(current_status, new_status):
                    db_cursor.execute("UPDATE project_milestones SET status = %s WHERE id = %s", (new_status, m_id))
                    final_statuses[m_id] = new_status
                else:
                    final_statuses[m_id] = current_status
        return final_statuses


class ProjectStateSnapshot:
    """
    Immutable runtime view of the project state after all milestone and prerequisite updates.
    """
    def __init__(self, db_cursor, project_id: int):
        self.milestone_statuses = {}
        self.milestone_id_to_name = {}
        self.milestone_dates = {}
        
        db_cursor.execute("SELECT id, name, status, planned_date FROM project_milestones WHERE project_id = %s", (project_id,))
        for r in db_cursor.fetchall():
            m_id = r['id'] if isinstance(r, dict) else r[0]
            name = r['name'] if isinstance(r, dict) else r[1]
            status = r['status'] if isinstance(r, dict) else r[2]
            planned_date = r.get('planned_date') if isinstance(r, dict) else r[3]
            self.milestone_statuses[m_id] = (status or "UNKNOWN").upper().replace(" ", "_")
            self.milestone_id_to_name[m_id] = name
            self.milestone_dates[m_id] = planned_date

    def get_status(self, m_id: int) -> str:
        return self.milestone_statuses.get(m_id, "UNKNOWN").replace(" ", "_")
        
    def get_date(self, m_id: int):
        return self.milestone_dates.get(m_id)


class DependencyExecutionStateResolver:
    @classmethod
    def analyze_static_graph(cls, snapshot: ProjectStateSnapshot, backward_graph: dict) -> dict:
        """
        Determines raw blockages from the static dependency graph.
        """
        results = {}
        
        # Build reverse graph for cascade computation
        forward_graph = {}
        for m, deps in backward_graph.items():
            if m not in forward_graph:
                forward_graph[m] = []
            for dep in deps:
                if dep not in forward_graph:
                    forward_graph[dep] = []
                if m not in forward_graph[dep]:
                    forward_graph[dep].append(m)
                    
        def get_all_downstream(node, visited=None):
            if visited is None:
                visited = set()
            if node in visited:
                return set()
            visited.add(node)
            downstream = set()
            for child in forward_graph.get(node, []):
                downstream.add(child)
                downstream.update(get_all_downstream(child, visited))
            return downstream

        # Compute dependency chain weight (downstream chain length)
        memo_dist = {}
        memo_path = {}
        def get_downstream_chain(node):
            if node in memo_dist:
                return memo_dist[node], memo_path[node]
            children = forward_graph.get(node, [])
            if not children:
                memo_dist[node] = 0
                memo_path[node] = []
                return 0, []
            
            max_dist = -1
            best_path = []
            for c in children:
                c_dist, c_path = get_downstream_chain(c)
                if c_dist > max_dist:
                    max_dist = c_dist
                    best_path = [c] + c_path
                    
            memo_dist[node] = max_dist + 1
            memo_path[node] = best_path
            return memo_dist[node], memo_path[node]

        for node in snapshot.milestone_statuses.keys():
            get_downstream_chain(node)

        # Critical path heuristics (nodes on the absolute longest path)
        max_graph_dist = max(memo_dist.values()) if memo_dist else 0
        critical_nodes = set()
        if max_graph_dist > 0:
            current_nodes = [n for n, d in memo_dist.items() if d == max_graph_dist]
            while current_nodes:
                next_nodes = []
                for n in current_nodes:
                    critical_nodes.add(n)
                    children = forward_graph.get(n, [])
                    if children:
                        max_c_dist = max(memo_dist.get(c, 0) for c in children)
                        for c in children:
                            if memo_dist.get(c, 0) == max_c_dist:
                                next_nodes.append(c)
                current_nodes = next_nodes

        for milestone_id, status in snapshot.milestone_statuses.items():
            downstream_set = get_all_downstream(milestone_id)
            cascade_count = len(downstream_set)
            
            # Root cause heuristic: Is it the EARLIEST actionable blocker?
            earliest_root_cause = False
            if cascade_count > 0 and status not in ["COMPLETED", "RESOLVED"]:
                earliest_root_cause = True
                for dep in backward_graph.get(milestone_id, []):
                    dep_status = snapshot.get_status(dep)
                    # If any predecessor is incomplete, this is a downstream consequence, not the earliest root.
                    if dep_status not in ["COMPLETED", "RESOLVED"]:
                        earliest_root_cause = False
                        break
                        
            # Determine if completing this IMMEDIATELY unlocks downstream work
            distance_to_next_executable = 999
            for child in forward_graph.get(milestone_id, []):
                child_ready = True
                for dep in backward_graph.get(child, []):
                    if dep != milestone_id and snapshot.get_status(dep) not in ["COMPLETED", "RESOLVED"]:
                        child_ready = False
                        break
                if child_ready:
                    distance_to_next_executable = 1
                    break
                        
            # Find next downstream deadline
            earliest_date = None
            next_downstream_name = None
            for child in forward_graph.get(milestone_id, []):
                child_date = snapshot.get_date(child)
                if child_date:
                    from datetime import datetime
                    if isinstance(child_date, str):
                        try:
                            child_date = datetime.strptime(child_date.split(' ')[0], "%Y-%m-%d").date()
                        except:
                            continue
                    elif hasattr(child_date, 'date'):
                        child_date = child_date.date()
                    
                    if not earliest_date or child_date < earliest_date:
                        earliest_date = child_date
                        next_downstream_name = snapshot.milestone_id_to_name.get(child)
                        
            results[milestone_id] = {
                "cascade_count": cascade_count,
                "downstream_milestones": list(downstream_set),
                "earliest_root_cause": earliest_root_cause,
                "distance_to_next_executable": distance_to_next_executable,
                "direct_downstream_milestones": forward_graph.get(milestone_id, []),
                "downstream_chain_length": memo_dist.get(milestone_id, 0),
                "longest_path": [snapshot.milestone_id_to_name.get(n, str(n)) for n in memo_path.get(milestone_id, []) if snapshot.milestone_id_to_name.get(n)],
                "critical_path": milestone_id in critical_nodes,
                "next_downstream_date": earliest_date,
                "next_downstream_name": next_downstream_name
            }
            
        return results


class DerivedExecutionState:
    @classmethod
    def compute_derived_status(cls, snapshot: ProjectStateSnapshot, backward_graph: dict) -> dict:
        """
        Deterministically computes downstream readiness using a 4-state model:

            READY   — all predecessors COMPLETED. Milestone can start now.
            WAITING — some predecessors still IN_PROGRESS but none are BLOCKED/DELAYED.
                      Normal project flow. NOT an execution risk.
            BLOCKED — at least one predecessor is explicitly BLOCKED or DELAYED.
                      This is an active execution risk.
            DELAYED — the milestone itself has missed its own planned date
                      (date-check is done in the scoring layer, not here).

        The critical enterprise PM distinction:
            Waiting ≠ Blocked.
            A WAITING task is on-track. A BLOCKED task has an active impediment.
        """
        derived_statuses = {}

        for m_id, current_status in snapshot.milestone_statuses.items():
            # Already completed — structural dependency is satisfied, not a risk.
            if current_status == "COMPLETED":
                derived_statuses[m_id] = {"status": "COMPLETED", "blockers": []}
                continue

            predecessors = backward_graph.get(m_id, [])

            if not predecessors:
                # No dependencies — milestone state is its own raw state.
                derived_statuses[m_id] = {"status": current_status, "blockers": []}
                continue

            # Classify each predecessor's contribution
            incomplete_predecessors = []   # predecessors not yet done
            at_risk_predecessors = []      # predecessors that are BLOCKED or DELAYED

            for dep_id in predecessors:
                dep_status = snapshot.get_status(dep_id)
                if dep_status not in ["COMPLETED", "RESOLVED"]:
                    name = snapshot.milestone_id_to_name.get(dep_id, str(dep_id))
                    incomplete_predecessors.append(name)
                    if dep_status in ["BLOCKED", "DELAYED", "NOT_STARTED"]:
                        at_risk_predecessors.append(name)

            if not incomplete_predecessors:
                # All predecessors done — milestone is READY (or its own status if in-progress)
                derived_statuses[m_id] = {"status": current_status, "blockers": []}
            elif at_risk_predecessors:
                # At least one predecessor is actively blocked/delayed → this milestone is BLOCKED
                derived_statuses[m_id] = {"status": "BLOCKED", "blockers": at_risk_predecessors}
            else:
                # Predecessors exist but are just IN_PROGRESS → milestone is WAITING (not a risk)
                derived_statuses[m_id] = {"status": "WAITING", "blockers": incomplete_predecessors}

        return derived_statuses

