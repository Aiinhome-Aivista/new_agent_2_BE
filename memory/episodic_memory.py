import mysql.connector
from core.database import get_db_connection

class EpisodicMemory:
    @staticmethod
    def get_recent_events(project_id: int, limit: int = 20) -> list[dict]:
        conn = get_db_connection()
        if not conn:
            return []
        cursor = conn.cursor(dictionary=True)
        cursor.execute(
            "SELECT * FROM episodic_memory WHERE project_id = %s ORDER BY created_at DESC LIMIT %s", 
            (project_id, limit)
        )
        events = cursor.fetchall()
        cursor.close()
        conn.close()
        return events

    @staticmethod
    def add_event(project_id: int, run_id: str, event_type: str, summary: str, entity_type: str = None, entity_id: int = None, importance: float = 0.5):
        conn = get_db_connection()
        if not conn:
            return
        cursor = conn.cursor()
        sql = """INSERT INTO episodic_memory 
                 (project_id, run_id, event_type, event_summary, entity_type, entity_id, importance_score)
                 VALUES (%s, %s, %s, %s, %s, %s, %s)"""
        cursor.execute(sql, (project_id, run_id, event_type, summary, entity_type, entity_id, importance))
        conn.commit()
        cursor.close()
        conn.close()
