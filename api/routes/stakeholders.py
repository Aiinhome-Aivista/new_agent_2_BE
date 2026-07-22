# pyrefly: ignore [missing-import]
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from typing import List, Optional
from core.database import get_db
from api.dependencies.auth import get_current_user, require_roles, verify_project_access
from repositories.stakeholder_repository import StakeholderRepository
import mysql.connector

router = APIRouter()

class StakeholderCreate(BaseModel):
    name: str
    email: Optional[str] = None
    role: Optional[str] = "Stakeholder"
    responsibility: Optional[str] = None
    user_id: Optional[int] = None

class StakeholderUpdate(BaseModel):
    name: Optional[str] = None
    email: Optional[str] = None
    role: Optional[str] = None
    responsibility: Optional[str] = None
    user_id: Optional[int] = None

@router.post("/")
def add_stakeholder(
    project_id: int, 
    stakeholder: StakeholderCreate, 
    current_user: dict = Depends(require_roles(["ADMIN", "ENGAGEMENT_MANAGER"])), 
    db: mysql.connector.connection.MySQLConnection = Depends(get_db)
):
    verify_project_access(project_id, current_user, db)
    
    # If user_id is not explicitly passed but email is provided, check if user exists in system
    target_user_id = stakeholder.user_id
    if not target_user_id and stakeholder.email:
        target_user_id = StakeholderRepository.get_user_id_by_email(db, stakeholder.email)
            
    stakeholder_id = StakeholderRepository.create_stakeholder(
        db=db,
        project_id=project_id,
        name=stakeholder.name,
        email=stakeholder.email,
        role=stakeholder.role or "Stakeholder",
        responsibility=stakeholder.responsibility,
        user_id=target_user_id
    )
    db.commit()
    
    # Auto-assign to project_users if linked to a system user
    if target_user_id:
        try:
            StakeholderRepository.assign_user_to_project(db, project_id, target_user_id)
            db.commit()
        except mysql.connector.IntegrityError:
            pass # Already assigned to project
            
    created_member = StakeholderRepository.get_stakeholder_by_id(db, stakeholder_id)
    return {"success": True, "message": "Project member added successfully", "data": created_member}

@router.get("/")
def get_stakeholders(
    project_id: int, 
    current_user: dict = Depends(get_current_user), 
    db: mysql.connector.connection.MySQLConnection = Depends(get_db)
):
    verify_project_access(project_id, current_user, db)
    stakeholders = StakeholderRepository.get_stakeholders_by_project(db, project_id)
    return {"success": True, "data": stakeholders}

@router.put("/{stakeholder_id}")
def update_stakeholder(
    project_id: int,
    stakeholder_id: int,
    stakeholder: StakeholderUpdate,
    current_user: dict = Depends(require_roles(["ADMIN", "ENGAGEMENT_MANAGER"])),
    db: mysql.connector.connection.MySQLConnection = Depends(get_db)
):
    verify_project_access(project_id, current_user, db)
    
    existing = StakeholderRepository.get_stakeholder_in_project(db, stakeholder_id, project_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Project member not found")
        
    update_data = stakeholder.model_dump(exclude_unset=True)
    if not update_data:
        return {"success": True, "message": "No changes provided", "data": existing}
        
    # Check if user_id should be updated or resolved by email
    if "email" in update_data and update_data["email"] and "user_id" not in update_data:
        matched_user_id = StakeholderRepository.get_user_id_by_email(db, update_data["email"])
        if matched_user_id:
            update_data["user_id"] = matched_user_id
            
    StakeholderRepository.update_stakeholder_fields(db, stakeholder_id, project_id, update_data)
    db.commit()
    
    # If user_id is updated, ensure assignment in project_users
    if update_data.get("user_id"):
        try:
            StakeholderRepository.assign_user_to_project(db, project_id, update_data["user_id"])
            db.commit()
        except mysql.connector.IntegrityError:
            pass
            
    updated_member = StakeholderRepository.get_stakeholder_by_id(db, stakeholder_id)
    return {"success": True, "message": "Project member updated successfully", "data": updated_member}

@router.delete("/{stakeholder_id}")
def delete_stakeholder(
    project_id: int,
    stakeholder_id: int,
    current_user: dict = Depends(require_roles(["ADMIN", "ENGAGEMENT_MANAGER"])),
    db: mysql.connector.connection.MySQLConnection = Depends(get_db)
):
    verify_project_access(project_id, current_user, db)
    
    existing = StakeholderRepository.get_stakeholder_in_project(db, stakeholder_id, project_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Project member not found")
        
    StakeholderRepository.delete_stakeholder(db, stakeholder_id, project_id)
    db.commit()
    return {"success": True, "message": "Project member removed successfully"}
