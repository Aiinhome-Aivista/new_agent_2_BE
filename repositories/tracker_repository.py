import mysql.connector
from typing import List, Dict, Any, Optional
from core.database import get_db_connection

class TrackerRepository:
    @staticmethod
    def ensure_schema_resolved_columns() -> None:
        try:
            conn = get_db_connection()
            if conn:
                cursor = conn.cursor()
                try:
                    cursor.execute("ALTER TABLE tracker_items ADD COLUMN resolved_by BIGINT NULL;")
                    conn.commit()
                except Exception:
                    pass
                try:
                    cursor.execute("ALTER TABLE tracker_items ADD COLUMN resolved_at TIMESTAMP NULL;")
                    conn.commit()
                except Exception:
                    pass
                cursor.close()
                conn.close()
        except Exception as e:
            print(f"Tracker table auto-migration warning: {e}")

    @staticmethod
    def get_tracker_items(db: mysql.connector.connection.MySQLConnection, project_id: int) -> List[Dict[str, Any]]:
        cursor = db.cursor(dictionary=True)
        cursor.execute("""
            SELECT ti.*, 
                   CASE 
                     WHEN ti.item_type = 'ACTIVITY' THEN pa.activity_name 
                     WHEN ti.item_type = 'NEW_REQUEST' THEN nr.request_name
                     ELSE CONCAT(REPLACE(ti.item_type, '_', ' '), ' #', ti.id)
                   END as name,
                   d.document_name,
                   u.name as resolved_by_name,
                   u.email as resolved_by_email
            FROM tracker_items ti
            LEFT JOIN project_activities pa ON ti.item_type = 'ACTIVITY' AND ti.reference_id = pa.id
            LEFT JOIN new_requests nr ON ti.item_type = 'NEW_REQUEST' AND ti.reference_id = nr.id
            LEFT JOIN documents d ON ti.source_document_id = d.id
            LEFT JOIN users u ON ti.resolved_by = u.id
            WHERE ti.project_id = %s
            ORDER BY ti.risk_score DESC
        """, (project_id,))
        items = cursor.fetchall()
        cursor.close()
        return items

    @staticmethod
    def get_tracker_item_by_id_and_project(db: mysql.connector.connection.MySQLConnection, item_id: int, project_id: int) -> Optional[Dict[str, Any]]:
        cursor = db.cursor(dictionary=True)
        cursor.execute("SELECT * FROM tracker_items WHERE id = %s AND project_id = %s", (item_id, project_id))
        item = cursor.fetchone()
        cursor.close()
        return item

    @staticmethod
    def resolve_item(db: mysql.connector.connection.MySQLConnection, item_id: int, resolution: str, status: str, resolved_by: int) -> None:
        cursor = db.cursor()
        cursor.execute("""
            UPDATE tracker_items 
            SET resolution = %s, status = %s, resolved_by = %s, resolved_at = NOW() 
            WHERE id = %s
        """, (resolution, status, resolved_by, item_id))
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
        return item
