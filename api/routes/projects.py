# pyrefly: ignore [missing-import]
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from typing import List, Optional
from core.database import get_db
from api.dependencies.auth import get_current_user, require_roles, verify_project_access
from services.llm_service import LLMService
from repositories.project_repository import ProjectRepository
import mysql.connector

router = APIRouter()

class ProjectCreate(BaseModel):
    project_name: str
    client_name: Optional[str] = None
    description: Optional[str] = None
    assigned_lead_id: Optional[int] = None
    start_date: Optional[str] = None
    end_date: Optional[str] = None

class ProjectUpdate(BaseModel):
    project_name: Optional[str] = None
    client_name: Optional[str] = None
    description: Optional[str] = None
    monitoring_status: Optional[str] = None
    end_date: Optional[str] = None

@router.post("/")
def create_project(project: ProjectCreate, current_user: dict = Depends(require_roles(["ADMIN", "ENGAGEMENT_MANAGER"])), db: mysql.connector.connection.MySQLConnection = Depends(get_db)):
    if project.start_date and project.end_date:
        if project.start_date > project.end_date:
            raise HTTPException(status_code=400, detail="Start date cannot be after end date")
            
    project_id = ProjectRepository.create_project(
        db=db,
        project_name=project.project_name,
        client_name=project.client_name,
        description=project.description,
        start_date=project.start_date,
        end_date=project.end_date,
        created_by=current_user["id"]
    )
    db.commit()
    
    # Auto assign creator to project
    ProjectRepository.assign_user_to_project(db, project_id, current_user["id"])
    
    # If assigned_lead_id is provided, assign that Project Lead
    if project.assigned_lead_id:
        try:
            ProjectRepository.assign_user_to_project(db, project_id, project.assigned_lead_id)
        except Exception:
            pass
            
    db.commit()
    return {"success": True, "message": "Project created successfully", "data": {"id": project_id}}

@router.get("/")
def get_projects(current_user: dict = Depends(get_current_user), db: mysql.connector.connection.MySQLConnection = Depends(get_db)):
    if current_user["role"] in ["ADMIN", "PMO_REVIEWER", "FINANCE_COMMERCIAL"]:
        projects = ProjectRepository.get_all_projects(db)
    else:
        projects = ProjectRepository.get_projects_for_user(db, current_user["id"])
    return {"success": True, "data": projects}

@router.get("/{project_id}")
def get_project(project_id: int, current_user: dict = Depends(get_current_user), db: mysql.connector.connection.MySQLConnection = Depends(get_db)):
    if current_user["role"] not in ["ADMIN", "PMO_REVIEWER", "FINANCE_COMMERCIAL"]:
        assigned = ProjectRepository.check_user_project_assignment(db, project_id, current_user["id"])
        if not assigned:
            raise HTTPException(status_code=403, detail="Not assigned to this project")
            
    project = ProjectRepository.get_project_by_id(db, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    return {"success": True, "data": project}

class ProjectUserAdd(BaseModel):
    user_id: int

@router.post("/{project_id}/users")
def add_project_user(project_id: int, user_req: ProjectUserAdd, current_user: dict = Depends(require_roles(["ADMIN", "ENGAGEMENT_MANAGER"])), db: mysql.connector.connection.MySQLConnection = Depends(get_db)):
    verify_project_access(project_id, current_user, db)
    try:
        ProjectRepository.assign_user_to_project(db, project_id, user_req.user_id)
        db.commit()
    except mysql.connector.IntegrityError:
        pass # Already assigned
    return {"success": True, "message": "User assigned to project"}

@router.get("/{project_id}/users")
def get_project_users(project_id: int, current_user: dict = Depends(get_current_user), db: mysql.connector.connection.MySQLConnection = Depends(get_db)):
    verify_project_access(project_id, current_user, db)
    users = ProjectRepository.get_project_users(db, project_id)
    return {"success": True, "data": users}

class DescriptionGenerateRequest(BaseModel):
    project_name: str
    client_name: str

@router.post("/generate-description")
def generate_project_description(
    payload: DescriptionGenerateRequest,
    current_user: dict = Depends(get_current_user),
    db: mysql.connector.connection.MySQLConnection = Depends(get_db)
):
    prompt = (
        f"You are a professional project management assistant. "
        f"Generate a concise, professional 2-3 sentence project description for a project named '{payload.project_name}' "
        f"for the client '{payload.client_name}'. The description should outline the key objectives and audit goals. "
        f"Do not output markdown format or quotes, just output the raw description text."
    )
    try:
        description = LLMService.generate(prompt).strip()
        if description.startswith('"') and description.endswith('"'):
            description = description[1:-1]
        return {"success": True, "description": description}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to generate description: {e}")

@router.put("/{project_id}")
def update_project(
    project_id: int, 
    project: ProjectUpdate, 
    current_user: dict = Depends(require_roles(["ADMIN", "ENGAGEMENT_MANAGER"])), 
    db: mysql.connector.connection.MySQLConnection = Depends(get_db)
):
    verify_project_access(project_id, current_user, db)
    
    if project.end_date is not None and project.end_date != "":
        start_date = ProjectRepository.get_project_start_date(db, project_id)
        if start_date:
            start_date_str = str(start_date)
            if start_date_str > project.end_date:
                raise HTTPException(status_code=400, detail="End date cannot be before start date")

    # We build the update dict dynamically based on what is provided
    updates = {}
    if project.project_name is not None:
        updates["project_name"] = project.project_name
    if project.client_name is not None:
        updates["client_name"] = project.client_name
    if project.description is not None:
        updates["description"] = project.description
    if project.monitoring_status is not None:
        updates["monitoring_status"] = project.monitoring_status
    if project.end_date is not None:
        updates["end_date"] = project.end_date if project.end_date != "" else None
        
    if not updates:
        raise HTTPException(status_code=400, detail="No updates provided")
        
    ProjectRepository.update_project_fields(db, project_id, updates)
    db.commit()
    return {"success": True, "message": "Project updated successfully"}
