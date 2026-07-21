from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from typing import List, Optional
from core.database import get_db_connection, get_db
from api.dependencies.auth import get_current_user, require_roles, verify_project_access
import mysql.connector

# Ensure resolved columns exist in tracker_items table
try:
    _conn = get_db_connection()
    if _conn:
        _cursor = _conn.cursor()
        try:
            _cursor.execute("ALTER TABLE tracker_items ADD COLUMN resolved_by BIGINT NULL;")
            _conn.commit()
        except Exception:
            pass
        try:
            _cursor.execute("ALTER TABLE tracker_items ADD COLUMN resolved_at TIMESTAMP NULL;")
            _conn.commit()
        except Exception:
            pass
        _cursor.close()
        _conn.close()
except Exception as _e:
    print(f"Tracker table auto-migration warning: {_e}")

router = APIRouter()

@router.get("/")
def get_tracker_items(project_id: int, current_user: dict = Depends(get_current_user), db: mysql.connector.connection.MySQLConnection = Depends(get_db)):
    verify_project_access(project_id, current_user, db)
    cursor = db.cursor(dictionary=True)
    cursor.execute("""
        SELECT ti.*, 
               CASE 
                 WHEN ti.item_type = 'ACTIVITY' THEN pa.activity_name 
                 WHEN ti.item_type = 'NEW_REQUEST' THEN nr.request_name
                 ELSE CONCAT(REPLACE(ti.item_type, '_', ' '), ' #', ti.id)
               END as name,
               d.document_name,
               u.name as resolved_by_name,
               u.email as resolved_by_email
        FROM tracker_items ti
        LEFT JOIN project_activities pa ON ti.item_type = 'ACTIVITY' AND ti.reference_id = pa.id
        LEFT JOIN new_requests nr ON ti.item_type = 'NEW_REQUEST' AND ti.reference_id = nr.id
        LEFT JOIN documents d ON ti.source_document_id = d.id
        LEFT JOIN users u ON ti.resolved_by = u.id
        WHERE ti.project_id = %s
        ORDER BY ti.risk_score DESC
    """, (project_id,))
    items = cursor.fetchall()
    cursor.close()
    return {"success": True, "data": items}

class ResolutionUpdate(BaseModel):
    resolution: str
    status: str

@router.post("/{item_id}/resolve")
def resolve_tracker_item(project_id: int, item_id: int, update: ResolutionUpdate, current_user: dict = Depends(require_roles(["ENGAGEMENT_MANAGER", "PROJECT_LEAD", "PMO_REVIEWER"])), db: mysql.connector.connection.MySQLConnection = Depends(get_db)):
    verify_project_access(project_id, current_user, db)
    cursor = db.cursor(dictionary=True)
    
    cursor.execute("SELECT * FROM tracker_items WHERE id = %s AND project_id = %s", (item_id, project_id))
    if not cursor.fetchone():
        raise HTTPException(status_code=404, detail="Item not found")
        
    cursor.execute("""
        UPDATE tracker_items 
        SET resolution = %s, status = %s, resolved_by = %s, resolved_at = NOW() 
        WHERE id = %s
    """, (update.resolution, update.status, current_user["id"], item_id))
    db.commit()
    
    cursor.execute("""
        SELECT ti.*, 
               u.name as resolved_by_name,
               u.email as resolved_by_email
        FROM tracker_items ti
        LEFT JOIN users u ON ti.resolved_by = u.id
        WHERE ti.id = %s
    """, (item_id,))
    updated_item = cursor.fetchone()
    
    cursor.close()
    return {"success": True, "message": "Tracker item resolved", "data": updated_item}
