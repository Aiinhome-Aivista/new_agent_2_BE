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
    def get_max_baseline_version(db: mysql.connector.connection.MySQLConnection, project_id: int) -> int:
        cursor = db.cursor(dictionary=True)
        cursor.execute("SELECT MAX(version) as max_v FROM scope_baselines WHERE project_id = %s", (project_id,))
        row = cursor.fetchone()
        cursor.close()
        return (row["max_v"] or 0) if (row and "max_v" in row) else 0

    @staticmethod
    def create_baseline(db: mysql.connector.connection.MySQLConnection, project_id: int, version: int, document_id: int, status: str = 'DRAFT', parser_version: str = '1.0', layout_version: str = '1.0', extractor_version: str = '2.0', llm_prompt_version: str = '1.0') -> int:
        cursor = db.cursor()
        cursor.execute("INSERT INTO scope_baselines (project_id, status, version, source_document_id, parser_version, layout_version, extractor_version, llm_prompt_version) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)", (project_id, status, version, document_id, parser_version, layout_version, extractor_version, llm_prompt_version))
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
    def get_scope_items_for_diff(db: mysql.connector.connection.MySQLConnection, baseline_id: int) -> List[Dict[str, Any]]:
        cursor = db.cursor(dictionary=True)
        cursor.execute("SELECT id, name, scope_type, milestone, deadline_text FROM scope_items WHERE baseline_id = %s", (baseline_id,))
        items = cursor.fetchall()
        cursor.close()
        return items

    @staticmethod
    def update_scope_item(db: mysql.connector.connection.MySQLConnection, item_id: int, description: str, scope_type: str, source_document_id: int, source_page: Optional[int], source_section: Optional[str], evidence_text: str, confidence: float, status_change_tag: Optional[str], deadline: Optional[str], milestone: Optional[str] = None, deadline_text: Optional[str] = None, extraction_confidence: Optional[float] = None, extraction_method: Optional[str] = None, scope_item_normalized: Optional[str] = None, milestone_normalized: Optional[str] = None, deadline_original: Optional[str] = None, deadline_normalized: Optional[str] = None, entity_type_id: Optional[int] = None) -> None:
        cursor = db.cursor()
        sql = """UPDATE scope_items 
                 SET description = %s, scope_type = %s, source_document_id = %s, source_page = %s, 
                     source_section = %s, evidence_text = %s, confidence = %s, status_change_tag = %s, deadline = %s,
                     milestone = %s, deadline_text = %s, extraction_confidence = %s, extraction_method = %s,
                     scope_item_normalized = %s, milestone_normalized = %s, deadline_original = %s, deadline_normalized = %s,
                     entity_type_id = %s
                 WHERE id = %s"""
        cursor.execute(sql, (
            description, scope_type, source_document_id, source_page,
            source_section, evidence_text, confidence, status_change_tag, deadline,
            milestone, deadline_text, extraction_confidence, extraction_method,
            scope_item_normalized, milestone_normalized, deadline_original, deadline_normalized, entity_type_id, item_id
        ))
        cursor.close()

    @staticmethod
    def insert_scope_item_extracted(db: mysql.connector.connection.MySQLConnection, baseline_id: int, project_id: int, name: str, description: str, scope_type: str, source_document_id: int, source_page: Optional[int], source_section: Optional[str], evidence_text: str, confidence: float, deadline: Optional[str], milestone: Optional[str] = None, deadline_text: Optional[str] = None, extraction_confidence: Optional[float] = None, extraction_method: Optional[str] = None, scope_item_normalized: Optional[str] = None, milestone_normalized: Optional[str] = None, deadline_original: Optional[str] = None, deadline_normalized: Optional[str] = None, entity_type_id: Optional[int] = None, status_change_tag: Optional[str] = None) -> None:
        cursor = db.cursor()
        sql = """INSERT INTO scope_items 
                 (baseline_id, project_id, name, scope_item_normalized, description, scope_type, source_document_id, source_page, source_section, evidence_text, confidence, deadline, deadline_original, deadline_normalized, milestone, milestone_normalized, deadline_text, extraction_confidence, extraction_method, entity_type_id, status_change_tag)
                 VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"""
        cursor.execute(sql, (
            baseline_id, project_id, name, scope_item_normalized, description,
            scope_type, source_document_id, source_page,
            source_section, evidence_text, confidence, deadline, deadline_original, deadline_normalized,
            milestone, milestone_normalized, deadline_text, extraction_confidence, extraction_method, entity_type_id, status_change_tag
        ))
        cursor.close()

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
        cursor.execute("SELECT * FROM scope_items WHERE baseline_id = %s", (baseline["id"],))
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
            
        cursor.execute("SELECT * FROM deliverables WHERE baseline_id = %s", (baseline["id"],))
        deliverables = cursor.fetchall()
        cursor.close()
        
        baseline["scope_items"] = items
        baseline["deliverables"] = deliverables
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
        confidence: float,
        evidence_text: str
    ) -> None:
        import json
        cursor = db.cursor()
        sql = """
            INSERT INTO deliverable_progress (
                project_id, scope_item_id, source_document_id, risk_evaluation_id, baseline_version,
                status_code, progress_percentage, execution_summary, dependencies, confidence, evidence_text
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """
        cursor.execute(sql, (
            project_id, scope_item_id, source_document_id, risk_evaluation_id, baseline_version,
            status_code, progress_percentage, execution_summary, json.dumps(dependencies) if dependencies else "[]", 
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

