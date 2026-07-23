# pyrefly: ignore [missing-import]
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from typing import List, Optional
from core.database import get_db
from api.dependencies.auth import get_current_user, require_roles, verify_project_access
from repositories.tracker_repository import TrackerRepository
from repositories.baseline_repository import BaselineRepository
import mysql.connector

# Ensure resolved columns exist in tracker_items table using Repository helper
TrackerRepository.ensure_schema_resolved_columns()

router = APIRouter()

@router.get("/")
def get_tracker_items(project_id: int, current_user: dict = Depends(get_current_user), db: mysql.connector.connection.MySQLConnection = Depends(get_db)):
    verify_project_access(project_id, current_user, db)
    items = TrackerRepository.get_tracker_items(db, project_id)
    return {"success": True, "data": items}

class ResolutionUpdate(BaseModel):
    resolution: str
    status: str

@router.post("/{item_id}/resolve")
def resolve_tracker_item(project_id: int, item_id: int, update: ResolutionUpdate, current_user: dict = Depends(require_roles(["ENGAGEMENT_MANAGER", "PROJECT_LEAD", "PMO_REVIEWER"])), db: mysql.connector.connection.MySQLConnection = Depends(get_db)):
    verify_project_access(project_id, current_user, db)
    
    item = TrackerRepository.get_tracker_item_by_id_and_project(db, item_id, project_id)
    if not item:
        raise HTTPException(status_code=404, detail="Item not found")
        
    TrackerRepository.resolve_item(db, item_id, update.resolution, update.status, current_user["id"])
    
    # If the tracker item is associated with a scope item (reference_id), mark the scope item as COMPLETED
    if item.get("reference_id"):
        BaselineRepository.update_scope_item_completion(db, item["reference_id"], project_id, "COMPLETED")
        
    db.commit()
    
    updated_item = TrackerRepository.get_resolved_item_details(db, item_id)
    return {"success": True, "message": "Tracker item resolved", "data": updated_item}
