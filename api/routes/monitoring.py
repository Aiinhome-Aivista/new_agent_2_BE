# pyrefly: ignore [missing-import]
from fastapi import APIRouter, Depends, HTTPException, status, Query
# pyrefly: ignore [missing-import]
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
# pyrefly: ignore [missing-import]
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

            if doc["processing_status"] == "PROCESSING":
                yield f'data: {json.dumps({"step": "FAILED", "progress": 0, "status": "failed", "error": "Document is already being processed"})}\n\n'
                return

            if doc["document_type"] not in ["STATUS_REPORT", "MOM"]:
                yield f'data: {json.dumps({"step": "FAILED", "progress": 0, "status": "failed", "error": "Only STATUS_REPORT and MOM can be processed"})}\n\n'
                return

            # removed old emit function from here
            pending_events = []

            # Mark as PROCESSING
            upd = conn.cursor()
            upd.execute(
                "UPDATE documents SET processing_status = 'PROCESSING', processing_progress = 5, processing_step = 'Loading Project Baseline', processing_started_at = NOW() WHERE id = %s",
                (document_id,)
            )
            conn.commit()
            upd.close()

            # ── Initial event ─────────────────────────────────────────────────
            yield f'data: {json.dumps({"step": "Loading Project Baseline", "progress": 5, "status": "running"})}\n\n'

            # Parse the document
            try:
                ext = os.path.splitext(doc["storage_key"])[1].lower()
                chunks = DocumentService.parse_document(doc["storage_key"], ext)
                text = "\n".join([chunk["text"] for chunk in chunks[:8]])
                if len(text) > 8000:
                    text = text[:8000]
            except Exception as parse_err:
                print(f"!!! Document parse error: {parse_err} !!!")
                try:
                    err_conn = get_db_connection()
                    if err_conn:
                        err_cursor = err_conn.cursor()
                        err_cursor.execute(
                            "UPDATE documents SET processing_status = 'FAILED', processing_error = %s, processing_progress = 0, processing_step = 'Failed' WHERE id = %s",
                            (f"Document parsing failed: {str(parse_err)[:400]}", document_id)
                        )
                        err_conn.commit()
                        err_cursor.close()
                        err_conn.close()
                except Exception:
                    pass
                yield f'data: {json.dumps({"step": "FAILED", "progress": 0, "status": "failed", "error": f"Failed to parse document: {str(parse_err)}"})}\n\n'
                return

            yield f'data: {json.dumps({"step": "Reading Uploaded Document", "progress": 12, "status": "running"})}\n\n'

            # ── Run the pipeline with emit callback ───────────────────────────
            import threading

            pipeline_error = [None]
            pipeline_done = [False]

            def run_pipeline():
                thread_conn = get_db_connection()
                if not thread_conn:
                    print("!!! Background pipeline failed to establish db connection !!!")
                    pipeline_done[0] = True
                    return
                def thread_emit(step: str, progress: int):
                    event = json.dumps({"step": step, "progress": progress, "status": "running"})
                    pending_events.append(f"data: {event}\n\n")
                    try:
                        upd_cursor = thread_conn.cursor()
                        upd_cursor.execute(
                            "UPDATE documents SET processing_progress = %s, processing_step = %s WHERE id = %s",
                            (progress, step, document_id)
                        )
                        thread_conn.commit()
                        upd_cursor.close()
                    except Exception as ex:
                        print(f"Failed to update progress in DB: {ex}")

                try:
                    cursor = thread_conn.cursor(dictionary=True)
                    OrchestratorAgent.run_workflow(
                        project_id=project_id,
                        document_id=document_id,
                        text=text,
                        db_cursor=cursor,
                        emit=thread_emit,
                    )
                    upd2 = thread_conn.cursor()
                    upd2.execute(
                        "UPDATE documents SET processing_status = 'COMPLETED', processing_progress = 100, processing_step = 'Completed' WHERE id = %s",
                        (document_id,)
                    )
                    thread_conn.commit()
                    upd2.close()
                    cursor.close()
                except Exception as e:
                    import traceback
                    print("!!! Pipeline execution failed !!!")
                    traceback.print_exc()
                    pipeline_error[0] = str(e)
                    try:
                        thread_conn.rollback()
                    except Exception:
                        pass
                    try:
                        err_conn = get_db_connection()
                        if err_conn:
                            err_cursor = err_conn.cursor()
                            err_cursor.execute(
                                "UPDATE documents SET processing_status = 'FAILED', processing_error = %s, processing_progress = 0, processing_step = 'Failed' WHERE id = %s",
                                (str(e)[:500], document_id)
                            )
                            err_conn.commit()
                            err_cursor.close()
                            err_conn.close()
                    except Exception as db_ex:
                        print("!!! Database update during failure handler failed !!!")
                        traceback.print_exc()
                finally:
                    try:
                        thread_conn.close()
                    except Exception:
                        pass
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
            try:
                fail_conn = get_db_connection()
                if fail_conn:
                    fail_cursor = fail_conn.cursor()
                    fail_cursor.execute(
                        "UPDATE documents SET processing_status = 'FAILED', processing_error = %s, processing_progress = 0, processing_step = 'Failed' WHERE id = %s",
                        (str(e)[:500], document_id)
                    )
                    fail_conn.commit()
                    fail_cursor.close()
                    fail_conn.close()
            except Exception:
                pass
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
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no"
        }
    )


