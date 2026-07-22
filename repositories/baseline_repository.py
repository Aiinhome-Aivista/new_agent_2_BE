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
            INSERT INTO scope_items (baseline_id, project_id, name, description, scope_type, source_document_id, source_page, source_section, evidence_text, confidence, deadline)
            SELECT %s, project_id, name, description, scope_type, source_document_id, source_page, source_section, evidence_text, confidence, deadline
            FROM scope_items WHERE baseline_id = %s
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
        cursor.execute("SELECT id, name, scope_type FROM scope_items WHERE baseline_id = %s", (baseline_id,))
        items = cursor.fetchall()
        cursor.close()
        return items

    @staticmethod
    def update_scope_item(db: mysql.connector.connection.MySQLConnection, item_id: int, description: str, scope_type: str, source_document_id: int, source_page: Optional[int], source_section: Optional[str], evidence_text: str, confidence: float, status_change_tag: Optional[str], deadline: Optional[str]) -> None:
        cursor = db.cursor()
        sql = """UPDATE scope_items 
                 SET description = %s, scope_type = %s, source_document_id = %s, source_page = %s, 
                     source_section = %s, evidence_text = %s, confidence = %s, status_change_tag = %s, deadline = %s
                 WHERE id = %s"""
        cursor.execute(sql, (
            description, scope_type, source_document_id, source_page,
            source_section, evidence_text, confidence, status_change_tag, deadline, item_id
        ))
        cursor.close()

    @staticmethod
    def insert_scope_item_extracted(db: mysql.connector.connection.MySQLConnection, baseline_id: int, project_id: int, name: str, description: str, scope_type: str, source_document_id: int, source_page: Optional[int], source_section: Optional[str], evidence_text: str, confidence: float, deadline: Optional[str]) -> None:
        cursor = db.cursor()
        sql = """INSERT INTO scope_items 
                 (baseline_id, project_id, name, description, scope_type, source_document_id, source_page, source_section, evidence_text, confidence, deadline)
                 VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"""
        cursor.execute(sql, (
            baseline_id, project_id, name, description,
            scope_type, source_document_id, source_page,
            source_section, evidence_text, confidence, deadline
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
        cursor.execute("SELECT id FROM scope_baselines WHERE project_id = %s ORDER BY version DESC LIMIT 1", (project_id,))
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
    def create_scope_item(db: mysql.connector.connection.MySQLConnection, baseline_id: int, project_id: int, name: str, description: str, scope_type: str, evidence_text: str, confidence: float) -> int:
        cursor = db.cursor()
        sql = """
            INSERT INTO scope_items (baseline_id, project_id, name, description, scope_type, evidence_text, confidence)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
        """
        cursor.execute(sql, (baseline_id, project_id, name, description, scope_type, evidence_text, confidence))
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
