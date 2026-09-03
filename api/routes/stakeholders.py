# pyrefly: ignore [missing-import]
from fastapi import APIRouter, Depends, HTTPException, status, UploadFile, File
from fastapi.responses import StreamingResponse
import csv
import io
import codecs
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

@router.get("/template")
def download_bulk_upload_template(
    project_id: int,
    current_user: dict = Depends(require_roles(["ADMIN", "ENGAGEMENT_MANAGER"])),
    db: mysql.connector.connection.MySQLConnection = Depends(get_db)
):
    verify_project_access(project_id, current_user, db)
    
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Full Name", "Email Address", "Role", "Responsibilities and Scope Notes"])
    
    output.seek(0)
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=project_members_template.csv"}
    )

@router.post("/bulk-upload")
def bulk_upload_stakeholders(
    project_id: int,
    file: UploadFile = File(...),
    current_user: dict = Depends(require_roles(["ADMIN", "ENGAGEMENT_MANAGER"])),
    db: mysql.connector.connection.MySQLConnection = Depends(get_db)
):
    verify_project_access(project_id, current_user, db)
    
    if not file.filename.endswith(".csv"):
        raise HTTPException(status_code=400, detail="Invalid file type. Please upload a CSV file.")
        
    try:
        content = file.file.read()
        # Decode the bytes into string and parse as CSV
        text = codecs.decode(content, 'utf-8-sig') # utf-8-sig handles BOM
        reader = csv.DictReader(io.StringIO(text))
        
        success_count = 0
        errors = []
        
        for row_idx, row in enumerate(reader, start=2): # Start at 2 to account for header
            # Clean keys in case of unexpected spaces in CSV headers
            cleaned_row = {k.strip(): v.strip() for k, v in row.items() if k and v}
            
            # Map columns
            name = cleaned_row.get("Full Name")
            email = cleaned_row.get("Email Address")
            role = cleaned_row.get("Role", "Stakeholder")
            responsibility = cleaned_row.get("Responsibilities and Scope Notes")
            
            if not name:
                errors.append(f"Row {row_idx}: Missing 'Full Name'")
                continue
                
            target_user_id = None
            if email:
                target_user_id = StakeholderRepository.get_user_id_by_email(db, email)
                
            try:
                stakeholder_id = StakeholderRepository.create_stakeholder(
                    db=db,
                    project_id=project_id,
                    name=name,
                    email=email if email else None,
                    role=role,
                    responsibility=responsibility if responsibility else None,
                    user_id=target_user_id
                )
                
                # Auto-assign to project_users if linked to a system user
                if target_user_id:
                    try:
                        StakeholderRepository.assign_user_to_project(db, project_id, target_user_id)
                    except mysql.connector.IntegrityError:
                        pass # Already assigned
                        
                success_count += 1
            except Exception as e:
                errors.append(f"Row {row_idx}: Error saving to database ({str(e)})")
                
        db.commit()
        
        return {
            "success": True, 
            "message": f"Successfully imported {success_count} members.",
            "data": {
                "success_count": success_count,
                "errors": errors
            }
        }
        
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Failed to process file: {str(e)}")
