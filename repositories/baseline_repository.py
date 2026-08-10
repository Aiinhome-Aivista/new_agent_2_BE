# pyrefly: ignore [missing-import]
import mysql.connector
from typing import List, Dict, Any, Optional

class BaselineRepository:
    @staticmethod
    def get_document(db: mysql.connector.connection.MySQLConnection, document_id: int, project_id: int) -> Optional[Dict[str, Any]]:
        cursor = db.cursor(dictionary=True)
        cursor.execute("SELECT * FROM documents WHERE id = %s AND project_id = %s", (document_id, project_id))
        doc = cursor.fetchone()
        cursor.close()
        return doc

    @staticmethod
    def get_draft_baseline(db: mysql.connector.connection.MySQLConnection, project_id: int) -> Optional[Dict[str, Any]]:
        cursor = db.cursor(dictionary=True)
        cursor.execute("SELECT id FROM scope_baselines WHERE project_id = %s AND status = 'DRAFT' ORDER BY id DESC LIMIT 1", (project_id,))
        baseline = cursor.fetchone()
        cursor.close()
        return baseline

    @staticmethod
    def update_baseline_source_document(db: mysql.connector.connection.MySQLConnection, baseline_id: int, document_id: int) -> None:
        cursor = db.cursor()
        cursor.execute("UPDATE scope_baselines SET source_document_id = %s WHERE id = %s", (document_id, baseline_id))
        cursor.close()

    @staticmethod
    def delete_stakeholders_by_project(db: mysql.connector.connection.MySQLConnection, project_id: int) -> None:
        cursor = db.cursor()
        cursor.execute("DELETE FROM stakeholders WHERE project_id = %s", (project_id,))
        cursor.close()

    @staticmethod
    def delete_scope_items_by_baseline(db: mysql.connector.connection.MySQLConnection, baseline_id: int) -> None:
        cursor = db.cursor()
        cursor.execute("DELETE FROM scope_items WHERE baseline_id = %s", (baseline_id,))
        cursor.close()

    @staticmethod
    def delete_deliverables_by_baseline(db: mysql.connector.connection.MySQLConnection, baseline_id: int) -> None:
        cursor = db.cursor()
        cursor.execute("DELETE FROM deliverables WHERE baseline_id = %s", (baseline_id,))
        cursor.close()

    @staticmethod
    def get_max_baseline_version(db: mysql.connector.connection.MySQLConnection, project_id: int) -> int:
        cursor = db.cursor(dictionary=True)
        cursor.execute("SELECT MAX(version) as max_v FROM scope_baselines WHERE project_id = %s", (project_id,))
        row = cursor.fetchone()
        cursor.close()
        return (row["max_v"] or 0) if (row and "max_v" in row) else 0

    @staticmethod
    def create_baseline(db: mysql.connector.connection.MySQLConnection, project_id: int, version: int, document_id: int, status: str = 'DRAFT') -> int:
        cursor = db.cursor()
        cursor.execute("INSERT INTO scope_baselines (project_id, status, version, source_document_id) VALUES (%s, %s, %s, %s)", (project_id, status, version, document_id))
        baseline_id = cursor.lastrowid
        cursor.close()
        return baseline_id

    @staticmethod
    def get_latest_approved_baseline(db: mysql.connector.connection.MySQLConnection, project_id: int) -> Optional[Dict[str, Any]]:
        cursor = db.cursor(dictionary=True)
        cursor.execute("SELECT id FROM scope_baselines WHERE project_id = %s AND status = 'APPROVED' ORDER BY id DESC LIMIT 1", (project_id,))
        baseline = cursor.fetchone()
        cursor.close()
        return baseline

    @staticmethod
    def copy_scope_items(db: mysql.connector.connection.MySQLConnection, source_baseline_id: int, target_baseline_id: int) -> None:
        cursor = db.cursor()
        cursor.execute("""
            INSERT INTO scope_items (baseline_id, project_id, name, scope_item_normalized, description, scope_type, source_document_id, source_page, source_section, evidence_text, confidence, deadline, deadline_original, deadline_normalized, milestone, milestone_normalized, deadline_text, extraction_confidence, extraction_method)
            SELECT %s, project_id, name, scope_item_normalized, description, scope_type, source_document_id, source_page, source_section, evidence_text, confidence, deadline, deadline_original, deadline_normalized, milestone, milestone_normalized, deadline_text, extraction_confidence, extraction_method
            FROM scope_items WHERE baseline_id = %s AND completion_status NOT IN ('COMPLETED', 'CANCELLED')
        """, (target_baseline_id, source_baseline_id))
        cursor.close()

    @staticmethod
    def copy_deliverables(db: mysql.connector.connection.MySQLConnection, source_baseline_id: int, target_baseline_id: int) -> None:
        cursor = db.cursor()
        cursor.execute("""
            INSERT INTO deliverables (baseline_id, project_id, name, description, deadline, owner, source_document_id)
            SELECT %s, project_id, name, description, deadline, owner, source_document_id
            FROM deliverables WHERE baseline_id = %s
        """, (target_baseline_id, source_baseline_id))
        cursor.close()


    @staticmethod
    def create_project_milestone(db, project_id, baseline_id, name, sequence, status='Planned', planned_date=None):
        cursor = db.cursor()
        cursor.execute(
            "INSERT INTO project_milestones (project_id, baseline_id, name, sequence, status, planned_date) VALUES (%s, %s, %s, %s, %s, %s)",
            (project_id, baseline_id, name, sequence, status, planned_date)
        )
        milestone_id = cursor.lastrowid
        cursor.close()
        return milestone_id

    @staticmethod
    def create_scope_milestone_mapping(db, scope_item_id, milestone_id, weight=1.0):
        cursor = db.cursor()
        cursor.execute(
            "INSERT INTO scope_milestone_mapping (scope_item_id, milestone_id, weight) VALUES (%s, %s, %s)",
            (scope_item_id, milestone_id, weight)
        )
        cursor.close()

    @staticmethod
    def get_scope_items_for_diff(db: mysql.connector.connection.MySQLConnection, baseline_id: int) -> List[Dict[str, Any]]:
        cursor = db.cursor(dictionary=True)
        cursor.execute("SELECT id, name, scope_type, milestone, deadline_text FROM scope_items WHERE baseline_id = %s", (baseline_id,))
        items = cursor.fetchall()
        cursor.close()
        return items

    @staticmethod
    def update_scope_item(db: mysql.connector.connection.MySQLConnection, item_id: int, description: str, scope_type: str, source_document_id: int, source_page: Optional[int], source_section: Optional[str], evidence_text: str, confidence: float, status_change_tag: Optional[str], deadline: Optional[str], milestone: Optional[str] = None, deadline_text: Optional[str] = None, extraction_confidence: Optional[float] = None, extraction_method: Optional[str] = None, scope_item_normalized: Optional[str] = None, milestone_normalized: Optional[str] = None, deadline_original: Optional[str] = None, deadline_normalized: Optional[str] = None, category: Optional[str] = None, completion_status: Optional[str] = None) -> None:
        cursor = db.cursor()
        sql = """UPDATE scope_items 
                 SET description = %s, scope_type = %s, source_document_id = %s, source_page = %s, 
                     source_section = %s, evidence_text = %s, confidence = %s, status_change_tag = %s, deadline = %s,
                     milestone = %s, deadline_text = %s, extraction_confidence = %s, extraction_method = %s,
                     scope_item_normalized = %s, milestone_normalized = %s, deadline_original = %s, deadline_normalized = %s, category = %s, completion_status = COALESCE(%s, completion_status)
                 WHERE id = %s"""
        cursor.execute(sql, (
            description, scope_type, source_document_id, source_page,
            source_section, evidence_text, confidence, status_change_tag, deadline,
            milestone, deadline_text, extraction_confidence, extraction_method,
            scope_item_normalized, milestone_normalized, deadline_original, deadline_normalized, category, completion_status, item_id
        ))
        cursor.close()

    @staticmethod
    def insert_scope_item_extracted(db: mysql.connector.connection.MySQLConnection, baseline_id: int, project_id: int, name: str, description: str, scope_type: str, source_document_id: int, source_page: Optional[int], source_section: Optional[str], evidence_text: str, confidence: float, deadline: Optional[str], milestone: Optional[str] = None, deadline_text: Optional[str] = None, extraction_confidence: Optional[float] = None, extraction_method: Optional[str] = None, scope_item_normalized: Optional[str] = None, milestone_normalized: Optional[str] = None, deadline_original: Optional[str] = None, deadline_normalized: Optional[str] = None, category: Optional[str] = None, completion_status: str = 'ACTIVE') -> int:
        cursor = db.cursor()
        sql = """INSERT INTO scope_items 
                 (baseline_id, project_id, name, scope_item_normalized, description, scope_type, source_document_id, source_page, source_section, evidence_text, confidence, deadline, deadline_original, deadline_normalized, milestone, milestone_normalized, deadline_text, extraction_confidence, extraction_method, category, completion_status)
                 VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"""
        cursor.execute(sql, (
            baseline_id, project_id, name, scope_item_normalized, description,
            scope_type, source_document_id, source_page,
            source_section, evidence_text, confidence, deadline, deadline_original, deadline_normalized,
            milestone, milestone_normalized, deadline_text, extraction_confidence, extraction_method, category, completion_status
        ))
        item_id = cursor.lastrowid
        cursor.close()
        return item_id

    @staticmethod
    def get_deliverables_for_diff(db: mysql.connector.connection.MySQLConnection, baseline_id: int) -> List[Dict[str, Any]]:
        cursor = db.cursor(dictionary=True)
        cursor.execute("SELECT id, name FROM deliverables WHERE baseline_id = %s", (baseline_id,))
        items = cursor.fetchall()
        cursor.close()
        return items

    @staticmethod
    def update_deliverable(db: mysql.connector.connection.MySQLConnection, item_id: int, description: str, deadline: Optional[str], owner: Optional[str], source_document_id: int) -> None:
        cursor = db.cursor()
        sql = """UPDATE deliverables
                 SET description = %s, deadline = %s, owner = %s, source_document_id = %s
                 WHERE id = %s"""
        cursor.execute(sql, (
            description, deadline, owner, source_document_id, item_id
        ))
        cursor.close()

    @staticmethod
    def insert_deliverable(db: mysql.connector.connection.MySQLConnection, baseline_id: int, project_id: int, name: str, description: str, deadline: Optional[str], owner: Optional[str], source_document_id: int) -> None:
        cursor = db.cursor()
        sql = """INSERT INTO deliverables
                 (baseline_id, project_id, name, description, deadline, owner, source_document_id)
                 VALUES (%s, %s, %s, %s, %s, %s, %s)"""
        cursor.execute(sql, (
            baseline_id, project_id, name, description,
            deadline, owner, source_document_id
        ))
        cursor.close()

    @staticmethod
    def insert_stakeholder(db: mysql.connector.connection.MySQLConnection, project_id: int, name: str, email: Optional[str], role: Optional[str], responsibility: Optional[str]) -> None:
        cursor = db.cursor()
        sql = """INSERT INTO stakeholders (project_id, name, email, role, responsibility)
                 VALUES (%s, %s, %s, %s, %s)"""
        cursor.execute(sql, (
            project_id, name, email, role, responsibility
        ))
        cursor.close()

    @staticmethod
    def update_project_monitoring_status(db: mysql.connector.connection.MySQLConnection, project_id: int, status: str) -> None:
        cursor = db.cursor()
        cursor.execute("UPDATE projects SET monitoring_status = %s WHERE id = %s", (status, project_id))
        cursor.close()

    @staticmethod
    def get_latest_baseline_details(db: mysql.connector.connection.MySQLConnection, project_id: int) -> Optional[Dict[str, Any]]:
        cursor = db.cursor(dictionary=True)
        cursor.execute("SELECT * FROM scope_baselines WHERE project_id = %s ORDER BY id DESC LIMIT 1", (project_id,))
        baseline = cursor.fetchone()
        if not baseline:
            cursor.close()
            return None
        cursor.execute("SELECT * FROM scope_items WHERE baseline_id = %s ORDER BY id ASC", (baseline["id"],))
        items = cursor.fetchall()
        
        for item in items:
            cursor.execute("""
                SELECT dp.*, psc.label as status_label, doc.document_name, doc.document_type
                FROM deliverable_progress dp
                LEFT JOIN progress_status_config psc ON dp.status_code = psc.status_code
                LEFT JOIN documents doc ON dp.source_document_id = doc.id
                WHERE dp.scope_item_id = %s
                ORDER BY dp.id DESC LIMIT 1
            """, (item["id"],))
            item["latest_progress"] = cursor.fetchone()
            # Attach recurring occurrences to parent items
            if item.get("is_recurring") and not item.get("parent_scope_item_id"):
                cursor.execute("""
                    SELECT * FROM scope_items
                    WHERE parent_scope_item_id = %s
                    ORDER BY deadline ASC
                """, (item["id"],))
                occurrences = cursor.fetchall()
                for occ in occurrences:
                    cursor.execute("""
                        SELECT dp.*, psc.label as status_label
                        FROM deliverable_progress dp
                        LEFT JOIN progress_status_config psc ON dp.status_code = psc.status_code
                        WHERE dp.scope_item_id = %s ORDER BY dp.id DESC LIMIT 1
                    """, (occ["id"],))
                    occ["latest_progress"] = cursor.fetchone()
                item["recurring_occurrences"] = occurrences
            else:
                item["recurring_occurrences"] = []
            
        cursor.execute("SELECT * FROM deliverables WHERE baseline_id = %s", (baseline["id"],))
        deliverables = cursor.fetchall()
        
        cursor.execute("""
            SELECT pm.*, 
                   GROUP_CONCAT(DISTINCT md.child_milestone_id) as blocking_ids, 
                   GROUP_CONCAT(DISTINCT md2.parent_milestone_id) as blocked_by_ids,
                   GROUP_CONCAT(DISTINCT CONCAT(succ.name, '||', md.dependency_type) SEPARATOR ';;') as successor_details,
                   GROUP_CONCAT(DISTINCT CONCAT(pred.name, '||', md2.dependency_type) SEPARATOR ';;') as predecessor_details
            FROM project_milestones pm
            LEFT JOIN milestone_dependencies md ON pm.id = md.parent_milestone_id
            LEFT JOIN project_milestones succ ON md.child_milestone_id = succ.id
            LEFT JOIN milestone_dependencies md2 ON pm.id = md2.child_milestone_id
            LEFT JOIN project_milestones pred ON md2.parent_milestone_id = pred.id
            WHERE pm.project_id = %s AND pm.baseline_id = %s
            GROUP BY pm.id
        """, (project_id, baseline["id"]))
        milestones = cursor.fetchall()
        
        cursor.close()
        
        # Separate parent recurring items from their child occurrences in the flat list
        # (child occurrences are already attached to their parent via recurring_occurrences)
        top_level_items = [i for i in items if not i.get("parent_scope_item_id")]
        
        baseline["scope_items"] = top_level_items
        baseline["deliverables"] = deliverables
        baseline["milestones"] = milestones
        return baseline

    @staticmethod
    def get_all_baseline_versions(db: mysql.connector.connection.MySQLConnection, project_id: int) -> List[Dict[str, Any]]:
        cursor = db.cursor(dictionary=True)
        cursor.execute("""
            SELECT sb.*, d.document_name as document_name 
            FROM scope_baselines sb
            LEFT JOIN documents d ON sb.source_document_id = d.id
            WHERE sb.project_id = %s 
            ORDER BY sb.version DESC
        """, (project_id,))
        baselines = cursor.fetchall()
        for b in baselines:
            cursor.execute("SELECT * FROM scope_items WHERE baseline_id = %s", (b["id"],))
            b["scope_items"] = cursor.fetchall()
            cursor.execute("SELECT * FROM deliverables WHERE baseline_id = %s", (b["id"],))
            b["deliverables"] = cursor.fetchall()
        cursor.close()
        return baselines

    @staticmethod
    def approve_baseline(db: mysql.connector.connection.MySQLConnection, baseline_id: int, user_id: int) -> None:
        cursor = db.cursor()
        cursor.execute("UPDATE scope_baselines SET status = 'APPROVED', approved_by = %s, approved_at = NOW() WHERE id = %s", (user_id, baseline_id))
        cursor.close()

    @staticmethod
    def get_latest_baseline(db: mysql.connector.connection.MySQLConnection, project_id: int) -> Optional[Dict[str, Any]]:
        cursor = db.cursor(dictionary=True)
        cursor.execute("SELECT id, source_document_id FROM scope_baselines WHERE project_id = %s ORDER BY version DESC LIMIT 1", (project_id,))
        baseline = cursor.fetchone()
        cursor.close()
        return baseline

    @staticmethod
    def create_simple_baseline(db: mysql.connector.connection.MySQLConnection, project_id: int, status: str = 'DRAFT') -> int:
        cursor = db.cursor()
        cursor.execute("INSERT INTO scope_baselines (project_id, status) VALUES (%s, %s)", (project_id, status))
        baseline_id = cursor.lastrowid
        cursor.close()
        return baseline_id

    @staticmethod
    def create_scope_item(db: mysql.connector.connection.MySQLConnection, baseline_id: int, project_id: int, name: str, description: str, scope_type: str, evidence_text: str, confidence: float, source_document_id: Optional[int] = None, deadline: Optional[str] = None, milestone: Optional[str] = None, deadline_text: Optional[str] = None, scope_item_normalized: Optional[str] = None, milestone_normalized: Optional[str] = None, deadline_original: Optional[str] = None, deadline_normalized: Optional[str] = None) -> int:
        cursor = db.cursor()
        sql = """
            INSERT INTO scope_items (baseline_id, project_id, name, scope_item_normalized, description, scope_type, evidence_text, confidence, source_document_id, deadline, deadline_original, deadline_normalized, milestone, milestone_normalized, deadline_text)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """
        cursor.execute(sql, (baseline_id, project_id, name, scope_item_normalized, description, scope_type, evidence_text, confidence, source_document_id, deadline, deadline_original, deadline_normalized, milestone, milestone_normalized, deadline_text))
        item_id = cursor.lastrowid
        cursor.close()
        return item_id


    @staticmethod
    def create_project_milestone(db, project_id, baseline_id, name, sequence, status='Planned', planned_date=None):
        cursor = db.cursor()
        cursor.execute(
            "INSERT INTO project_milestones (project_id, baseline_id, name, sequence, status, planned_date) VALUES (%s, %s, %s, %s, %s, %s)",
            (project_id, baseline_id, name, sequence, status, planned_date)
        )
        milestone_id = cursor.lastrowid
        cursor.close()
        return milestone_id

    @staticmethod
    def create_scope_milestone_mapping(db, scope_item_id, milestone_id, weight=1.0):
        cursor = db.cursor()
        cursor.execute(
            "INSERT INTO scope_milestone_mapping (scope_item_id, milestone_id, weight) VALUES (%s, %s, %s)",
            (scope_item_id, milestone_id, weight)
        )
        cursor.close()

    @staticmethod
    def get_scope_item(db: mysql.connector.connection.MySQLConnection, item_id: int) -> Optional[Dict[str, Any]]:
        cursor = db.cursor(dictionary=True)
        cursor.execute("SELECT * FROM scope_items WHERE id = %s", (item_id,))
        item = cursor.fetchone()
        cursor.close()
        return item

    @staticmethod
    def check_scope_item_exists_in_project(db: mysql.connector.connection.MySQLConnection, item_id: int, project_id: int) -> Optional[Dict[str, Any]]:
        cursor = db.cursor(dictionary=True)
        cursor.execute("SELECT id FROM scope_items WHERE id = %s AND project_id = %s", (item_id, project_id))
        item = cursor.fetchone()
        cursor.close()
        return item

    @staticmethod
    def delete_scope_item(db: mysql.connector.connection.MySQLConnection, item_id: int, project_id: int) -> None:
        cursor = db.cursor()
        cursor.execute("DELETE FROM scope_items WHERE id = %s AND project_id = %s", (item_id, project_id))
        cursor.close()

    @staticmethod
    def delete_baseline(db: mysql.connector.connection.MySQLConnection, baseline_id: int, project_id: int) -> None:
        cursor = db.cursor()
        cursor.execute("DELETE FROM deliverables WHERE baseline_id = %s AND project_id = %s", (baseline_id, project_id))
        cursor.execute("DELETE FROM scope_items WHERE baseline_id = %s AND project_id = %s", (baseline_id, project_id))
        sql = "DELETE FROM scope_baselines WHERE id = %s AND project_id = %s AND status = 'DRAFT'"
        cursor.execute(sql, (baseline_id, project_id))
        cursor.close()

    @staticmethod
    def update_scope_item_completion(db: mysql.connector.connection.MySQLConnection, item_id: int, project_id: int, completion_status: str) -> None:
        cursor = db.cursor()
        cursor.execute(
            "UPDATE scope_items SET completion_status = %s WHERE id = %s AND project_id = %s",
            (completion_status, item_id, project_id)
        )
        cursor.close()

    @staticmethod
    def insert_deliverable_progress(
        db: mysql.connector.connection.MySQLConnection, 
        project_id: int, 
        scope_item_id: int, 
        source_document_id: int, 
        risk_evaluation_id: int,
        baseline_version: int,
        status_code: str, 
        progress_percentage: Optional[int], 
        execution_summary: str,
        dependencies: list,
        resolved_items: list,
        confidence: float,
        evidence_text: str
    ) -> None:
        import json
        cursor = db.cursor()
        import datetime
        import re
        _normalize = lambda x: re.sub(r'[^\w\s]', '', (x or '').lower().strip())
        
        # Dependency Lifecycle Merging (Execution Prerequisite State Manager)
        # Fetch the previous dependencies for this scope_item
        cursor.execute(
            "SELECT dependencies FROM deliverable_progress WHERE scope_item_id = %s ORDER BY id DESC LIMIT 1",
            (scope_item_id,)
        )
        prev_row = cursor.fetchone()
        
        previous_deps_map = {}
        if prev_row and prev_row[0]:
            try:
                prev_deps = json.loads(prev_row[0])
                for d in prev_deps:
                    if isinstance(d, dict) and "name" in d:
                        previous_deps_map[_normalize(d["name"])] = d
                    elif isinstance(d, str):
                        # Handle legacy string format
                        previous_deps_map[_normalize(d)] = {
                            "name": d,
                            "status": "PENDING",
                            "last_updated": datetime.datetime.now().isoformat(),
                            "evidence": "",
                            "resolved_by_document": None
                        }
            except Exception:
                pass
                
        # Build new dependencies state
        current_deps_map = {}
        
        # 1. Start with everything we just extracted (which the LLM thinks are pending/blocking right now)
        for d in dependencies:
            name = d if isinstance(d, str) else d.get("name", str(d))
            norm_name = _normalize(name)
            current_deps_map[norm_name] = {
                "name": name,
                "status": "PENDING",
                "last_updated": datetime.datetime.now().isoformat(),
                "evidence": "Extracted as execution prerequisite",
                "resolved_by_document": None
            }
            
        # 2. Inherit previous dependencies that haven't explicitly transitioned
        for norm_name, prev_obj in previous_deps_map.items():
            if norm_name not in current_deps_map:
                current_deps_map[norm_name] = prev_obj
                
        # 3. Apply Explicit Resolutions
        for r_item in resolved_items:
            r_name = r_item.get("name", "")
            r_norm = _normalize(r_name)
            # Find matching prerequisite
            for d_norm in list(current_deps_map.keys()):
                if r_norm in d_norm or d_norm in r_norm:
                    current_deps_map[d_norm]["status"] = "COMPLETED"
                    current_deps_map[d_norm]["last_updated"] = datetime.datetime.now().isoformat()
                    current_deps_map[d_norm]["evidence"] = r_item.get("resolution_evidence", "Resolved")
                    current_deps_map[d_norm]["resolved_by_document"] = source_document_id
                    
        final_dependencies = list(current_deps_map.values())

        sql = """
            INSERT INTO deliverable_progress (
                project_id, scope_item_id, source_document_id, risk_evaluation_id, baseline_version,
                status_code, progress_percentage, execution_summary, dependencies, confidence, evidence_text
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """
        cursor.execute(sql, (
            project_id, scope_item_id, source_document_id, risk_evaluation_id, baseline_version,
            status_code, progress_percentage, execution_summary, json.dumps(final_dependencies), 
            confidence, evidence_text
        ))
        cursor.close()

    @staticmethod
    def update_scope_item_details(
        db: mysql.connector.connection.MySQLConnection,
        item_id: int,
        project_id: int,
        completion_status: Optional[str] = None,
        deadline: Optional[str] = None
    ) -> None:
        cursor = db.cursor()
        updates = []
        params = []
        if completion_status is not None:
            updates.append("completion_status = %s")
            params.append(completion_status)
        if deadline is not None:
            updates.append("deadline = %s")
            params.append(deadline)
        if updates:
            params.extend([item_id, project_id])
            sql = f"UPDATE scope_items SET {', '.join(updates)} WHERE id = %s AND project_id = %s"
            cursor.execute(sql, tuple(params))
        cursor.close()

    # -----------------------------------------------------------------------
    # RECURRING DELIVERABLE HELPERS
    # -----------------------------------------------------------------------

    @staticmethod
    def get_recurring_parents(db: mysql.connector.connection.MySQLConnection, baseline_id: int) -> List[Dict[str, Any]]:
        """Return all recurring parent scope items for a baseline."""
        cursor = db.cursor(dictionary=True)
        cursor.execute(
            """SELECT * FROM scope_items
               WHERE baseline_id = %s AND is_recurring = 1 AND parent_scope_item_id IS NULL""",
            (baseline_id,)
        )
        items = cursor.fetchall()
        cursor.close()
        return items

    @staticmethod
    def get_recurring_children(db: mysql.connector.connection.MySQLConnection, parent_id: int) -> List[Dict[str, Any]]:
        """Return all generated occurrence rows for a given parent."""
        cursor = db.cursor(dictionary=True)
        cursor.execute(
            "SELECT * FROM scope_items WHERE parent_scope_item_id = %s ORDER BY deadline ASC",
            (parent_id,)
        )
        items = cursor.fetchall()
        cursor.close()
        return items

    @staticmethod
    def delete_future_occurrences_after_date(
        db: mysql.connector.connection.MySQLConnection,
        parent_id: int,
        after_date: str
    ) -> int:
        """Hard-delete future occurrences that have NO progress recorded (for shortened projects)."""
        cursor = db.cursor()
        cursor.execute(
            """DELETE si FROM scope_items si
               LEFT JOIN deliverable_progress dp ON dp.scope_item_id = si.id
               WHERE si.parent_scope_item_id = %s
                 AND si.deadline > %s
                 AND dp.id IS NULL""",
            (parent_id, after_date)
        )
        affected = cursor.rowcount
        cursor.close()
        return affected

