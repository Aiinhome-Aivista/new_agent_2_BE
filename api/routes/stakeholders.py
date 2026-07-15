from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from typing import List, Optional
from core.database import get_db
from api.dependencies.auth import get_current_user, require_roles
import mysql.connector

router = APIRouter()

class StakeholderCreate(BaseModel):
    name: str
    email: Optional[str] = None
    role: Optional[str] = None
    responsibility: Optional[str] = None

@router.post("/")
def add_stakeholder(project_id: int, stakeholder: StakeholderCreate, current_user: dict = Depends(require_roles(["ADMIN", "ENGAGEMENT_MANAGER", "PROJECT_LEAD"])), db: mysql.connector.connection.MySQLConnection = Depends(get_db)):
    cursor = db.cursor(dictionary=True)
    sql = "INSERT INTO stakeholders (project_id, name, email, role, responsibility) VALUES (%s, %s, %s, %s, %s)"
    cursor.execute(sql, (project_id, stakeholder.name, stakeholder.email, stakeholder.role, stakeholder.responsibility))
    db.commit()
    stakeholder_id = cursor.lastrowid
    cursor.close()
    return {"success": True, "message": "Stakeholder added", "data": {"id": stakeholder_id}}

@router.get("/")
def get_stakeholders(project_id: int, current_user: dict = Depends(get_current_user), db: mysql.connector.connection.MySQLConnection = Depends(get_db)):
    cursor = db.cursor(dictionary=True)
    cursor.execute("SELECT * FROM stakeholders WHERE project_id = %s", (project_id,))
    stakeholders = cursor.fetchall()
    cursor.close()
    return {"success": True, "data": stakeholders}
