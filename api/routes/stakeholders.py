from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from typing import List, Optional
from core.database import get_db
from api.dependencies.auth import get_current_user, require_roles, verify_project_access
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
    cursor = db.cursor(dictionary=True)
    
    # If user_id is not explicitly passed but email is provided, check if user exists in system
    target_user_id = stakeholder.user_id
    if not target_user_id and stakeholder.email:
        cursor.execute("SELECT id FROM users WHERE email = %s", (stakeholder.email,))
        existing_user = cursor.fetchone()
        if existing_user:
            target_user_id = existing_user["id"]
            
    sql = """
        INSERT INTO stakeholders (project_id, name, email, role, responsibility, user_id) 
        VALUES (%s, %s, %s, %s, %s, %s)
    """
    cursor.execute(sql, (
        project_id, 
        stakeholder.name, 
        stakeholder.email, 
        stakeholder.role or "Stakeholder", 
        stakeholder.responsibility,
        target_user_id
    ))
    db.commit()
    stakeholder_id = cursor.lastrowid
    
    # Auto-assign to project_users if linked to a system user
    if target_user_id:
        try:
            cursor.execute(
                "INSERT INTO project_users (project_id, user_id) VALUES (%s, %s)", 
                (project_id, target_user_id)
            )
            db.commit()
        except mysql.connector.IntegrityError:
            pass # Already assigned to project
            
    cursor.execute("SELECT * FROM stakeholders WHERE id = %s", (stakeholder_id,))
    created_member = cursor.fetchone()
    cursor.close()
    
    return {"success": True, "message": "Project member added successfully", "data": created_member}

@router.get("/")
def get_stakeholders(
    project_id: int, 
    current_user: dict = Depends(get_current_user), 
    db: mysql.connector.connection.MySQLConnection = Depends(get_db)
):
    verify_project_access(project_id, current_user, db)
    cursor = db.cursor(dictionary=True)
    cursor.execute("SELECT * FROM stakeholders WHERE project_id = %s ORDER BY created_at DESC", (project_id,))
    stakeholders = cursor.fetchall()
    cursor.close()
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
    cursor = db.cursor(dictionary=True)
    
    cursor.execute("SELECT * FROM stakeholders WHERE id = %s AND project_id = %s", (stakeholder_id, project_id))
    existing = cursor.fetchone()
    if not existing:
        cursor.close()
        raise HTTPException(status_code=404, detail="Project member not found")
        
    update_data = stakeholder.model_dump(exclude_unset=True)
    if not update_data:
        cursor.close()
        return {"success": True, "message": "No changes provided", "data": existing}
        
    # Check if user_id should be updated or resolved by email
    if "email" in update_data and update_data["email"] and "user_id" not in update_data:
        cursor.execute("SELECT id FROM users WHERE email = %s", (update_data["email"],))
        matched_user = cursor.fetchone()
        if matched_user:
            update_data["user_id"] = matched_user["id"]
            
    set_clauses = []
    values = []
    for field, val in update_data.items():
        set_clauses.append(f"{field} = %s")
        values.append(val)
        
    values.extend([stakeholder_id, project_id])
    sql = f"UPDATE stakeholders SET {', '.join(set_clauses)} WHERE id = %s AND project_id = %s"
    cursor.execute(sql, values)
    db.commit()
    
    # If user_id is updated, ensure assignment in project_users
    if update_data.get("user_id"):
        try:
            cursor.execute(
                "INSERT INTO project_users (project_id, user_id) VALUES (%s, %s)",
                (project_id, update_data["user_id"])
            )
            db.commit()
        except mysql.connector.IntegrityError:
            pass
            
    cursor.execute("SELECT * FROM stakeholders WHERE id = %s", (stakeholder_id,))
    updated_member = cursor.fetchone()
    cursor.close()
    
    return {"success": True, "message": "Project member updated successfully", "data": updated_member}

@router.delete("/{stakeholder_id}")
def delete_stakeholder(
    project_id: int,
    stakeholder_id: int,
    current_user: dict = Depends(require_roles(["ADMIN", "ENGAGEMENT_MANAGER"])),
    db: mysql.connector.connection.MySQLConnection = Depends(get_db)
):
    verify_project_access(project_id, current_user, db)
    cursor = db.cursor(dictionary=True)
    
    cursor.execute("SELECT * FROM stakeholders WHERE id = %s AND project_id = %s", (stakeholder_id, project_id))
    existing = cursor.fetchone()
    if not existing:
        cursor.close()
        raise HTTPException(status_code=404, detail="Project member not found")
        
    cursor.execute("DELETE FROM stakeholders WHERE id = %s AND project_id = %s", (stakeholder_id, project_id))
    db.commit()
    cursor.close()
    
    return {"success": True, "message": "Project member removed successfully"}
