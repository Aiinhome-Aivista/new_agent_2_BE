# pyrefly: ignore [missing-import]
from fastapi import APIRouter, Depends
from core.database import get_db
from api.dependencies.auth import get_current_user
import mysql.connector

router = APIRouter()

@router.get("/stats")
def get_dashboard_stats(current_user: dict = Depends(get_current_user), db: mysql.connector.connection.MySQLConnection = Depends(get_db)):
    cursor = db.cursor(dictionary=True)
    user_id = current_user["id"]
    is_admin = current_user.get("role") == "ADMIN"
    
    # Base WHERE clause depending on role
    project_filter = "" if is_admin else f"WHERE id IN (SELECT project_id FROM project_users WHERE user_id = {user_id})"
    project_filter_ref = "" if is_admin else f"WHERE project_id IN (SELECT project_id FROM project_users WHERE user_id = {user_id})"
    
    # 1. Active Projects
    cursor.execute(f"SELECT COUNT(*) as count FROM projects {project_filter}")
    active_projects = cursor.fetchone()["count"]
    
    # 2. Contract Baselines (Approved)
    cursor.execute(f"SELECT COUNT(*) as count FROM scope_baselines {project_filter_ref} AND status = 'APPROVED'")
    approved_baselines = cursor.fetchone()["count"]
    cursor.execute(f"SELECT COUNT(*) as count FROM scope_baselines {project_filter_ref}")
    total_baselines = cursor.fetchone()["count"]
    
    # 3. Scope Creep Risks
    cursor.execute(f"SELECT COUNT(*) as count FROM tracker_items {project_filter_ref} {'AND' if project_filter_ref else 'WHERE'} risk_category = 'SCOPE_CREEP' AND status = 'OPEN'")
    scope_creep_risks = cursor.fetchone()["count"]
    
    cursor.execute(f"SELECT COUNT(*) as count FROM tracker_items {project_filter_ref} {'AND' if project_filter_ref else 'WHERE'} risk_category = 'SCOPE_CREEP' AND status = 'OPEN' AND risk_level IN ('HIGH', 'CRITICAL')")
    high_severity_creep = cursor.fetchone()["count"]

    # 4. Total Unresolved Risks
    cursor.execute(f"SELECT COUNT(*) as count FROM tracker_items {project_filter_ref} {'AND' if project_filter_ref else 'WHERE'} status = 'OPEN'")
    unresolved_risks = cursor.fetchone()["count"]
    
    # 5. System Alerts Sent
    cursor.execute(f"SELECT COUNT(*) as count FROM alerts {project_filter_ref}")
    total_alerts = cursor.fetchone()["count"]
    
    # 6. Recent Activities (from audit_logs)
    audit_filter = project_filter_ref.replace('WHERE project_id', 'WHERE al.project_id') if project_filter_ref else ""
    cursor.execute(f"""
        SELECT al.*, p.project_name as project_name 
        FROM audit_logs al
        LEFT JOIN projects p ON al.project_id = p.id
        {audit_filter}
        ORDER BY al.created_at DESC LIMIT 5
    """)
    recent_activities = cursor.fetchall()
    
    cursor.close()
    
    return {
        "success": True,
        "data": {
            "unresolved_risks": unresolved_risks,
            "active_projects": active_projects,
            "contract_baselines": {
                "total": total_baselines,
                "approved": approved_baselines
            },
            "scope_creep_risks": {
                "total": scope_creep_risks,
                "high_severity": high_severity_creep
            },
            "system_alerts": total_alerts,
            "recent_activities": recent_activities
        }
    }
