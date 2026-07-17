from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form, status
from typing import List
import os
import uuid
from core.config import settings
from core.database import get_db
from api.dependencies.auth import get_current_user, require_roles
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
    
    # Trigger processing right away for POC simplicity
    try:
        cursor.execute("UPDATE documents SET processing_status = 'PARSING' WHERE id = %s", (document_id,))
        db.commit()
        
        chunks = DocumentService.parse_document(storage_key, ext)
        
        # Index document in ChromaDB and BM25
        from services.rag_service import RAGService
        RAGService.index_document(project_id, document_id, file.filename, document_type, chunks)
        
        cursor.execute("UPDATE documents SET processing_status = 'COMPLETED' WHERE id = %s", (document_id,))
        db.commit()
        
    except Exception as e:
        cursor.execute("UPDATE documents SET processing_status = 'FAILED', processing_error = %s WHERE id = %s", (str(e), document_id))
        db.commit()
        raise HTTPException(status_code=500, detail=f"Failed to process document: {e}")
    finally:
        cursor.close()

    return {"success": True, "message": "Document uploaded and parsed", "data": {"id": document_id}}

@router.get("/")
def get_documents(project_id: int, current_user: dict = Depends(get_current_user), db: mysql.connector.connection.MySQLConnection = Depends(get_db)):
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
