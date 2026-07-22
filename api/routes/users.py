# pyrefly: ignore [missing-import]
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from typing import List, Optional
from core.database import get_db
from api.dependencies.auth import get_current_user, require_roles
from core.security import get_password_hash
from repositories.user_repository import UserRepository
import mysql.connector

router = APIRouter()

class UserCreate(BaseModel):
    name: str
    email: str
    password: str
    role: str

class UserUpdate(BaseModel):
    name: Optional[str]
    email: Optional[str]
    role: Optional[str]

class UserStatusUpdate(BaseModel):
    is_active: bool

@router.get("/")
def get_users(role: Optional[str] = None, current_user: dict = Depends(require_roles(["ADMIN", "ENGAGEMENT_MANAGER", "PROJECT_LEAD"])), db: mysql.connector.connection.MySQLConnection = Depends(get_db)):
    users = UserRepository.get_users(db, role)
    return {"success": True, "data": users}

@router.post("/", dependencies=[Depends(require_roles(["ADMIN"]))])
def create_user(user: UserCreate, db: mysql.connector.connection.MySQLConnection = Depends(get_db)):
    existing_user = UserRepository.get_user_by_email(db, user.email)
    if existing_user:
        raise HTTPException(status_code=400, detail="Email already registered")
        
    hashed_password = get_password_hash(user.password)
    new_id = UserRepository.create_user(db, user.name, user.email, hashed_password, user.role)
    db.commit()
    return {"success": True, "message": "User created successfully", "data": {"id": new_id}}
