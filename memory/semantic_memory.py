from services.rag_service import RAGService
import mysql.connector
from core.database import get_db_connection

class SemanticMemory:
    @staticmethod
    def get_evidence(project_id: int, query: str, document_types: list[str] = None):
        return RAGService.retrieve_evidence(project_id, query, document_types)

    @staticmethod
    def get_approved_baseline(project_id: int) -> dict:
        conn = get_db_connection()
        if not conn:
            return {}
        cursor = conn.cursor(dictionary=True)
        cursor.execute("SELECT * FROM scope_baselines WHERE project_id = %s AND status = 'APPROVED' ORDER BY id DESC LIMIT 1", (project_id,))
        baseline = cursor.fetchone()
        
        if not baseline:
            cursor.close()
            conn.close()
            return {}
            
        cursor.execute("SELECT * FROM scope_items WHERE baseline_id = %s", (baseline["id"],))
        scope_items = cursor.fetchall()
        
        cursor.execute("SELECT * FROM deliverables WHERE baseline_id = %s", (baseline["id"],))
        deliverables = cursor.fetchall()
        
        cursor.close()
        conn.close()
        
        return {
            "baseline": baseline,
            "scope_items": scope_items,
            "deliverables": deliverables
        }
