import mysql.connector
from typing import Dict, Any

class DashboardRepository:
    @staticmethod
    def get_dashboard_stats(db: mysql.connector.connection.MySQLConnection, user_id: int, is_admin: bool) -> Dict[str, Any]:
        cursor = db.cursor(dictionary=True)
        
        if is_admin:
            # 1. Active Projects
            cursor.execute("SELECT COUNT(*) as count FROM projects")
            active_projects = cursor.fetchone()["count"]
            
            # 2. Contract Baselines
            cursor.execute("SELECT COUNT(*) as count FROM scope_baselines WHERE status = 'APPROVED'")
            approved_baselines = cursor.fetchone()["count"]
            cursor.execute("SELECT COUNT(*) as count FROM scope_baselines")
            total_baselines = cursor.fetchone()["count"]
            
            # 3. Scope Creep Risks
            cursor.execute("SELECT COUNT(*) as count FROM tracker_items WHERE risk_category = 'SCOPE_CREEP' AND status = 'OPEN'")
            scope_creep_risks = cursor.fetchone()["count"]
            
            cursor.execute("SELECT COUNT(*) as count FROM tracker_items WHERE risk_category = 'SCOPE_CREEP' AND status = 'OPEN' AND risk_level IN ('HIGH', 'CRITICAL')")
            high_severity_creep = cursor.fetchone()["count"]
            
            # 4. Total Unresolved Risks
            cursor.execute("SELECT COUNT(*) as count FROM tracker_items WHERE status = 'OPEN'")
            unresolved_risks = cursor.fetchone()["count"]
            
            # 5. System Alerts Sent
            cursor.execute("SELECT COUNT(*) as count FROM alerts")
            total_alerts = cursor.fetchone()["count"]
            
            # 6. Recent Activities
            cursor.execute("""
                SELECT al.*, p.project_name as project_name 
                FROM audit_logs al
                LEFT JOIN projects p ON al.project_id = p.id
                ORDER BY al.created_at DESC LIMIT 5
            """)
            recent_activities = cursor.fetchall()
            
        else:
            # 1. Active Projects
            cursor.execute("SELECT COUNT(*) as count FROM projects WHERE id IN (SELECT project_id FROM project_users WHERE user_id = %s)", (user_id,))
            active_projects = cursor.fetchone()["count"]
            
            # 2. Contract Baselines
            cursor.execute("SELECT COUNT(*) as count FROM scope_baselines WHERE project_id IN (SELECT project_id FROM project_users WHERE user_id = %s) AND status = 'APPROVED'", (user_id,))
            approved_baselines = cursor.fetchone()["count"]
            cursor.execute("SELECT COUNT(*) as count FROM scope_baselines WHERE project_id IN (SELECT project_id FROM project_users WHERE user_id = %s)", (user_id,))
            total_baselines = cursor.fetchone()["count"]
            
            # 3. Scope Creep Risks
            cursor.execute("SELECT COUNT(*) as count FROM tracker_items WHERE project_id IN (SELECT project_id FROM project_users WHERE user_id = %s) AND risk_category = 'SCOPE_CREEP' AND status = 'OPEN'", (user_id,))
            scope_creep_risks = cursor.fetchone()["count"]
            
            cursor.execute("SELECT COUNT(*) as count FROM tracker_items WHERE project_id IN (SELECT project_id FROM project_users WHERE user_id = %s) AND risk_category = 'SCOPE_CREEP' AND status = 'OPEN' AND risk_level IN ('HIGH', 'CRITICAL')", (user_id,))
            high_severity_creep = cursor.fetchone()["count"]
            
            # 4. Total Unresolved Risks
            cursor.execute("SELECT COUNT(*) as count FROM tracker_items WHERE project_id IN (SELECT project_id FROM project_users WHERE user_id = %s) AND status = 'OPEN'", (user_id,))
            unresolved_risks = cursor.fetchone()["count"]
            
            # 5. System Alerts Sent
            cursor.execute("SELECT COUNT(*) as count FROM alerts WHERE project_id IN (SELECT project_id FROM project_users WHERE user_id = %s)", (user_id,))
            total_alerts = cursor.fetchone()["count"]
            
            # 6. Recent Activities
            cursor.execute("""
                SELECT al.*, p.project_name as project_name 
                FROM audit_logs al
                LEFT JOIN projects p ON al.project_id = p.id
                WHERE al.project_id IN (SELECT project_id FROM project_users WHERE user_id = %s)
                ORDER BY al.created_at DESC LIMIT 5
            """, (user_id,))
            recent_activities = cursor.fetchall()
            
        cursor.close()
        
        return {
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
