# pyrefly: ignore [missing-import]
from fastapi import APIRouter, Depends, HTTPException, status, BackgroundTasks
from pydantic import BaseModel
from typing import List, Optional
import os
import traceback
from core.database import get_db, get_db_connection
from core.progress import ProgressManager
from api.dependencies.auth import get_current_user, require_roles, verify_project_access
from services.document_service import DocumentService
from services.rag_service import RAGService
from agents.status_ingestion_agent import StatusIngestionAgent
from agents.orchestrator_agent import OrchestratorAgent
from repositories.document_repository import DocumentRepository
import mysql.connector

router = APIRouter()

def run_evaluation_async(project_id: int, document_id: int, storage_key: str, document_name: str, document_type: str):
    def progress_callback(stage: str, progress: int, status: str, details: dict = None, error: str = None):
        ProgressManager.set_progress(
            project_id=project_id,
            document_id=document_id,
            stage=stage,
            progress=progress,
            status=status,
            details=details,
            error=error
        )

    # 1. Loading Project Baseline
    progress_callback("Loading Project Baseline", 10, "running")

    db = get_db_connection()
    if not db:
        progress_callback("Loading Project Baseline", 10, "failed", error="Database connection failed")
        return

    cursor = db.cursor(dictionary=True)
    try:
        # 2. Reading Uploaded Document
        progress_callback("Reading Uploaded Document", 20, "running")
        
        cursor.execute("SELECT processing_status FROM documents WHERE id = %s", (document_id,))
        doc_status = cursor.fetchone()
        
        ext = os.path.splitext(storage_key)[1].lower()
        chunks = DocumentService.parse_document(storage_key, ext)
        
        if not doc_status or doc_status.get("processing_status") != 'COMPLETED':
            cursor.execute("UPDATE documents SET processing_status = 'PROCESSING' WHERE id = %s", (document_id,))
            db.commit()
            
            RAGService.index_document(project_id, document_id, document_name, document_type, chunks)
            
            cursor.execute("UPDATE documents SET processing_status = 'COMPLETED' WHERE id = %s", (document_id,))
            db.commit()

        # 3. Extracting Activities
        progress_callback("Extracting Activities", 30, "running")
        
        text = "\n".join([chunk["text"] for chunk in chunks[:8]])
        if len(text) > 8000:
            text = text[:8000]

        OrchestratorAgent.run_workflow(project_id, document_id, text, cursor, progress_callback=progress_callback)
        db.commit()

        # 10. Completed (final update)
        progress_callback("Completed", 100, "completed")

    except Exception as e:
        db.rollback()
        error_msg = str(e)
        print(f"Error in async evaluation: {error_msg}")
        traceback.print_exc()
        progress_callback("Completed", 100, "failed", error=error_msg)
        try:
            cursor.execute("UPDATE documents SET processing_status = 'FAILED', processing_error = %s WHERE id = %s", (error_msg[:500], document_id))
            db.commit()
        except Exception:
            pass
    finally:
        cursor.close()
        db.close()

@router.post("/process")
def process_monitoring(
    project_id: int, 
    document_id: int, 
    background_tasks: BackgroundTasks,
    current_user: dict = Depends(require_roles(["ADMIN", "PROJECT_LEAD"])), 
    db: mysql.connector.connection.MySQLConnection = Depends(get_db)
):
    verify_project_access(project_id, current_user, db)
    
    doc = DocumentRepository.get_document(db, document_id, project_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
        
    if doc["document_type"] not in ["STATUS_REPORT", "MOM"]:
        raise HTTPException(status_code=400, detail="Only STATUS_REPORT and MOM can be used for monitoring")
        
    # Start background task
    background_tasks.add_task(
        run_evaluation_async,
        project_id,
        document_id,
        doc["storage_key"],
        doc["document_name"],
        doc["document_type"]
    )
    
    return {"success": True, "message": "Evaluation started in the background"}

@router.get("/progress")
def get_evaluation_progress(
    project_id: int,
    document_id: Optional[int] = None,
    current_user: dict = Depends(get_current_user),
    db: mysql.connector.connection.MySQLConnection = Depends(get_db)
):
    verify_project_access(project_id, current_user, db)
    
    if document_id:
        progress = ProgressManager.get_progress(project_id, document_id)
        if progress:
            return {"success": True, "data": progress}
            
    active_progress = ProgressManager.get_active_progress_for_project(project_id)
    if active_progress:
        return {"success": True, "data": active_progress}
        
    # Database status check fallback
    cursor = db.cursor(dictionary=True)
    try:
        cursor.execute("""
            SELECT id, document_name, document_type 
            FROM documents 
            WHERE project_id = %s AND processing_status = 'PROCESSING'
            ORDER BY id DESC LIMIT 1
        """, (project_id,))
        doc = cursor.fetchone()
        if doc:
            return {
                "success": True,
                "data": {
                    "currentStage": "Reading Uploaded Document",
                    "progress": 20,
                    "status": "running",
                    "document_id": doc["id"]
                }
            }
    finally:
        cursor.close()
        
    return {"success": True, "data": None}
