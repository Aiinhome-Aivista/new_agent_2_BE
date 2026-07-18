from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from typing import List, Optional
from core.database import get_db
from api.dependencies.auth import get_current_user, require_roles
import mysql.connector

router = APIRouter()

@router.get("/")
def get_tracker_items(project_id: int, current_user: dict = Depends(get_current_user), db: mysql.connector.connection.MySQLConnection = Depends(get_db)):
    cursor = db.cursor(dictionary=True)
    cursor.execute("""
        SELECT ti.*, 
               CASE WHEN ti.item_type = 'ACTIVITY' THEN pa.activity_name ELSE nr.request_name END as name,
               d.document_name
        FROM tracker_items ti
        LEFT JOIN project_activities pa ON ti.item_type = 'ACTIVITY' AND ti.reference_id = pa.id
        LEFT JOIN new_requests nr ON ti.item_type = 'NEW_REQUEST' AND ti.reference_id = nr.id
        LEFT JOIN documents d ON ti.source_document_id = d.id
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
def resolve_tracker_item(project_id: int, item_id: int, update: ResolutionUpdate, current_user: dict = Depends(require_roles(["ADMIN", "ENGAGEMENT_MANAGER", "PROJECT_LEAD", "PMO_REVIEWER"])), db: mysql.connector.connection.MySQLConnection = Depends(get_db)):
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
    cursor.close()
    return {"success": True, "message": "Tracker item resolved"}
