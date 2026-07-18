from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form, status
from typing import List
import os
import uuid
from core.config import settings
from core.database import get_db
from api.dependencies.auth import get_current_user, require_roles, verify_project_access
from services.document_service import DocumentService
import mysql.connector

router = APIRouter()

@router.post("/")
def upload_document(
    project_id: int,
    document_type: str = Form(...),
    file: UploadFile = File(...),
    current_user: dict = Depends(require_roles(["ADMIN", "ENGAGEMENT_MANAGER", "PROJECT_LEAD"])),
    db: mysql.connector.connection.MySQLConnection = Depends(get_db)
):
    verify_project_access(project_id, current_user, db)
    if not document_type:
        raise HTTPException(status_code=400, detail="Document type is required")

    ext = os.path.splitext(file.filename)[1].lower()
    if ext not in [".pdf", ".docx", ".txt"]:
        raise HTTPException(status_code=400, detail="Unsupported file format")

    storage_dir = os.path.join(settings.UPLOAD_PATH, str(project_id))
    os.makedirs(storage_dir, exist_ok=True)
    
    unique_filename = f"{uuid.uuid4()}{ext}"
    storage_key = os.path.join(storage_dir, unique_filename)
    
    with open(storage_key, "wb") as f:
        f.write(file.file.read())
        
    cursor = db.cursor(dictionary=True)
    sql = """
        INSERT INTO documents (project_id, document_name, document_type, storage_key, processing_status, uploaded_by)
        VALUES (%s, %s, %s, %s, %s, %s)
    """
    cursor.execute(sql, (project_id, file.filename, document_type, storage_key, "UPLOADED", current_user["id"]))
    db.commit()
    document_id = cursor.lastrowid
    cursor.close()

    return {"success": True, "message": "Document uploaded successfully", "data": {"id": document_id}}

@router.get("/")
def get_documents(project_id: int, current_user: dict = Depends(get_current_user), db: mysql.connector.connection.MySQLConnection = Depends(get_db)):
    verify_project_access(project_id, current_user, db)
    cursor = db.cursor(dictionary=True)
    cursor.execute("SELECT id, document_name, document_type, processing_status, uploaded_at FROM documents WHERE project_id = %s", (project_id,))
    docs = cursor.fetchall()
    cursor.close()
    return {"success": True, "data": docs}

from pydantic import BaseModel

class DocumentTypeCreate(BaseModel):
    name: str
    label: str
    description: str = ""

@router.get("/types")
def get_document_types(project_id: int, current_user: dict = Depends(get_current_user), db: mysql.connector.connection.MySQLConnection = Depends(get_db)):
    verify_project_access(project_id, current_user, db)
    cursor = db.cursor(dictionary=True)
    # Master standard types from DB
    cursor.execute("SELECT name, label, description FROM master_document_types")
    types = cursor.fetchall()
    
    # Custom types from DB
    cursor.execute("SELECT name, label, description FROM document_types WHERE project_id = %s", (project_id,))
    custom_types = cursor.fetchall()
    cursor.close()
    
    return {"success": True, "data": types + custom_types}

@router.post("/types")
def create_document_type(
    project_id: int, 
    data: DocumentTypeCreate, 
    current_user: dict = Depends(require_roles(["ADMIN", "ENGAGEMENT_MANAGER", "PROJECT_LEAD"])), 
    db: mysql.connector.connection.MySQLConnection = Depends(get_db)
):
    verify_project_access(project_id, current_user, db)
    cursor = db.cursor(dictionary=True)
    try:
        sql = "INSERT INTO document_types (project_id, name, label, description, added_by) VALUES (%s, %s, %s, %s, %s)"
        cursor.execute(sql, (project_id, data.name, data.label, data.description, current_user["id"]))
        db.commit()
    except Exception as e:
        cursor.close()
        raise HTTPException(status_code=400, detail="Failed to create document type")
        
    cursor.close()
    return {"success": True, "message": "Document type created successfully"}

@router.post("/{document_id}/process")
def process_document(
    project_id: int,
    document_id: int,
    current_user: dict = Depends(require_roles(["ADMIN", "ENGAGEMENT_MANAGER", "PROJECT_LEAD"])),
    db: mysql.connector.connection.MySQLConnection = Depends(get_db)
):
    verify_project_access(project_id, current_user, db)
    cursor = db.cursor(dictionary=True)
    cursor.execute("SELECT * FROM documents WHERE id = %s AND project_id = %s", (document_id, project_id))
    doc = cursor.fetchone()
    if not doc:
        cursor.close()
        raise HTTPException(status_code=404, detail="Document not found")
        
    try:
        cursor.execute("UPDATE documents SET processing_status = 'PROCESSING' WHERE id = %s", (document_id,))
        db.commit()
        
        ext = os.path.splitext(doc["storage_key"])[1].lower()
        chunks = DocumentService.parse_document(doc["storage_key"], ext)
        
        # Index document in ChromaDB and BM25
        from services.rag_service import RAGService
        RAGService.index_document(project_id, document_id, doc["document_name"], doc["document_type"], chunks)
        
        cursor.execute("UPDATE documents SET processing_status = 'COMPLETED' WHERE id = %s", (document_id,))
        db.commit()
        
    except Exception as e:
        cursor.execute("UPDATE documents SET processing_status = 'FAILED', processing_error = %s WHERE id = %s", (str(e), document_id))
        db.commit()
        cursor.close()
        raise HTTPException(status_code=500, detail=f"Failed to process document: {e}")
        
    cursor.close()
    return {"success": True, "message": "Document processed successfully"}

@router.delete("/{document_id}")
def delete_document(
    project_id: int,
    document_id: int,
    current_user: dict = Depends(require_roles(["ADMIN", "ENGAGEMENT_MANAGER", "PROJECT_LEAD"])),
    db: mysql.connector.connection.MySQLConnection = Depends(get_db)
):
    verify_project_access(project_id, current_user, db)
    cursor = db.cursor(dictionary=True)
    cursor.execute("SELECT * FROM documents WHERE id = %s AND project_id = %s", (document_id, project_id))
    doc = cursor.fetchone()
    if not doc:
        cursor.close()
        raise HTTPException(status_code=404, detail="Document not found")
        
    try:
        # 1. Clean up RAG resources if it was completed
        if doc["processing_status"] == "COMPLETED":
            from services.rag_service import RAGService
            RAGService.delete_document(project_id, document_id)
            
        # 2. Clean up scope items (since there's no ON DELETE CASCADE on source_document_id constraint)
        cursor.execute("DELETE FROM scope_items WHERE source_document_id = %s", (document_id,))
        db.commit()
        
        # 3. Delete document record (cascades automatically to project_activities/new_requests)
        cursor.execute("DELETE FROM documents WHERE id = %s", (document_id,))
        db.commit()
        
        # 4. Remove physical file
        if os.path.exists(doc["storage_key"]):
            os.remove(doc["storage_key"])
            
    except Exception as e:
        cursor.close()
        raise HTTPException(status_code=500, detail=f"Failed to delete document: {e}")
        
    cursor.close()
    return {"success": True, "message": "Document deleted successfully"}
