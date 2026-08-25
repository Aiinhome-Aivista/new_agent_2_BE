# pyrefly: ignore [missing-import]
import mysql.connector
from typing import List, Dict, Any, Optional
import json
from core.database import get_db_connection

class TrackerRepository:
    @staticmethod
    def ensure_schema_resolved_columns() -> None:
        try:
            conn = get_db_connection()
            if conn:
                cursor = conn.cursor()
                new_columns = [
                    "ALTER TABLE tracker_items ADD COLUMN resolved_by BIGINT NULL",
                    "ALTER TABLE tracker_items ADD COLUMN resolved_at TIMESTAMP NULL",
                    "ALTER TABLE tracker_items ADD COLUMN title VARCHAR(255) NULL AFTER reference_id",
                    "ALTER TABLE tracker_items ADD COLUMN priority_order INT NULL",
                    "ALTER TABLE tracker_items ADD COLUMN recommended_action TEXT NULL",
                    "ALTER TABLE tracker_items ADD COLUMN execution_priority_score INT NULL",
                    # New decoupled status and graph metadata columns
                    "ALTER TABLE tracker_items ADD COLUMN execution_status VARCHAR(60) NULL COMMENT 'Operational status from document: WAITING_ON_CUSTOMER, NOT_STARTED, DELAYED, IN_PROGRESS'",
                    "ALTER TABLE tracker_items ADD COLUMN risk_status VARCHAR(30) NULL DEFAULT 'OPEN' COMMENT 'Risk lifecycle: OPEN, RESOLVED, NO_ACTIVE_RISK'",
                    "ALTER TABLE tracker_items ADD COLUMN graph_role VARCHAR(40) NULL COMMENT 'Graph-derived role: ROOT_CAUSE, EXECUTION_BLOCKER, DOWNSTREAM_ACTIVITY, etc.'",
                    "ALTER TABLE tracker_items ADD COLUMN canonical_id VARCHAR(40) NULL COMMENT 'Canonical entity ID from EntityResolver registry'",
                    "ALTER TABLE tracker_items ADD COLUMN risk_severity_score INT NULL COMMENT 'Risk severity score, independent of execution_priority_score'",
                ]
                for sql in new_columns:
                    try:
                        cursor.execute(sql)
                        conn.commit()
                    except Exception:
                        pass  # Column already exists
                cursor.close()
                conn.close()
        except Exception as e:
            print(f"Tracker table auto-migration warning: {e}")

    @staticmethod
    def _fetch_and_attach_audit_trails(db: mysql.connector.connection.MySQLConnection, items: List[Dict[str, Any]], project_id: int) -> None:
        if not items:
            return
        
        cursor = db.cursor(dictionary=True)
        cursor.execute("""
            SELECT a.*
            FROM audit_logs a
            WHERE a.project_id = %s AND a.entity_type = 'TRACKER_ITEM'
            ORDER BY a.created_at ASC
        """, (project_id,))
        logs = cursor.fetchall()
        cursor.close()
        
        logs_by_item = {}
        for log in logs:
            eid = log["entity_id"]
            if eid not in logs_by_item:
                logs_by_item[eid] = []
            
            details = {}
            if log["details_json"]:
                if isinstance(log["details_json"], dict):
                    details = log["details_json"]
                elif isinstance(log["details_json"], str):
                    try:
                        details = json.loads(log["details_json"])
                    except Exception:
                        pass
                    
            logs_by_item[eid].append({
                "action": log["action"],
                "agent_name": log["agent_name"],
                "user_name": details.get("user_name", "System") if isinstance(details, dict) else "System",
                "created_at": log["created_at"].isoformat() if log["created_at"] else None,
                "details": details
            })
            
        for item in items:
            item["audit_trail"] = logs_by_item.get(item["id"], [])

    @staticmethod
    def get_tracker_items(db: mysql.connector.connection.MySQLConnection, project_id: int) -> List[Dict[str, Any]]:
        cursor = db.cursor(dictionary=True)
        cursor.execute("""
            SELECT ti.*, 
                   COALESCE(ti.title, 
                            CASE 
                              WHEN ti.item_type = 'ACTIVITY' THEN pa.activity_name 
                              WHEN ti.item_type = 'NEW_REQUEST' THEN nr.request_name
                              ELSE CONCAT(REPLACE(ti.item_type, '_', ' '), ' #', ti.id)
                            END) as name,
                   d.document_name,
                   u.name as resolved_by_name,
                   u.email as resolved_by_email
            FROM tracker_items ti
            LEFT JOIN project_activities pa ON ti.item_type = 'ACTIVITY' AND ti.reference_id = pa.id
            LEFT JOIN new_requests nr ON ti.item_type = 'NEW_REQUEST' AND ti.reference_id = nr.id
            LEFT JOIN documents d ON ti.source_document_id = d.id
            LEFT JOIN users u ON ti.resolved_by = u.id
            WHERE ti.project_id = %s
            ORDER BY
                CASE WHEN ti.priority_order IS NOT NULL THEN ti.priority_order ELSE 9999 END ASC,
                COALESCE(ti.execution_priority_score, 0) DESC,
                ti.risk_score DESC
        """, (project_id,))
        items = cursor.fetchall() or []
        cursor.close()
        
        # FIX 1 & BUG 2: Populate owner and dependency_owner from embedded reasoning JSON if present
        for it in items:
            owner = None
            if it.get("reasoning"):
                try:
                    r_parsed = json.loads(it["reasoning"])
                    if isinstance(r_parsed, dict) and r_parsed.get("owner"):
                        owner = r_parsed["owner"]
                except Exception:
                    pass
            if not owner:
                owner = it.get("dependency_owner") or ("Customer" if "CUSTOMER" in str(it.get("risk_category", "")).upper() else "Internal")
            
            it["owner"] = owner
            if not it.get("dependency_owner"):
                it["dependency_owner"] = owner
        
        TrackerRepository._fetch_and_attach_audit_trails(db, items, project_id)
        
        return items

    @staticmethod
    def get_tracker_item_by_id_and_project(db: mysql.connector.connection.MySQLConnection, item_id: int, project_id: int) -> Optional[Dict[str, Any]]:
        cursor = db.cursor(dictionary=True)
        cursor.execute("SELECT * FROM tracker_items WHERE id = %s AND project_id = %s", (item_id, project_id))
        item = cursor.fetchone()
        cursor.close()
        
        if item:
            owner = None
            if item.get("reasoning"):
                try:
                    r_parsed = json.loads(item["reasoning"])
                    if isinstance(r_parsed, dict) and r_parsed.get("owner"):
                        owner = r_parsed["owner"]
                except Exception:
                    pass
            if not owner:
                owner = item.get("dependency_owner") or ("Customer" if "CUSTOMER" in str(item.get("risk_category", "")).upper() else "Internal")
            
            item["owner"] = owner
            if not item.get("dependency_owner"):
                item["dependency_owner"] = owner
            TrackerRepository._fetch_and_attach_audit_trails(db, [item], project_id)
            
        return item

    @staticmethod
    def resolve_item(db: mysql.connector.connection.MySQLConnection, item_id: int, resolution: str, status: str, resolved_by: int) -> None:
        cursor = db.cursor()
        cursor.execute("""
            UPDATE tracker_items 
            SET resolution = %s, status = %s, resolved_by = %s, resolved_at = NOW() 
            WHERE id = %s
        """, (resolution, status, resolved_by, item_id))
        
        cursor.execute("SELECT project_id FROM tracker_items WHERE id = %s", (item_id,))
        row = cursor.fetchone()
        project_id = row[0] if row else None
        
        if project_id:
            # Fetch user name for details_json
            cursor.execute("SELECT name FROM users WHERE id = %s", (resolved_by,))
            user_row = cursor.fetchone()
            user_name = user_row[0] if user_row else "User"
            
            details = json.dumps({"resolution": resolution, "status": status, "user_name": user_name})
            cursor.execute("""
                INSERT INTO audit_logs (project_id, agent_name, action, entity_type, entity_id, details_json)
                VALUES (%s, %s, %s, %s, %s, %s)
            """, (project_id, user_name, "RESOLVE_TRACKER_ITEM", "TRACKER_ITEM", item_id, details))
            
        cursor.close()

    @staticmethod
    def get_resolved_item_details(db: mysql.connector.connection.MySQLConnection, item_id: int) -> Optional[Dict[str, Any]]:
        cursor = db.cursor(dictionary=True)
        cursor.execute("""
            SELECT ti.*, 
                   u.name as resolved_by_name,
                   u.email as resolved_by_email
            FROM tracker_items ti
            LEFT JOIN users u ON ti.resolved_by = u.id
            WHERE ti.id = %s
        """, (item_id,))
        item = cursor.fetchone()
        cursor.close()
        
        if item:
            TrackerRepository._fetch_and_attach_audit_trails(db, [item], item['project_id'])
            
        return item

    @staticmethod
    def reactivate_item(db: mysql.connector.connection.MySQLConnection, item_id: int, reactivated_by: int = None) -> None:
        cursor = db.cursor()
        cursor.execute("""
            UPDATE tracker_items 
            SET resolution = NULL, status = 'OPEN', resolved_by = NULL, resolved_at = NULL 
            WHERE id = %s
        """, (item_id,))
        
        cursor.execute("SELECT project_id FROM tracker_items WHERE id = %s", (item_id,))
        row = cursor.fetchone()
        project_id = row[0] if row else None
        
        if project_id:
            user_name = "System"
            if reactivated_by is not None:
                cursor.execute("SELECT name FROM users WHERE id = %s", (reactivated_by,))
                user_row = cursor.fetchone()
                if user_row:
                    user_name = user_row[0]
                    
            details = json.dumps({"status": "OPEN", "reason": "Manually reactivated", "user_name": user_name})
            cursor.execute("""
                INSERT INTO audit_logs (project_id, agent_name, action, entity_type, entity_id, details_json)
                VALUES (%s, %s, %s, %s, %s, %s)
            """, (project_id, user_name, "REACTIVATE_TRACKER_ITEM", "TRACKER_ITEM", item_id, details))
            
        cursor.close()
