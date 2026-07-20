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
            
        extracted_data = StatusIngestionAgent.extract_status(text)
        
        from agents.reconciliation_agent import ReconciliationAgent
        
        # --- Helper to insert a tracker item ---
        def insert_tracker_item(item_type: str, reference_id: int, risk_assessment: dict):
            tracker_sql = """INSERT INTO tracker_items 
                             (project_id, source_document_id, item_type, reference_id, 
                              is_out_of_scope, risk_score, risk_level, risk_category, 
                              confidence, reasoning, requires_escalation, status)
                             VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'OPEN')"""
            
            # Build reasoning that includes the description (reason)
            description = risk_assessment.get("description", "")
            reasoning = risk_assessment.get("reasoning", "")
            full_reasoning = f"{description}\n\n{reasoning}" if description and reasoning else (description or reasoning)
            
            cursor.execute(tracker_sql, (
                project_id, document_id, item_type, reference_id,
                risk_assessment.get("is_out_of_scope", False),
                int(risk_assessment.get("risk_score", 0)),
                risk_assessment.get("risk_level", "LOW"),
                risk_assessment.get("risk_category", "GENERAL"),
                risk_assessment.get("confidence", 0.5),
                full_reasoning,
                risk_assessment.get("requires_escalation", False)
            ))
            
            # Alert on high risk
            if risk_assessment.get("requires_escalation") or int(risk_assessment.get("risk_score", 0)) >= 70:
                cursor.execute("SELECT email, role FROM stakeholders WHERE project_id = %s", (project_id,))
                stakeholders = cursor.fetchall()
                if stakeholders:
                    from services.alert_service import AlertService
                    item_name = risk_assessment.get("description", "Unknown item")[:100]
                    AlertService.alert_high_risk(
                        project_id,
                        item_name,
                        full_reasoning,
                        stakeholders
                    )
        
        # --- 1. Process Activities ---
        for item in extracted_data.get("activities", []):
            sql = """INSERT INTO project_activities 
                     (project_id, document_id, activity_name, description, activity_status, progress_percentage, requested_by, owner, mentioned_deadline, source_page, source_section, evidence_text, confidence)
                     VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"""
            cursor.execute(sql, (
                project_id, document_id, item.get("activity_name", "Unknown"), item.get("description", ""),
                item.get("activity_status", "UNKNOWN"), item.get("progress_percentage"), item.get("requested_by"),
                item.get("owner"), item.get("mentioned_deadline"), item.get("source_page"),
                item.get("source_section"), item.get("evidence_text", ""), item.get("confidence", 0.5)
            ))
            activity_id = cursor.lastrowid
            
            risk_assessment = ReconciliationAgent.evaluate_risk(project_id, item, "ACTIVITY")
            insert_tracker_item("ACTIVITY", activity_id, risk_assessment)
            
        # --- 2. Process New Requests ---
        for item in extracted_data.get("new_requests", []):
            sql = """INSERT INTO new_requests
                     (project_id, document_id, request_name, requested_by, request_status, source_page, evidence_text)
                     VALUES (%s, %s, %s, %s, 'DETECTED', %s, %s)"""
            cursor.execute(sql, (
                project_id, document_id, item.get("request_name", "Unknown"), item.get("requested_by"),
                item.get("source_page"), item.get("evidence_text", "")
            ))
            request_id = cursor.lastrowid
            
            risk_assessment = ReconciliationAgent.evaluate_risk(project_id, item, "NEW_REQUEST")
            insert_tracker_item("NEW_REQUEST", request_id, risk_assessment)
        
        # --- 3. Process Blockers ---
        for item in extracted_data.get("blockers", []):
            risk_assessment = ReconciliationAgent.evaluate_risk(project_id, item, "BLOCKER")
            insert_tracker_item("BLOCKER", 0, risk_assessment)
        
        # --- 4. Process Action Items ---
        for item in extracted_data.get("action_items", []):
            risk_assessment = ReconciliationAgent.evaluate_risk(project_id, item, "ACTION_ITEM")
            insert_tracker_item("ACTION_ITEM", 0, risk_assessment)
        
        # --- 5. Process Decisions ---
        for item in extracted_data.get("decisions", []):
            risk_assessment = ReconciliationAgent.evaluate_risk(project_id, item, "DECISION")
            insert_tracker_item("DECISION", 0, risk_assessment)
        
        # --- 6. Process Risks Mentioned ---
        for item in extracted_data.get("risks_mentioned", []):
            risk_assessment = ReconciliationAgent.evaluate_risk(project_id, item, "RISK_MENTIONED")
            insert_tracker_item("RISK_MENTIONED", 0, risk_assessment)
            
        db.commit()
        
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Status ingestion failed: {e}")
    finally:
        cursor.close()
        
    return {"success": True, "message": "Monitoring document processed"}
