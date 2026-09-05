# pyrefly: ignore [missing-import]
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form, status
# pyrefly: ignore [missing-import]
from fastapi.responses import FileResponse
from pydantic import BaseModel
from typing import List
import os
import uuid
import json
from core.config import settings
from core.database import get_db
from api.dependencies.auth import get_current_user, require_roles, verify_project_access
from services.document_service import DocumentService
from services.relevance_service import RelevanceService
from services.rag_service import RAGService
from repositories.document_repository import DocumentRepository
from services.s3_service import S3Service
import tempfile
import mysql.connector

router = APIRouter()

class ConfirmUploadRequest(BaseModel):
    temp_key: str
    document_type: str
    original_name: str

@router.post("/confirm-upload")
def confirm_upload_document(
    project_id: int,
    document_type: str = Form(...),
    file: UploadFile = File(...),
    current_user: dict = Depends(require_roles(["ADMIN", "ENGAGEMENT_MANAGER", "PROJECT_LEAD"])),
    db: mysql.connector.connection.MySQLConnection = Depends(get_db)
):
    verify_project_access(project_id, current_user, db)
    
    cursor = db.cursor(dictionary=True)
    cursor.execute("SELECT project_name, monitoring_status FROM projects WHERE id = %s", (project_id,))
    project = cursor.fetchone()
    cursor.close()
    
    if project and project.get("monitoring_status") == "CLOSED":
        raise HTTPException(status_code=400, detail="Cannot upload documents to a closed project.")

    if document_type in ["EL", "IFA"] and current_user["role"] != "ENGAGEMENT_MANAGER":
        raise HTTPException(status_code=403, detail="Only Engagement Managers are authorized to upload EL or IFA documents.")
        
    import re
    ext = os.path.splitext(file.filename)[1].lower()
    if ext not in [".pdf", ".docx", ".txt"]:
        raise HTTPException(status_code=400, detail="Unsupported file format")

    base_name = os.path.splitext(file.filename)[0]
    safe_base_name = re.sub(r'[^a-zA-Z0-9_\-]', '_', base_name)
    unique_filename = f"{safe_base_name}_{uuid.uuid4().hex[:8]}{ext}"
    project_name = project.get("project_name", f"Project_{project_id}") if project else f"Project_{project_id}"
    
    try:
        storage_key = S3Service.upload_fileobj(file.file, project_id, project_name, unique_filename)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to finalize file storage to S3: {e}")
        
    document_id = DocumentRepository.create_document(
        db=db,
        project_id=project_id,
        document_name=file.filename,
        document_type=document_type,
        storage_key=storage_key,
        uploaded_by=current_user["id"]
    )
    db.commit()
    return {"success": True, "message": "Document uploaded successfully", "data": {"id": document_id}}

@router.get("/")
def get_documents(project_id: int, current_user: dict = Depends(get_current_user), db: mysql.connector.connection.MySQLConnection = Depends(get_db)):
    verify_project_access(project_id, current_user, db)
    docs = DocumentRepository.get_documents_by_project(db, project_id)
    return {"success": True, "data": docs}

class DocumentTypeCreate(BaseModel):
    name: str
    label: str
    description: str = ""

@router.get("/types")
def get_document_types(project_id: int, current_user: dict = Depends(get_current_user), db: mysql.connector.connection.MySQLConnection = Depends(get_db)):
    verify_project_access(project_id, current_user, db)
    types = DocumentRepository.get_master_document_types(db)
    custom_types = DocumentRepository.get_project_document_types(db, project_id)
    return {"success": True, "data": types + custom_types}

@router.post("/types")
def create_document_type(
    project_id: int, 
    data: DocumentTypeCreate, 
    current_user: dict = Depends(require_roles(["ADMIN", "ENGAGEMENT_MANAGER", "PROJECT_LEAD"])), 
    db: mysql.connector.connection.MySQLConnection = Depends(get_db)
):
    verify_project_access(project_id, current_user, db)
    
    # Auto-expand the short description into a rich reference profile
    # This ensures embedding-based relevance scoring works accurately for custom types
    expanded_description = RelevanceService.expand_description(data.name, data.description or data.label)
    
    try:
        DocumentRepository.create_custom_document_type(
            db=db,
            project_id=project_id,
            name=data.name,
            label=data.label,
            description=expanded_description,
            added_by=current_user["id"]
        )
        db.commit()
    except Exception as e:
        raise HTTPException(status_code=400, detail="Failed to create document type")
        
    return {"success": True, "message": "Document type created successfully"}