@router.post("/cancel")
def cancel_monitoring_process(
    project_id: int, 
    payload: Optional[dict] = None,
    current_user: dict = Depends(require_roles(["ADMIN", "ENGAGEMENT_MANAGER", "PROJECT_LEAD"])), 
    db: mysql.connector.connection.MySQLConnection = Depends(get_db)
):
    """
    Cancel an ongoing document processing or baseline extraction task,
    mark the document status as FAILED/CANCELLED, and clean up any partial DB records.
    """
    verify_project_access(project_id, current_user, db)
    doc_id = payload.get("document_id") if payload else None
    
    cursor = db.cursor(dictionary=True)
    try:
        # If no specific doc_id provided, find any currently processing or stuck document
        if not doc_id:
            cursor.execute(
                "SELECT id, document_name FROM documents WHERE project_id = %s AND processing_status = 'PROCESSING' ORDER BY id DESC LIMIT 1",
                (project_id,)
            )
            doc = cursor.fetchone()
            if doc:
                doc_id = doc["id"]
        
        if doc_id:
            # 1. Update document status to FAILED
            cursor.execute(
                "UPDATE documents SET processing_status = 'FAILED', processing_error = 'Process stopped by user', processing_progress = 0, processing_step = 'Cancelled' WHERE id = %s AND project_id = %s",
                (doc_id, project_id)
            )
            
            # 2. Clean up any draft scope items if baseline extraction was running
            cursor.execute(
                "DELETE FROM scope_items WHERE project_id = %s AND source_document_id = %s AND is_draft = 1",
                (project_id, doc_id)
            )
            
            # 3. Clean up any unfinalized tracker items created for this document in the last hour
            cursor.execute(
                "DELETE FROM tracker_items WHERE project_id = %s AND source_document_id = %s AND (status = 'DRAFT' OR created_at >= NOW() - INTERVAL 1 HOUR)",
                (project_id, doc_id)
            )
            
            db.commit()
            return {
                "success": True,
                "message": "Process stopped and temporary data cleaned up successfully.",
                "document_id": doc_id
            }
        else:
            return {"success": True, "message": "No active process found to cancel."}
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Failed to cancel process: {e}")
    finally:
        cursor.close()


@router.post("/ingest-status")
def ingest_status_document(
    project_id: int, 
    document_id: int, 
    current_user: dict = Depends(require_roles(["ADMIN", "ENGAGEMENT_MANAGER", "PROJECT_LEAD"])), 
    db: mysql.connector.connection.MySQLConnection = Depends(get_db)
):
    verify_project_access(project_id, current_user, db)
    doc = DocumentRepository.get_document_by_id(db, document_id)
    if not doc or doc['project_id'] != project_id:
        raise HTTPException(status_code=404, detail="Document not found for this project")
        
    if doc['document_type'] not in ['STATUS_REPORT', 'MOM']:
        raise HTTPException(status_code=400, detail="Only STATUS_REPORT or MOM documents can be processed for monitoring")
        
    cursor = db.cursor(dictionary=True)
    try:
        ext = os.path.splitext(doc['storage_key'])[1].lower()
        chunks = DocumentService.parse_document(doc['storage_key'], ext)
        text = "\n".join([chunk["text"] for chunk in chunks[:8]])
        if len(text) > 8000:
            text = text[:8000]
            
        DocumentRepository.update_processing_status(db, document_id, 'PROCESSING')
        db.commit()
        
        OrchestratorAgent.run_workflow(
            project_id=project_id,
            document_id=document_id,
            text=text,
            db_cursor=cursor
        )
        
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
    
    # Auto-fail any document that has been in PROCESSING for more than 10 minutes (stuck/interrupted)
    try:
        cleanup_cursor = db.cursor()
        cleanup_cursor.execute(
            """UPDATE documents 
               SET processing_status = 'FAILED', processing_error = 'Process timed out or server restarted', processing_progress = 0, processing_step = 'Failed'
               WHERE project_id = %s AND processing_status = 'PROCESSING' AND processing_started_at < NOW() - INTERVAL 10 MINUTE""",
            (project_id,)
        )
        db.commit()
        cleanup_cursor.close()
    except Exception as e:
        print(f"Warning: Auto-timeout cleanup query failed: {e}")
    
    if document_id:
        # Get document and compute elapsed seconds since processing started
        cursor = db.cursor(dictionary=True)
        cursor.execute(
            "SELECT *, TIMESTAMPDIFF(SECOND, processing_started_at, NOW()) AS elapsed_seconds FROM documents WHERE id = %s AND project_id = %s",
            (document_id, project_id)
        )
        doc = cursor.fetchone()
        cursor.close()
        if not doc:
            return {"success": True, "data": None}
    else:
        # Find any document that is currently PROCESSING in this project and compute elapsed seconds
        cursor = db.cursor(dictionary=True)
        cursor.execute(
            "SELECT *, TIMESTAMPDIFF(SECOND, processing_started_at, NOW()) AS elapsed_seconds FROM documents WHERE project_id = %s AND processing_status = 'PROCESSING' AND (processing_started_at >= NOW() - INTERVAL 15 MINUTE OR processing_started_at IS NULL) ORDER BY id DESC LIMIT 1",
            (project_id,)
        )
        doc = cursor.fetchone()
        cursor.close()
        if not doc:
            return {"success": True, "data": None}
        document_id = doc["id"]
        
    status_map = {
        "UPLOADED": "pending",
        "PROCESSING": "running",
        "COMPLETED": "completed",
        "FAILED": "failed"
    }
    
    return {
        "success": True,
        "data": {
            "document_id": document_id,
            "document_name": doc["document_name"],
            "document_type": doc.get("document_type", ""),
            "status": status_map.get(doc["processing_status"], "running"),
            "progress": doc.get("processing_progress", 0) if doc.get("processing_progress") is not None else 0,
            "step": doc.get("processing_step", "") if doc.get("processing_step") is not None else "",
            "elapsed_seconds": doc.get("elapsed_seconds", 0) if doc.get("elapsed_seconds") is not None else 0,
            "error": doc.get("processing_error")
        }
    }
