# pyrefly: ignore [missing-import]
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
            cursor.execute("DELETE FROM stakeholders WHERE project_id = %s", (project_id,))
        else:
            # Get max version to auto-increment it for the new draft
            cursor.execute("SELECT MAX(version) as max_v FROM scope_baselines WHERE project_id = %s", (project_id,))
            max_v_row = cursor.fetchone()
            next_version = (max_v_row["max_v"] or 0) + 1 if max_v_row else 1
            
            # Create draft baseline
            cursor.execute("INSERT INTO scope_baselines (project_id, status, version) VALUES (%s, 'DRAFT', %s)", (project_id, next_version))
            baseline_id = cursor.lastrowid
            
            # Copy items from latest APPROVED baseline to carry forward historical data
            cursor.execute("SELECT id FROM scope_baselines WHERE project_id = %s AND status = 'APPROVED' ORDER BY id DESC LIMIT 1", (project_id,))
            latest_approved = cursor.fetchone()
            if latest_approved:
                app_baseline_id = latest_approved["id"]
                # Copy scope items
                cursor.execute("""
                    INSERT INTO scope_items (baseline_id, project_id, name, description, scope_type, source_document_id, source_page, source_section, evidence_text, confidence)
                    SELECT %s, project_id, name, description, scope_type, source_document_id, source_page, source_section, evidence_text, confidence
                    FROM scope_items WHERE baseline_id = %s
                """, (baseline_id, app_baseline_id))
                
                # Copy deliverables
                cursor.execute("""
                    INSERT INTO deliverables (baseline_id, project_id, name, description, deadline, owner, source_document_id)
                    SELECT %s, project_id, name, description, deadline, owner, source_document_id
                    FROM deliverables WHERE baseline_id = %s
                """, (baseline_id, app_baseline_id))

            cursor.execute("DELETE FROM stakeholders WHERE project_id = %s", (project_id,))
        
        # Smart Diffing (UPSERT)
        import difflib
        
        cursor.execute("SELECT id, name, scope_type FROM scope_items WHERE baseline_id = %s", (baseline_id,))
        existing_scope_items = cursor.fetchall()
        
        for item in extracted_data.get("scope_items", []):
            item_name = item.get("name", "Unknown")
            item_type = item.get("scope_type", "UNCERTAIN")
            
            existing_item = None
            best_ratio = 0.0
            for db_item in existing_scope_items:
                ratio = difflib.SequenceMatcher(None, item_name.lower(), db_item["name"].lower()).ratio()
                if ratio > 0.8 and ratio > best_ratio:
                    best_ratio = ratio
                    existing_item = db_item
            
            if existing_item:
                status_change_tag = None
                old_type = existing_item["scope_type"]
                if old_type != item_type:
                    status_change_tag = f"Changed from {old_type} to {item_type}"
                    
                sql = """UPDATE scope_items 
                         SET description = %s, scope_type = %s, source_document_id = %s, source_page = %s, 
                             source_section = %s, evidence_text = %s, confidence = %s, status_change_tag = %s
                         WHERE id = %s"""
                cursor.execute(sql, (
                    item.get("description", ""), item_type, document_id, item.get("source_page"),
                    item.get("source_section"), item.get("evidence_text", ""), item.get("confidence", 0.5), status_change_tag, existing_item["id"]
                ))
            else:
                sql = """INSERT INTO scope_items 
                         (baseline_id, project_id, name, description, scope_type, source_document_id, source_page, source_section, evidence_text, confidence)
                         VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"""
                cursor.execute(sql, (
                    baseline_id, project_id, item_name, item.get("description", ""),
                    item_type, document_id, item.get("source_page"),
                    item.get("source_section"), item.get("evidence_text", ""), item.get("confidence", 0.5)
                ))
            
        # UPSERT deliverables
        cursor.execute("SELECT id, name FROM deliverables WHERE baseline_id = %s", (baseline_id,))
        existing_deliverables = cursor.fetchall()
        
        for item in extracted_data.get("deliverables", []):
            item_name = item.get("name", "Unknown")
            deadline = item.get("deadline") if item.get("deadline") else None
            
            existing_deliv = None
            best_ratio = 0.0
            for db_item in existing_deliverables:
                ratio = difflib.SequenceMatcher(None, item_name.lower(), db_item["name"].lower()).ratio()
                if ratio > 0.8 and ratio > best_ratio:
                    best_ratio = ratio
                    existing_deliv = db_item

            
            if existing_deliv:
                sql = """UPDATE deliverables
                         SET description = %s, deadline = %s, owner = %s, source_document_id = %s
                         WHERE id = %s"""
                cursor.execute(sql, (
                    item.get("description", ""), deadline, item.get("owner"), document_id, existing_deliv["id"]
                ))
            else:
                sql = """INSERT INTO deliverables
                         (baseline_id, project_id, name, description, deadline, owner, source_document_id)
                         VALUES (%s, %s, %s, %s, %s, %s, %s)"""
                cursor.execute(sql, (
                    baseline_id, project_id, item_name, item.get("description", ""),
                    deadline, item.get("owner"), document_id
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
    cursor.execute("SELECT * FROM scope_baselines WHERE project_id = %s ORDER BY id DESC LIMIT 1", (project_id,))
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

class ScopeItemCreate(BaseModel):
    name: str
    description: Optional[str] = None
    scope_type: str = "IN_SCOPE"
    evidence_text: Optional[str] = None
    confidence: Optional[float] = 1.0

@router.post("/items")
def add_scope_item(
    project_id: int,
    item: ScopeItemCreate,
    current_user: dict = Depends(require_roles(["ADMIN", "ENGAGEMENT_MANAGER"])),
    db: mysql.connector.connection.MySQLConnection = Depends(get_db)
):
    verify_project_access(project_id, current_user, db)
    cursor = db.cursor(dictionary=True)
    
    cursor.execute("SELECT id FROM scope_baselines WHERE project_id = %s ORDER BY version DESC LIMIT 1", (project_id,))
    baseline = cursor.fetchone()
    if not baseline:
        cursor.execute("INSERT INTO scope_baselines (project_id, status) VALUES (%s, 'DRAFT')", (project_id,))
        db.commit()
        baseline_id = cursor.lastrowid
    else:
        baseline_id = baseline["id"]
        
    sql = """
        INSERT INTO scope_items (baseline_id, project_id, name, description, scope_type, evidence_text, confidence)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
    """
    cursor.execute(sql, (
        baseline_id,
        project_id,
        item.name,
        item.description or "",
        item.scope_type,
        item.evidence_text or "Manually added item",
        item.confidence if item.confidence is not None else 1.0
    ))
    db.commit()
    item_id = cursor.lastrowid
    
    cursor.execute("SELECT * FROM scope_items WHERE id = %s", (item_id,))
    created_item = cursor.fetchone()
    cursor.close()
    
    return {"success": True, "message": "Scope item added successfully", "data": created_item}

@router.delete("/items/{item_id}")
def delete_scope_item(
    project_id: int,
    item_id: int,
    current_user: dict = Depends(require_roles(["ADMIN", "ENGAGEMENT_MANAGER"])),
    db: mysql.connector.connection.MySQLConnection = Depends(get_db)
):
    verify_project_access(project_id, current_user, db)
    cursor = db.cursor(dictionary=True)
    
    cursor.execute("SELECT id FROM scope_items WHERE id = %s AND project_id = %s", (item_id, project_id))
    item = cursor.fetchone()
    if not item:
        cursor.close()
        raise HTTPException(status_code=404, detail="Scope item not found")
        
    cursor.execute("DELETE FROM scope_items WHERE id = %s AND project_id = %s", (item_id, project_id))
    db.commit()
    cursor.close()
    
    return {"success": True, "message": "Scope item deleted successfully"}

