# pyrefly: ignore [missing-import]
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from typing import List, Optional
from core.database import get_db
from api.dependencies.auth import get_current_user, require_roles, verify_project_access
from agents.status_ingestion_agent import StatusIngestionAgent
import mysql.connector

router = APIRouter()

@router.post("/process")
def process_monitoring(project_id: int, document_id: int, current_user: dict = Depends(require_roles(["ADMIN", "PROJECT_LEAD"])), db: mysql.connector.connection.MySQLConnection = Depends(get_db)):
    verify_project_access(project_id, current_user, db)
    cursor = db.cursor(dictionary=True)
    
    cursor.execute("SELECT * FROM documents WHERE id = %s AND project_id = %s", (document_id, project_id))
    doc = cursor.fetchone()
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
        
    if doc["document_type"] not in ["STATUS_REPORT", "MOM"]:
        raise HTTPException(status_code=400, detail="Only STATUS_REPORT and MOM can be used for monitoring")
        
    try:
        from services.document_service import DocumentService
        import os
        
        ext = os.path.splitext(doc["storage_key"])[1].lower()
        chunks = DocumentService.parse_document(doc["storage_key"], ext)
        text = "\n".join([chunk["text"] for chunk in chunks[:8]])
        if len(text) > 8000:
            text = text[:8000]
            
        from agents.orchestrator_agent import OrchestratorAgent
        OrchestratorAgent.run_workflow(project_id, document_id, text, cursor)
            
        db.commit()
        
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Status ingestion failed: {e}")
    finally:
        cursor.close()
        
    return {"success": True, "message": "Monitoring document processed"}
