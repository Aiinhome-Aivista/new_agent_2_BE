# pyrefly: ignore [missing-import]
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from typing import List, Optional
from core.database import get_db
from api.dependencies.auth import get_current_user, require_roles
from core.security import get_password_hash
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
def get_users(role: Optional[str] = None, current_user: dict = Depends(require_roles(["ADMIN", "ENGAGEMENT_MANAGER"])), db: mysql.connector.connection.MySQLConnection = Depends(get_db)):
    cursor = db.cursor(dictionary=True)
    if role:
        cursor.execute("SELECT id, name, email, role, is_active, created_at FROM users WHERE role = %s", (role,))
    else:
        cursor.execute("SELECT id, name, email, role, is_active, created_at FROM users")
    users = cursor.fetchall()
    cursor.close()
    return {"success": True, "data": users}

@router.post("/", dependencies=[Depends(require_roles(["ADMIN"]))])
def create_user(user: UserCreate, db: mysql.connector.connection.MySQLConnection = Depends(get_db)):
    cursor = db.cursor(dictionary=True)
    cursor.execute("SELECT id FROM users WHERE email = %s", (user.email,))
    if cursor.fetchone():
        raise HTTPException(status_code=400, detail="Email already registered")
        
    hashed_password = get_password_hash(user.password)
    sql = "INSERT INTO users (name, email, password_hash, role) VALUES (%s, %s, %s, %s)"
    cursor.execute(sql, (user.name, user.email, hashed_password, user.role))
    db.commit()
    new_id = cursor.lastrowid
    cursor.close()
    return {"success": True, "message": "User created successfully", "data": {"id": new_id}}