@router.post("/{document_id}/process")
def process_document(
    project_id: int,
    document_id: int,
    current_user: dict = Depends(require_roles(["ADMIN", "ENGAGEMENT_MANAGER", "PROJECT_LEAD"])),
    db: mysql.connector.connection.MySQLConnection = Depends(get_db)
):
    verify_project_access(project_id, current_user, db)
    doc = DocumentRepository.get_document(db, document_id, project_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
        
    if doc["document_type"] in ["EL", "IFA"] and current_user["role"] != "ENGAGEMENT_MANAGER":
        raise HTTPException(status_code=403, detail="Only Engagement Managers are authorized to process EL or IFA documents.")
        
    try:
        DocumentRepository.update_processing_status(db, document_id, 'PROCESSING')
        db.commit()
        
        ext = os.path.splitext(doc["storage_key"])[1].lower()
        temp_path = os.path.join(tempfile.gettempdir(), f"temp_{uuid.uuid4()}{ext}")
        
        try:
            S3Service.download_to_temp_file(doc["storage_key"], temp_path)
            chunks = DocumentService.parse_document(temp_path, ext)
        finally:
            if os.path.exists(temp_path):
                os.remove(temp_path)
        
        # Index document in ChromaDB and BM25
        RAGService.index_document(project_id, document_id, doc["document_name"], doc["document_type"], chunks)
        
        DocumentRepository.update_processing_status(db, document_id, 'COMPLETED')
        db.commit()
        
    except Exception as e:
        DocumentRepository.update_processing_status(db, document_id, 'FAILED', str(e))
        db.commit()
        raise HTTPException(status_code=500, detail=f"Failed to process document: {e}")
        
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
    doc = DocumentRepository.get_document(db, document_id, project_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
        
    if doc["document_type"] in ["EL", "IFA"] and current_user["role"] != "ENGAGEMENT_MANAGER":
        raise HTTPException(status_code=403, detail="Only Engagement Managers are authorized to delete EL or IFA documents.")
        
    try:
        # 1. Clean up RAG resources if it was completed
        if doc["processing_status"] == "COMPLETED":
            RAGService.delete_document(project_id, document_id)
            
        # 2. Clean up scope items (since there's no ON DELETE CASCADE on source_document_id constraint)
        DocumentRepository.delete_scope_items_by_document(db, document_id)
        db.commit()
        
        # 3. Log the deletion details and reason in audit_logs
        details = json.dumps({
            "document_name": doc["document_name"],
            "document_type": doc["document_type"],
            "reason": reason,
            "deleted_by_user_id": current_user["id"]
        })
        DocumentRepository.log_audit(db, project_id, "SYSTEM", "DELETE_DOCUMENT", "DOCUMENT", document_id, details)
        db.commit()
        
        # 4. Delete document record (cascades automatically to project_activities/new_requests)
        DocumentRepository.delete_document(db, document_id)
        db.commit()
        
        # 5. Remove physical file from S3
        try:
            S3Service.delete_file(doc["storage_key"])
        except Exception as e:
            print(f"Warning: Failed to delete file from S3: {e}")
            
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to delete document: {e}")
        
    return {"success": True, "message": "Document deleted successfully"}

@router.get("/{document_id}/download")
def download_document(
    project_id: int,
    document_id: int,
    current_user: dict = Depends(get_current_user),
    db: mysql.connector.connection.MySQLConnection = Depends(get_db)
):
    verify_project_access(project_id, current_user, db)
    doc = DocumentRepository.get_document(db, document_id, project_id)
    
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
        
    storage_key = doc["storage_key"]
    
    # 1. If stored locally and file exists on disk, serve directly via FileResponse
    if os.path.exists(storage_key):
        # pyrefly: ignore [missing-import]
        from fastapi.responses import FileResponse
        return FileResponse(
            path=storage_key,
            filename=doc.get("document_name", os.path.basename(storage_key)),
            media_type="application/octet-stream"
        )

    # 2. Otherwise in AWS S3 mode, generate presigned URL and redirect
    try:
        presigned_url = S3Service.generate_presigned_url(storage_key)
        # pyrefly: ignore [missing-import]
        from fastapi.responses import RedirectResponse
        return RedirectResponse(url=presigned_url)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to generate download link: {e}")
