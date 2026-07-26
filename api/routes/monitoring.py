# pyrefly: ignore [missing-import]
from fastapi import APIRouter, Depends, HTTPException, status, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from typing import List, Optional
import os
import json
import time
from core.database import get_db, get_db_connection
from core.security import decode_access_token
from api.dependencies.auth import get_current_user, require_roles, verify_project_access
from services.document_service import DocumentService
from agents.status_ingestion_agent import StatusIngestionAgent
from agents.orchestrator_agent import OrchestratorAgent
from repositories.document_repository import DocumentRepository
import mysql.connector

router = APIRouter()


# ─────────────────────────────────────────────────────────────────────────────
# SSE STREAMING ENDPOINT  (replaces the polling pattern)
# EventSource fires this; backend streams progress events in real time.
# Auth: JWT passed as ?token= query param (browser EventSource limitation).
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/stream")
def stream_monitoring(
    project_id: int,
    document_id: int,
    token: str = Query(..., description="JWT access token (EventSource cannot send headers)"),
):
    """
    SSE endpoint that runs the full risk evaluation pipeline and streams
    step-by-step progress events to the client.

    Event format (one per line, separated by blank line):
        data: {"step": "Extracting Activities", "progress": 35, "status": "running"}

    Terminal events:
        data: {"step": "Completed", "progress": 100, "status": "completed"}
        data: {"step": "FAILED", "progress": 0, "status": "failed", "error": "..."}
    """
    # ── Auth via query-param token ────────────────────────────────────────────
    payload = decode_access_token(token)
    if not payload:
        def _unauth():
            yield 'data: {"step": "FAILED", "progress": 0, "status": "failed", "error": "Unauthorized"}\n\n'
        return StreamingResponse(_unauth(), media_type="text/event-stream")

    current_user = {"id": int(payload.get("sub", 0)), "role": payload.get("role", "")}
    allowed = ["ADMIN", "PROJECT_LEAD", "ENGAGEMENT_MANAGER"]
    if current_user["role"] not in allowed:
        def _forbidden():
            yield 'data: {"step": "FAILED", "progress": 0, "status": "failed", "error": "Access denied"}\n\n'
        return StreamingResponse(_forbidden(), media_type="text/event-stream")

    def event_generator():
        conn = get_db_connection()
        if not conn:
            yield f'data: {json.dumps({"step": "FAILED", "progress": 0, "status": "failed", "error": "Database connection failed"})}\n\n'
            return

        try:
            # Verify project access
            cursor_check = conn.cursor(dictionary=True)
            cursor_check.execute(
                "SELECT 1 FROM project_users WHERE project_id = %s AND user_id = %s",
                (project_id, current_user["id"])
            )
            has_access = cursor_check.fetchone() is not None
            cursor_check.close()

            role = current_user["role"]
            if role not in ["ADMIN", "PMO_REVIEWER", "FINANCE_COMMERCIAL"] and not has_access:
                yield f'data: {json.dumps({"step": "FAILED", "progress": 0, "status": "failed", "error": "Not assigned to this project"})}\n\n'
                return

            # Load document
            doc_cursor = conn.cursor(dictionary=True)
            doc_cursor.execute(
                "SELECT * FROM documents WHERE id = %s AND project_id = %s",
                (document_id, project_id)
            )
            doc = doc_cursor.fetchone()
            doc_cursor.close()

            if not doc:
                yield f'data: {json.dumps({"step": "FAILED", "progress": 0, "status": "failed", "error": "Document not found"})}\n\n'
                return

            if doc["document_type"] not in ["STATUS_REPORT", "MOM"]:
                yield f'data: {json.dumps({"step": "FAILED", "progress": 0, "status": "failed", "error": "Only STATUS_REPORT and MOM can be processed"})}\n\n'
                return

            # ── Emit helper ───────────────────────────────────────────────────
            def emit(step: str, progress: int):
                event = json.dumps({"step": step, "progress": progress, "status": "running"})
                # Note: yield inside nested function won't work directly.
                # We use a queue-based approach via a list.
                pending_events.append(f"data: {event}\n\n")

            pending_events = []

            # Mark as PROCESSING
            upd = conn.cursor()
            upd.execute(
                "UPDATE documents SET processing_status = 'PROCESSING' WHERE id = %s",
                (document_id,)
            )
            conn.commit()
            upd.close()

            # ── Initial event ─────────────────────────────────────────────────
            yield f'data: {json.dumps({"step": "Loading Project Baseline", "progress": 5, "status": "running"})}\n\n'

            # Parse the document
            ext = os.path.splitext(doc["storage_key"])[1].lower()
            chunks = DocumentService.parse_document(doc["storage_key"], ext)
            text = "\n".join([chunk["text"] for chunk in chunks[:8]])
            if len(text) > 8000:
                text = text[:8000]

            yield f'data: {json.dumps({"step": "Reading Uploaded Document", "progress": 12, "status": "running"})}\n\n'

            # ── Run the pipeline with emit callback ───────────────────────────
            import threading

            pipeline_error = [None]
            pipeline_done = [False]

            def run_pipeline():
                try:
                    cursor = conn.cursor(dictionary=True)
                    OrchestratorAgent.run_workflow(
                        project_id=project_id,
                        document_id=document_id,
                        text=text,
                        db_cursor=cursor,
                        emit=emit,
                    )
                    upd2 = conn.cursor()
                    upd2.execute(
                        "UPDATE documents SET processing_status = 'COMPLETED' WHERE id = %s",
                        (document_id,)
                    )
                    conn.commit()
                    upd2.close()
                    cursor.close()
                except Exception as e:
                    pipeline_error[0] = str(e)
                    try:
                        conn.rollback()
                        fail = conn.cursor()
                        fail.execute(
                            "UPDATE documents SET processing_status = 'FAILED', processing_error = %s WHERE id = %s",
                            (str(e)[:500], document_id)
                        )
                        conn.commit()
                        fail.close()
                    except Exception:
                        pass
                finally:
                    pipeline_done[0] = True

            thread = threading.Thread(target=run_pipeline, daemon=True)
            thread.start()

            # ── Flush pending events while pipeline runs ──────────────────────
            while not pipeline_done[0]:
                while pending_events:
                    yield pending_events.pop(0)
                time.sleep(0.3)

            # Flush any remaining events after thread finishes
            while pending_events:
                yield pending_events.pop(0)

            # ── Terminal event ────────────────────────────────────────────────
            if pipeline_error[0]:
                yield f'data: {json.dumps({"step": "FAILED", "progress": 0, "status": "failed", "error": pipeline_error[0]})}\n\n'
            else:
                yield f'data: {json.dumps({"step": "Completed", "progress": 100, "status": "completed"})}\n\n'

        except Exception as e:
            yield f'data: {json.dumps({"step": "FAILED", "progress": 0, "status": "failed", "error": str(e)})}\n\n'
        finally:
            try:
                conn.close()
            except Exception:
                pass

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",   # Disable nginx buffering
            "Connection": "keep-alive",
        }
    )


# ─────────────────────────────────────────────────────────────────────────────
# LEGACY ENDPOINTS (kept for backward compatibility)
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/process")
def process_monitoring(project_id: int, document_id: int, current_user: dict = Depends(require_roles(["ADMIN", "PROJECT_LEAD", "ENGAGEMENT_MANAGER"])), db: mysql.connector.connection.MySQLConnection = Depends(get_db)):
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
