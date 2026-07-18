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
        # For POC, read the document file and get first 8k chars
        with open(doc["storage_key"], "r", encoding="utf-8", errors="ignore") as f:
            text = f.read(8000)
            
        extracted_data = StatusIngestionAgent.extract_status(text)
        
        # Insert activities and evaluate risk
        from agents.reconciliation_agent import ReconciliationAgent
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
            
            # Reconcile Risk
            risk_assessment = ReconciliationAgent.evaluate_risk(project_id, item)
            
            tracker_sql = """INSERT INTO tracker_items 
                             (project_id, source_document_id, item_type, reference_id, is_out_of_scope, risk_score, confidence, reasoning, requires_escalation, status)
                             VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, 'OPEN')"""
            cursor.execute(tracker_sql, (
                project_id, document_id, "ACTIVITY", activity_id, 
                risk_assessment.get("is_out_of_scope", False),
                risk_assessment.get("risk_score", 0.0),
                risk_assessment.get("confidence", 0.5),
                risk_assessment.get("reasoning", ""),
                risk_assessment.get("requires_escalation", False)
            ))
            
        # Insert new requests and evaluate risk
        for item in extracted_data.get("new_requests", []):
            sql = """INSERT INTO new_requests
                     (project_id, document_id, request_name, requested_by, request_status, source_page, evidence_text)
                     VALUES (%s, %s, %s, %s, 'DETECTED', %s, %s)"""
            cursor.execute(sql, (
                project_id, document_id, item.get("request_name", "Unknown"), item.get("requested_by"),
                item.get("source_page"), item.get("evidence_text", "")
            ))
            request_id = cursor.lastrowid
            
            # Reconcile Risk
            risk_assessment = ReconciliationAgent.evaluate_risk(project_id, item)
            
            tracker_sql = """INSERT INTO tracker_items 
                             (project_id, source_document_id, item_type, reference_id, is_out_of_scope, risk_score, confidence, reasoning, requires_escalation, status)
                             VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, 'OPEN')"""
            cursor.execute(tracker_sql, (
                project_id, document_id, "NEW_REQUEST", request_id, 
                risk_assessment.get("is_out_of_scope", True), # Usually new requests might be OOS
                risk_assessment.get("risk_score", 1.0),
                risk_assessment.get("confidence", 0.5),
                risk_assessment.get("reasoning", ""),
                risk_assessment.get("requires_escalation", True)
            ))
            
            if risk_assessment.get("requires_escalation") or risk_assessment.get("risk_score", 0.0) >= 0.8:
                # Fetch stakeholders to alert
                cursor.execute("SELECT email, role FROM stakeholders WHERE project_id = %s", (project_id,))
                stakeholders = cursor.fetchall()
                from services.alert_service import AlertService
                AlertService.alert_high_risk(
                    project_id, 
                    item.get("request_name", "Unknown Request"), 
                    risk_assessment.get("reasoning", "High risk detected"), 
                    stakeholders
                )
            
        db.commit()
        
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Status ingestion failed: {e}")
    finally:
        cursor.close()
        
    return {"success": True, "message": "Monitoring document processed"}
