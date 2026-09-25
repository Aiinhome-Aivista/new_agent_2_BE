# pyrefly: ignore [missing-import]
from fastapi import APIRouter, Depends, HTTPException
from core.database import get_db
from api.dependencies.auth import get_current_user
from repositories.dashboard_repository import DashboardRepository
import mysql.connector

router = APIRouter()

@router.get("/stats")
def get_dashboard_stats(
    current_user: dict = Depends(get_current_user),
    db: mysql.connector.connection.MySQLConnection = Depends(get_db)
):
    try:
        user_id = current_user["id"]
        is_admin = current_user.get("role") == "ADMIN"
        
        stats_data = DashboardRepository.get_dashboard_stats(db, user_id, is_admin)
        return {
            "success": True,
            "data": stats_data
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to fetch dashboard stats: {str(e)}"
        )
