# pyrefly: ignore [missing-import]
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form, status
from pydantic import BaseModel
from typing import List
import os
import uuid
from core.config import settings
from core.database import get_db
from api.dependencies.auth import get_current_user, require_roles, verify_project_access
from services.document_service import DocumentService
import mysql.connector

router = APIRouter()

class ConfirmUploadRequest(BaseModel):
    temp_key: str
    document_type: str
    original_name: str

@router.post("/check-relevance")
def check_document_relevance(
    project_id: int,
    document_type: str = Form(...),
    file: UploadFile = File(...),
    current_user: dict = Depends(require_roles(["ADMIN", "ENGAGEMENT_MANAGER", "PROJECT_LEAD"])),
    db: mysql.connector.connection.MySQLConnection = Depends(get_db)
):
    verify_project_access(project_id, current_user, db)
    if not document_type:
        raise HTTPException(status_code=400, detail="Document type is required")

    cursor = db.cursor(dictionary=True)
    
    # 1. Duplicate check (strict check for EL and IFA types) - REMOVED for Multi-Upload Support
    # Allow multiple EL and IFA uploads.

    ext = os.path.splitext(file.filename)[1].lower()
    if ext not in [".pdf", ".docx", ".txt"]:
        cursor.close()
        raise HTTPException(status_code=400, detail="Unsupported file format")

    # Create a temp storage directory
    temp_dir = os.path.join(settings.UPLOAD_PATH, "temp")
    os.makedirs(temp_dir, exist_ok=True)
    
    unique_filename = f"{uuid.uuid4()}{ext}"
    storage_key = os.path.join(temp_dir, unique_filename)
    
    # Write temp file for analysis
    with open(storage_key, "wb") as f:
        f.write(file.file.read())
        
    # Extract text from the uploaded document for relevance scoring
    try:
        chunks = DocumentService.parse_document(storage_key, ext)
        sample_text = "\n".join([chunk["text"] for chunk in chunks[:8]])
        if len(sample_text) > 8000:
            sample_text = sample_text[:8000]
    except Exception as e:
        if os.path.exists(storage_key):
            os.remove(storage_key)
        cursor.close()
        raise HTTPException(status_code=400, detail=f"Failed to parse document text: {e}")

    if not sample_text.strip():
        if os.path.exists(storage_key):
            os.remove(storage_key)
        cursor.close()
        raise HTTPException(status_code=400, detail="Uploaded file appears to contain no readable text.")

    # Vector Embedding Relevance Check (replaces LLM-based scoring)
    # Uses cosine similarity between document embedding and reference profile embedding.
    # Benefits: zero LLM tokens, sub-second speed, deterministic scores.
    from services.relevance_service import RelevanceService
    try:
        result = RelevanceService.score_relevance(sample_text, document_type, db)
        score = result["score"]
        reasoning = result["reasoning"]
    except Exception as e:
        if os.path.exists(storage_key):
            os.remove(storage_key)
        cursor.close()
        raise HTTPException(status_code=500, detail=f"Relevance check failed: {e}")

    cursor.close()
    return {
        "success": True,
        "score": score,
        "reasoning": reasoning,
        "temp_key": unique_filename,
        "original_name": file.filename
    }

@router.post("/confirm-upload")
def confirm_upload_document(
    project_id: int,
    payload: ConfirmUploadRequest,
    current_user: dict = Depends(require_roles(["ADMIN", "ENGAGEMENT_MANAGER", "PROJECT_LEAD"])),
    db: mysql.connector.connection.MySQLConnection = Depends(get_db)
):
    verify_project_access(project_id, current_user, db)
    temp_dir = os.path.join(settings.UPLOAD_PATH, "temp")
    temp_file_path = os.path.join(temp_dir, payload.temp_key)
    
    if not os.path.exists(temp_file_path):
        raise HTTPException(status_code=400, detail="Temporary file session not found or expired")

    cursor = db.cursor(dictionary=True)
    
    # Duplicate check again for safety - REMOVED for Multi-Upload Support
    # Allow multiple EL and IFA uploads.

    # Move file to permanent project folder
    storage_dir = os.path.join(settings.UPLOAD_PATH, str(project_id))
    os.makedirs(storage_dir, exist_ok=True)
    storage_key = os.path.join(storage_dir, payload.temp_key)
    
    try:
        os.rename(temp_file_path, storage_key)
    except Exception as e:
        cursor.close()
        raise HTTPException(status_code=500, detail=f"Failed to finalize file storage: {e}")
        
    sql = """
        INSERT INTO documents (project_id, document_name, document_type, storage_key, processing_status, uploaded_by)
        VALUES (%s, %s, %s, %s, %s, %s)
    """
    cursor.execute(sql, (project_id, payload.original_name, payload.document_type, storage_key, "UPLOADED", current_user["id"]))
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
    
    # Auto-expand the short description into a rich reference profile
    # This ensures embedding-based relevance scoring works accurately for custom types
    from services.relevance_service import RelevanceService
    expanded_description = RelevanceService.expand_description(data.name, data.description or data.label)
    
    try:
        sql = "INSERT INTO document_types (project_id, name, label, description, added_by) VALUES (%s, %s, %s, %s, %s)"
        cursor.execute(sql, (project_id, data.name, data.label, expanded_description, current_user["id"]))
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
    reason: str,
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
        
        # 3. Log the deletion details and reason in audit_logs
        import json
        audit_sql = """INSERT INTO audit_logs (project_id, agent_name, action, entity_type, entity_id, details_json) 
                       VALUES (%s, %s, %s, %s, %s, %s)"""
        details = json.dumps({
            "document_name": doc["document_name"],
            "document_type": doc["document_type"],
            "reason": reason,
            "deleted_by_user_id": current_user["id"]
        })
        cursor.execute(audit_sql, (project_id, "SYSTEM", "DELETE_DOCUMENT", "DOCUMENT", document_id, details))
        db.commit()
        
        # 4. Delete document record (cascades automatically to project_activities/new_requests)
        cursor.execute("DELETE FROM documents WHERE id = %s", (document_id,))
        db.commit()
        
        # 5. Remove physical file
        if os.path.exists(doc["storage_key"]):
            os.remove(doc["storage_key"])
            
    except Exception as e:
        cursor.close()
        raise HTTPException(status_code=500, detail=f"Failed to delete document: {e}")
        
    cursor.close()
    return {"success": True, "message": "Document deleted successfully"}
