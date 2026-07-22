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
    if document_type in ["EL", "IFA"] and current_user["role"] != "ENGAGEMENT_MANAGER":
        raise HTTPException(status_code=403, detail="Only Engagement Managers are authorized to upload EL or IFA documents.")
        
    ext = os.path.splitext(file.filename)[1].lower()
    if ext not in [".pdf", ".docx", ".txt"]:
        raise HTTPException(status_code=400, detail="Unsupported file format")

    # Move file to permanent project folder
    storage_dir = os.path.join(settings.UPLOAD_PATH, str(project_id))
    os.makedirs(storage_dir, exist_ok=True)
    unique_filename = f"{uuid.uuid4()}{ext}"
    storage_key = os.path.join(storage_dir, unique_filename)
    
    try:
        with open(storage_key, "wb") as f:
            f.write(file.file.read())
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to finalize file storage: {e}")
        
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
        chunks = DocumentService.parse_document(doc["storage_key"], ext)
        
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
        
        # 5. Remove physical file
        if os.path.exists(doc["storage_key"]):
            os.remove(doc["storage_key"])
            
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
        
    file_path = doc["storage_key"]
    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="Physical file not found on disk")
        
    return FileResponse(
        path=file_path,
        filename=doc["document_name"],
        media_type="application/octet-stream"
    )
