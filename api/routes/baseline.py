from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from typing import List, Optional
from core.database import get_db
from api.dependencies.auth import get_current_user, require_roles, verify_project_access
from agents.scope_extraction_agent import ScopeExtractionAgent
import mysql.connector

router = APIRouter()

@router.post("/extract")
def extract_baseline(project_id: int, document_id: int, current_user: dict = Depends(require_roles(["ADMIN", "ENGAGEMENT_MANAGER"])), db: mysql.connector.connection.MySQLConnection = Depends(get_db)):
    verify_project_access(project_id, current_user, db)
    cursor = db.cursor(dictionary=True)
    
    cursor.execute("SELECT * FROM documents WHERE id = %s AND project_id = %s", (document_id, project_id))
    doc = cursor.fetchone()
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
        
    if doc["document_type"] not in ["EL", "IFA"]:
        raise HTTPException(status_code=400, detail="Only EL and IFA can be used for baseline extraction")
        
    try:
        from services.document_service import DocumentService
        import os
        
        ext = os.path.splitext(doc["storage_key"])[1].lower()
        chunks = DocumentService.parse_document(doc["storage_key"], ext)
        text = "\n".join([chunk["text"] for chunk in chunks[:8]])
        if len(text) > 8000:
            text = text[:8000]
            
        extracted_data = ScopeExtractionAgent.extract_scope(text)
        
        # Check if there is an existing DRAFT baseline for the project
        cursor.execute("SELECT id FROM scope_baselines WHERE project_id = %s AND status = 'DRAFT' ORDER BY id DESC LIMIT 1", (project_id,))
        existing_draft = cursor.fetchone()
        if existing_draft:
            baseline_id = existing_draft["id"]
            # Clear previous items from this specific file under this draft
            cursor.execute("DELETE FROM scope_items WHERE baseline_id = %s AND source_document_id = %s", (baseline_id, document_id))
            cursor.execute("DELETE FROM deliverables WHERE baseline_id = %s AND source_document_id = %s", (baseline_id, document_id))
            cursor.execute("DELETE FROM stakeholders WHERE project_id = %s", (project_id,))
        else:
            # Create draft baseline
            cursor.execute("INSERT INTO scope_baselines (project_id, status) VALUES (%s, 'DRAFT')", (project_id,))
            baseline_id = cursor.lastrowid
            cursor.execute("DELETE FROM stakeholders WHERE project_id = %s", (project_id,))
        
        # Insert scope items
        for item in extracted_data.get("scope_items", []):
            sql = """INSERT INTO scope_items 
                     (baseline_id, project_id, name, description, scope_type, source_document_id, source_page, source_section, evidence_text, confidence)
                     VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"""
            cursor.execute(sql, (
                baseline_id, project_id, item.get("name", "Unknown"), item.get("description", ""),
                item.get("scope_type", "UNCERTAIN"), document_id, item.get("source_page"),
                item.get("source_section"), item.get("evidence_text", ""), item.get("confidence", 0.5)
            ))
            
        # Insert deliverables
        for item in extracted_data.get("deliverables", []):
            sql = """INSERT INTO deliverables
                     (baseline_id, project_id, name, description, deadline, owner, source_document_id)
                     VALUES (%s, %s, %s, %s, %s, %s, %s)"""
            cursor.execute(sql, (
                baseline_id, project_id, item.get("name", "Unknown"), item.get("description", ""),
                item.get("deadline") if item.get("deadline") else None, item.get("owner"), document_id
            ))
            
        # Insert stakeholders
        for stakeholder in extracted_data.get("stakeholders", []):
            sql = """INSERT INTO stakeholders (project_id, name, email, role, responsibility)
                     VALUES (%s, %s, %s, %s, %s)"""
            cursor.execute(sql, (
                project_id, stakeholder.get("name", "Unknown"), stakeholder.get("email"),
                stakeholder.get("role"), stakeholder.get("responsibility")
            ))
            
        # Update project status
        cursor.execute("UPDATE projects SET monitoring_status = 'BASELINE_PENDING_REVIEW' WHERE id = %s", (project_id,))
        db.commit()
        
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Baseline extraction failed: {e}")
    finally:
        cursor.close()
        
    return {"success": True, "message": "Draft baseline extracted", "data": {"baseline_id": baseline_id}}

@router.get("/")
def get_baseline(project_id: int, current_user: dict = Depends(get_current_user), db: mysql.connector.connection.MySQLConnection = Depends(get_db)):
    verify_project_access(project_id, current_user, db)
    cursor = db.cursor(dictionary=True)
    cursor.execute("SELECT * FROM scope_baselines WHERE project_id = %s ORDER BY version DESC LIMIT 1", (project_id,))
    baseline = cursor.fetchone()
    
    if not baseline:
        return {"success": True, "data": None}
        
    cursor.execute("SELECT * FROM scope_items WHERE baseline_id = %s", (baseline["id"],))
    scope_items = cursor.fetchall()
    
    cursor.execute("SELECT * FROM deliverables WHERE baseline_id = %s", (baseline["id"],))
    deliverables = cursor.fetchall()
    
    baseline["scope_items"] = scope_items
    baseline["deliverables"] = deliverables
    
    cursor.close()
    return {"success": True, "data": baseline}

@router.post("/approve")
def approve_baseline(project_id: int, current_user: dict = Depends(require_roles(["ENGAGEMENT_MANAGER", "ADMIN"])), db: mysql.connector.connection.MySQLConnection = Depends(get_db)):
    verify_project_access(project_id, current_user, db)
    cursor = db.cursor(dictionary=True)
    
    cursor.execute("SELECT id FROM scope_baselines WHERE project_id = %s AND status = 'DRAFT' ORDER BY id DESC LIMIT 1", (project_id,))
    baseline = cursor.fetchone()
    if not baseline:
        raise HTTPException(status_code=404, detail="No draft baseline found")
        
    cursor.execute("UPDATE scope_baselines SET status = 'APPROVED', approved_by = %s, approved_at = NOW() WHERE id = %s", (current_user["id"], baseline["id"]))
    cursor.execute("UPDATE projects SET monitoring_status = 'ACTIVE' WHERE id = %s", (project_id,))
    
    db.commit()
    cursor.close()
    
    return {"success": True, "message": "Baseline approved. Project is now ACTIVE."}
