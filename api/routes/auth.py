from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from typing import Any
from core.database import get_db
from core.security import verify_password, create_access_token
from api.dependencies.auth import get_current_user
import mysql.connector

router = APIRouter()

class LoginRequest(BaseModel):
    email: str
    password: str

@router.post("/login")
def login(login_data: LoginRequest, db: mysql.connector.connection.MySQLConnection = Depends(get_db)):
    cursor = db.cursor(dictionary=True)
    cursor.execute("SELECT id, name, email, password_hash, role, is_active FROM users WHERE email = %s", (login_data.email,))
    user = cursor.fetchone()
    cursor.close()

    if not user or not verify_password(login_data.password, user["password_hash"]):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
        
    if not user["is_active"]:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Inactive user"
        )

    access_token = create_access_token(
        subject=user["id"],
        role=user["role"]
    )
    
    return {
        "success": True,
        "data": {
            "access_token": access_token,
            "user": {
                "id": user["id"],
                "name": user["name"],
                "email": user["email"],
                "role": user["role"]
            }
        }
    }

@router.get("/me")
def read_users_me(current_user: dict = Depends(get_current_user), db: mysql.connector.connection.MySQLConnection = Depends(get_db)):
    cursor = db.cursor(dictionary=True)
    cursor.execute("SELECT id, name, email, role FROM users WHERE id = %s", (current_user["id"],))
    user = cursor.fetchone()
    cursor.close()
    
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
        
    return {
        "success": True,
        "data": user
    }
