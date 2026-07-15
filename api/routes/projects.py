from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from typing import List, Optional
from core.database import get_db
from api.dependencies.auth import get_current_user, require_roles
import mysql.connector

router = APIRouter()

class ProjectCreate(BaseModel):
    project_name: str
    client_name: Optional[str] = None
    description: Optional[str] = None

class ProjectUpdate(BaseModel):
    project_name: Optional[str]
    client_name: Optional[str]
    description: Optional[str]
    monitoring_status: Optional[str]

@router.post("/")
def create_project(project: ProjectCreate, current_user: dict = Depends(require_roles(["ADMIN", "ENGAGEMENT_MANAGER"])), db: mysql.connector.connection.MySQLConnection = Depends(get_db)):
    cursor = db.cursor(dictionary=True)
    sql = "INSERT INTO projects (project_name, client_name, description, created_by) VALUES (%s, %s, %s, %s)"
    cursor.execute(sql, (project.project_name, project.client_name, project.description, current_user["id"]))
    db.commit()
    project_id = cursor.lastrowid
    
    # Auto assign creator to project
    cursor.execute("INSERT INTO project_users (project_id, user_id) VALUES (%s, %s)", (project_id, current_user["id"]))
    db.commit()
    cursor.close()
    
    return {"success": True, "message": "Project created successfully", "data": {"id": project_id}}

@router.get("/")
def get_projects(current_user: dict = Depends(get_current_user), db: mysql.connector.connection.MySQLConnection = Depends(get_db)):
    cursor = db.cursor(dictionary=True)
    if current_user["role"] == "ADMIN":
        cursor.execute("SELECT * FROM projects")
    else:
        cursor.execute("""
            SELECT p.* FROM projects p
            JOIN project_users pu ON p.id = pu.project_id
            WHERE pu.user_id = %s
        """, (current_user["id"],))
    projects = cursor.fetchall()
    cursor.close()
    return {"success": True, "data": projects}

@router.get("/{project_id}")
def get_project(project_id: int, current_user: dict = Depends(get_current_user), db: mysql.connector.connection.MySQLConnection = Depends(get_db)):
    cursor = db.cursor(dictionary=True)
    if current_user["role"] != "ADMIN":
        cursor.execute("SELECT 1 FROM project_users WHERE project_id = %s AND user_id = %s", (project_id, current_user["id"]))
        if not cursor.fetchone():
            raise HTTPException(status_code=403, detail="Not assigned to this project")
            
    cursor.execute("SELECT * FROM projects WHERE id = %s", (project_id,))
    project = cursor.fetchone()
    cursor.close()
    
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    return {"success": True, "data": project}

class ProjectUserAdd(BaseModel):
    user_id: int

@router.post("/{project_id}/users")
def add_project_user(project_id: int, user_req: ProjectUserAdd, current_user: dict = Depends(require_roles(["ADMIN", "ENGAGEMENT_MANAGER"])), db: mysql.connector.connection.MySQLConnection = Depends(get_db)):
    cursor = db.cursor(dictionary=True)
    try:
        cursor.execute("INSERT INTO project_users (project_id, user_id) VALUES (%s, %s)", (project_id, user_req.user_id))
        db.commit()
    except mysql.connector.IntegrityError:
        pass # Already assigned
    cursor.close()
    return {"success": True, "message": "User assigned to project"}

@router.get("/{project_id}/users")
def get_project_users(project_id: int, current_user: dict = Depends(get_current_user), db: mysql.connector.connection.MySQLConnection = Depends(get_db)):
    cursor = db.cursor(dictionary=True)
    cursor.execute("""
        SELECT u.id, u.name, u.email, u.role 
        FROM users u 
        JOIN project_users pu ON u.id = pu.user_id 
        WHERE pu.project_id = %s
    """, (project_id,))
    users = cursor.fetchall()
    cursor.close()
    return {"success": True, "data": users}
