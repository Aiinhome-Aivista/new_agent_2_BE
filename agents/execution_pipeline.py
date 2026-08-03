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
        
        db_cursor.execute("SELECT id, name, status FROM project_milestones WHERE project_id = %s", (project_id,))
        for r in db_cursor.fetchall():
            m_id = r['id'] if isinstance(r, dict) else r[0]
            name = r['name'] if isinstance(r, dict) else r[1]
            status = r['status'] if isinstance(r, dict) else r[2]
            self.milestone_statuses[m_id] = (status or "UNKNOWN").upper()
            self.milestone_id_to_name[m_id] = name

    def get_status(self, m_id: int) -> str:
        return self.milestone_statuses.get(m_id, "UNKNOWN")


class DependencyExecutionStateResolver:
    @classmethod
    def analyze_static_graph(cls, snapshot: ProjectStateSnapshot, backward_graph: dict) -> dict:
        """
        Determines raw blockages from the static dependency graph.
        """
        results = {}
        
        # Build reverse graph for cascade computation
        reverse_graph = {}
        for m, deps in backward_graph.items():
            if m not in reverse_graph:
                reverse_graph[m] = []
            for dep in deps:
                if dep not in reverse_graph:
                    reverse_graph[dep] = []
                if m not in reverse_graph[dep]:
                    reverse_graph[dep].append(m)
                    
        def get_all_downstream(node, visited=None):
            if visited is None:
                visited = set()
            if node in visited:
                return set()
            visited.add(node)
            downstream = set()
            for child in reverse_graph.get(node, []):
                downstream.add(child)
                downstream.update(get_all_downstream(child, visited))
            return downstream

        for milestone_id, status in snapshot.milestone_statuses.items():
            downstream_set = get_all_downstream(milestone_id)
            cascade_count = len(downstream_set)
            
            # Root cause heuristic: Is it blocking things, but isn't blocked itself?
            is_root_cause = False
            if status in ["BLOCKED", "DELAYED", "IN_PROGRESS", "NOT_STARTED", "PENDING"]:
                is_root_cause = True
                for dep in backward_graph.get(milestone_id, []):
                    dep_status = snapshot.get_status(dep)
                    if dep_status in ["BLOCKED", "DELAYED", "IN_PROGRESS", "NOT_STARTED", "PENDING"]:
                        is_root_cause = False
                        break
                        
            results[milestone_id] = {
                "cascade_count": cascade_count,
                "downstream_milestones": list(downstream_set),
                "is_root_cause": is_root_cause,
                "direct_downstream_milestones": reverse_graph.get(milestone_id, [])
            }
            
        return results


class DerivedExecutionState:
    @classmethod
    def compute_derived_status(cls, snapshot: ProjectStateSnapshot, backward_graph: dict) -> dict:
        """
        Deterministically computes downstream readiness as runtime state.
        If any dependency is incomplete, the milestone is derived as BLOCKED.
        """
        derived_statuses = {}
        
        for m_id, current_status in snapshot.milestone_statuses.items():
            if current_status == "COMPLETED":
                derived_statuses[m_id] = {
                    "status": "COMPLETED",
                    "blockers": []
                }
                continue
                
            is_blocked = False
            is_at_risk = False
            blocker_names = []
            
            for dep_id in backward_graph.get(m_id, []):
                dep_status = snapshot.get_status(dep_id)
                if dep_status not in ["COMPLETED", "RESOLVED"]:
                    is_blocked = True
                    blocker_names.append(snapshot.milestone_id_to_name.get(dep_id, str(dep_id)))
                    if dep_status in ["BLOCKED", "DELAYED"]:
                        is_at_risk = True
                    
            if is_blocked and current_status not in ["COMPLETED"]:
                derived_statuses[m_id] = {
                    "status": "BLOCKED" if is_at_risk else "PENDING",
                    "blockers": blocker_names
                }
            else:
                derived_statuses[m_id] = {
                    "status": current_status,
                    "blockers": []
                }
                
        return derived_statuses

