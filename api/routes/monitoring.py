# pyrefly: ignore [missing-import]
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from typing import List, Optional
import os
from core.database import get_db
from api.dependencies.auth import get_current_user, require_roles, verify_project_access
from services.document_service import DocumentService
from agents.status_ingestion_agent import StatusIngestionAgent
from agents.orchestrator_agent import OrchestratorAgent
from repositories.document_repository import DocumentRepository
import mysql.connector

router = APIRouter()

@router.post("/process")
def process_monitoring(project_id: int, document_id: int, current_user: dict = Depends(require_roles(["ADMIN", "PROJECT_LEAD"])), db: mysql.connector.connection.MySQLConnection = Depends(get_db)):
    verify_project_access(project_id, current_user, db)
    
    doc = DocumentRepository.get_document(db, document_id, project_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
        
    if doc["document_type"] not in ["STATUS_REPORT", "MOM"]:
        raise HTTPException(status_code=400, detail="Only STATUS_REPORT and MOM can be used for monitoring")
        
    cursor = db.cursor(dictionary=True)
    try:
        DocumentRepository.update_processing_status(db, document_id, 'PROCESSING')
        db.commit()

        ext = os.path.splitext(doc["storage_key"])[1].lower()
        chunks = DocumentService.parse_document(doc["storage_key"], ext)
        text = "\n".join([chunk["text"] for chunk in chunks[:8]])
        if len(text) > 8000:
            text = text[:8000]
            
        OrchestratorAgent.run_workflow(project_id, document_id, text, cursor)
        
        DocumentRepository.update_processing_status(db, document_id, 'COMPLETED')
        db.commit()
        
    except Exception as e:
        db.rollback()
        DocumentRepository.update_processing_status(db, document_id, 'FAILED', str(e))
        db.commit()
        raise HTTPException(status_code=500, detail=f"Status ingestion failed: {e}")
    finally:
        cursor.close()
        
    return {"success": True, "message": "Status ingested successfully"}

@router.get("/progress")
def get_monitoring_progress(project_id: int, document_id: Optional[int] = None, current_user: dict = Depends(require_roles(["ADMIN", "ENGAGEMENT_MANAGER", "PROJECT_LEAD"])), db: mysql.connector.connection.MySQLConnection = Depends(get_db)):
    verify_project_access(project_id, current_user, db)
    if not document_id:
        return {"success": True, "data": None}
    
    doc = DocumentRepository.get_document(db, document_id, project_id)
    if not doc or doc["processing_status"] == "UPLOADED":
        return {"success": True, "data": None}
        
    status_map = {
        "PROCESSING": "running",
        "COMPLETED": "completed",
        "FAILED": "failed"
    }
    
    return {
        "success": True,
        "data": {
            "status": status_map.get(doc["processing_status"], "running"),
            "error": doc.get("processing_error")
        }
    }
