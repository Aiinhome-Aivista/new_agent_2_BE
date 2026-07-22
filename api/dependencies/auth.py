# pyrefly: ignore [missing-import]
from fastapi import Depends, HTTPException, status
# pyrefly: ignore [missing-import]
from fastapi.security import OAuth2PasswordBearer
from typing import List
import jwt
from core.config import settings
from core.security import decode_access_token
from core.database import get_db

oauth2_scheme = OAuth2PasswordBearer(tokenUrl=f"{settings.API_PREFIX}/auth/login")

def get_current_user(token: str = Depends(oauth2_scheme)):
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    payload = decode_access_token(token)
    if payload is None:
        raise credentials_exception
    
    user_id: str = payload.get("sub")
    role: str = payload.get("role")
    if user_id is None or role is None:
        raise credentials_exception
        
    return {"id": int(user_id), "role": role}

def require_roles(allowed_roles: List[str]):
    def role_checker(current_user: dict = Depends(get_current_user)):
        if current_user["role"] not in allowed_roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Operation not permitted"
            )
        return current_user
    return role_checker

def verify_project_access(project_id: int, current_user: dict, db):
    if current_user["role"] in ["ADMIN", "PMO_REVIEWER", "FINANCE_COMMERCIAL"]:
        return True
    cursor = db.cursor()
    cursor.execute("SELECT 1 FROM project_users WHERE project_id = %s AND user_id = %s", (project_id, current_user["id"]))
    has_access = cursor.fetchone() is not None
    cursor.close()
    if not has_access:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You are not assigned to this project"
        )
    return True
