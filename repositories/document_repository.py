# pyrefly: ignore [missing-import]
import mysql.connector
from typing import List, Dict, Any, Optional

class DocumentRepository:
    @staticmethod
    def get_document(db: mysql.connector.connection.MySQLConnection, document_id: int, project_id: int) -> Optional[Dict[str, Any]]:
        cursor = db.cursor(dictionary=True)
        cursor.execute("SELECT * FROM documents WHERE id = %s AND project_id = %s", (document_id, project_id))
        doc = cursor.fetchone()
        cursor.close()
        return doc

    @staticmethod
    def create_document(db: mysql.connector.connection.MySQLConnection, project_id: int, document_name: str, document_type: str, storage_key: str, uploaded_by: int) -> int:
        cursor = db.cursor()
        sql = "INSERT INTO documents (project_id, document_name, document_type, storage_key, processing_status, uploaded_by) VALUES (%s, %s, %s, %s, 'UPLOADED', %s)"
        cursor.execute(sql, (project_id, document_name, document_type, storage_key, uploaded_by))
        doc_id = cursor.lastrowid
        cursor.close()
        return doc_id

    @staticmethod
    def get_documents_by_project(db: mysql.connector.connection.MySQLConnection, project_id: int) -> List[Dict[str, Any]]:
        cursor = db.cursor(dictionary=True)
        cursor.execute("SELECT id, document_name, document_type, processing_status, processing_error, uploaded_at FROM documents WHERE project_id = %s", (project_id,))
        docs = cursor.fetchall()
        cursor.close()
        return docs

    @staticmethod
    def get_master_document_types(db: mysql.connector.connection.MySQLConnection) -> List[Dict[str, Any]]:
        cursor = db.cursor(dictionary=True)
        cursor.execute("SELECT name, label, description FROM master_document_types")
        types = cursor.fetchall()
        cursor.close()
        return types

    @staticmethod
    def get_project_document_types(db: mysql.connector.connection.MySQLConnection, project_id: int) -> List[Dict[str, Any]]:
        cursor = db.cursor(dictionary=True)
        cursor.execute("SELECT name, label, description FROM document_types WHERE project_id = %s", (project_id,))
        types = cursor.fetchall()
        cursor.close()
        return types

    @staticmethod
    def create_custom_document_type(db: mysql.connector.connection.MySQLConnection, project_id: int, name: str, label: str, description: str, added_by: int) -> None:
        cursor = db.cursor()
        sql = "INSERT INTO document_types (project_id, name, label, description, added_by) VALUES (%s, %s, %s, %s, %s)"
        cursor.execute(sql, (project_id, name, label, description, added_by))
        cursor.close()

    @staticmethod
    def update_processing_status(db: mysql.connector.connection.MySQLConnection, document_id: int, status: str, error_message: Optional[str] = None) -> None:
        cursor = db.cursor()
        if error_message is not None:
            cursor.execute("UPDATE documents SET processing_status = %s, processing_error = %s WHERE id = %s", (status, error_message, document_id))
        else:
            cursor.execute("UPDATE documents SET processing_status = %s WHERE id = %s", (status, document_id))
        cursor.close()

    @staticmethod
    def delete_scope_items_by_document(db: mysql.connector.connection.MySQLConnection, document_id: int) -> None:
        cursor = db.cursor()
        cursor.execute("DELETE FROM scope_items WHERE source_document_id = %s", (document_id,))
        cursor.close()

    @staticmethod
    def log_audit(db: mysql.connector.connection.MySQLConnection, project_id: int, agent_name: str, action: str, entity_type: str, entity_id: int, details_json: str) -> None:
        cursor = db.cursor()
        sql = """INSERT INTO audit_logs (project_id, agent_name, action, entity_type, entity_id, details_json) 
                 VALUES (%s, %s, %s, %s, %s, %s)"""
        cursor.execute(sql, (project_id, agent_name, action, entity_type, entity_id, details_json))
        cursor.close()

    @staticmethod
    def delete_document(db: mysql.connector.connection.MySQLConnection, document_id: int) -> None:
        cursor = db.cursor()
        cursor.execute("DELETE FROM documents WHERE id = %s", (document_id,))
        cursor.close()
