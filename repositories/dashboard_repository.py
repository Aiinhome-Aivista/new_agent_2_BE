# pyrefly: ignore [missing-import]
import mysql.connector
from typing import Dict, Any

class DashboardRepository:
    @staticmethod
    def get_dashboard_stats(db: mysql.connector.connection.MySQLConnection, user_id: int, is_admin: bool) -> Dict[str, Any]:
        cursor = db.cursor(dictionary=True)
        
        if is_admin:
            # 1. Active Projects
            cursor.execute("SELECT COUNT(*) as count FROM projects")
            row = cursor.fetchone()
            active_projects = row["count"] if row else 0
            
            # 2. Contract Baselines (single query for total and approved)
            cursor.execute("""
                SELECT 
                    COUNT(*) as total,
                    COALESCE(SUM(CASE WHEN status = 'APPROVED' THEN 1 ELSE 0 END), 0) as approved
                FROM scope_baselines
            """)
            baseline_row = cursor.fetchone() or {"total": 0, "approved": 0}
            total_baselines = int(baseline_row.get("total") or 0)
            approved_baselines = int(baseline_row.get("approved") or 0)
            
            # 3. Tracker Items / Risks (single query for open total, scope creep, and high severity)
            cursor.execute("""
                SELECT 
                    COUNT(*) as unresolved_risks,
                    COALESCE(SUM(CASE WHEN risk_category = 'SCOPE_CREEP' THEN 1 ELSE 0 END), 0) as scope_creep_risks,
                    COALESCE(SUM(CASE WHEN risk_category = 'SCOPE_CREEP' AND risk_level IN ('HIGH', 'CRITICAL') THEN 1 ELSE 0 END), 0) as high_severity_creep
                FROM tracker_items
                WHERE status = 'OPEN'
            """)
            tracker_row = cursor.fetchone() or {"unresolved_risks": 0, "scope_creep_risks": 0, "high_severity_creep": 0}
            unresolved_risks = int(tracker_row.get("unresolved_risks") or 0)
            scope_creep_risks = int(tracker_row.get("scope_creep_risks") or 0)
            high_severity_creep = int(tracker_row.get("high_severity_creep") or 0)
            open_risks = unresolved_risks
            
            # 4. System Alerts Sent
            cursor.execute("SELECT COUNT(*) as count FROM alerts")
            alert_row = cursor.fetchone()
            total_alerts = alert_row["count"] if alert_row else 0
            
            # 5. Recent Activities
            cursor.execute("""
                SELECT al.*, p.project_name as project_name 
                FROM audit_logs al
                LEFT JOIN projects p ON al.project_id = p.id
                ORDER BY al.created_at DESC LIMIT 5
            """)
            recent_activities = cursor.fetchall() or []

            # 6. Audit Activity Summary
            cursor.execute("SELECT COUNT(*) as count, MAX(created_at) as last_event_at FROM audit_logs")
            audit_activity = cursor.fetchone() or {"count": 0, "last_event_at": None}
            
        else:
            # 1. Fetch user assigned project IDs once
            cursor.execute("SELECT project_id FROM project_users WHERE user_id = %s", (user_id,))
            user_projects = cursor.fetchall() or []
            project_ids = [r["project_id"] for r in user_projects if r.get("project_id") is not None]
            
            if not project_ids:
                cursor.close()
                return {
                    "unresolved_risks": 0,
                    "active_projects": 0,
                    "contract_baselines": {"total": 0, "approved": 0},
                    "scope_creep_risks": {"total": 0, "high_severity": 0},
                    "system_alerts": 0,
                    "recent_activities": [],
                    "audit_status": {
                        "audit_events": 0,
                        "last_audit_at": None,
                        "open_risks": 0,
                        "baseline_approval_percent": 0
                    }
                }
                
            active_projects = len(project_ids)
            placeholders = ','.join(['%s'] * len(project_ids))
            params = tuple(project_ids)
            
            # 2. Contract Baselines (single query for total and approved)
            cursor.execute(f"""
                SELECT 
                    COUNT(*) as total,
                    COALESCE(SUM(CASE WHEN status = 'APPROVED' THEN 1 ELSE 0 END), 0) as approved
                FROM scope_baselines 
                WHERE project_id IN ({placeholders})
            """, params)
            baseline_row = cursor.fetchone() or {"total": 0, "approved": 0}
            total_baselines = int(baseline_row.get("total") or 0)
            approved_baselines = int(baseline_row.get("approved") or 0)
            
            # 3. Tracker Items / Risks (single query for open total, scope creep, and high severity)
            cursor.execute(f"""
                SELECT 
                    COUNT(*) as unresolved_risks,
                    COALESCE(SUM(CASE WHEN risk_category = 'SCOPE_CREEP' THEN 1 ELSE 0 END), 0) as scope_creep_risks,
                    COALESCE(SUM(CASE WHEN risk_category = 'SCOPE_CREEP' AND risk_level IN ('HIGH', 'CRITICAL') THEN 1 ELSE 0 END), 0) as high_severity_creep
                FROM tracker_items 
                WHERE status = 'OPEN' AND project_id IN ({placeholders})
            """, params)
            tracker_row = cursor.fetchone() or {"unresolved_risks": 0, "scope_creep_risks": 0, "high_severity_creep": 0}
            unresolved_risks = int(tracker_row.get("unresolved_risks") or 0)
            scope_creep_risks = int(tracker_row.get("scope_creep_risks") or 0)
            high_severity_creep = int(tracker_row.get("high_severity_creep") or 0)
            open_risks = unresolved_risks
            
            # 4. System Alerts Sent
            cursor.execute(f"SELECT COUNT(*) as count FROM alerts WHERE project_id IN ({placeholders})", params)
            alert_row = cursor.fetchone()
            total_alerts = alert_row["count"] if alert_row else 0
            
            # 5. Recent Activities
            cursor.execute(f"""
                SELECT al.*, p.project_name as project_name 
                FROM audit_logs al
                LEFT JOIN projects p ON al.project_id = p.id
                WHERE al.project_id IN ({placeholders})
                ORDER BY al.created_at DESC LIMIT 5
            """, params)
            recent_activities = cursor.fetchall() or []

            # 6. Audit Activity Summary
            cursor.execute(f"""
                SELECT COUNT(*) as count, MAX(created_at) as last_event_at
                FROM audit_logs
                WHERE project_id IN ({placeholders})
            """, params)
            audit_activity = cursor.fetchone() or {"count": 0, "last_event_at": None}
            
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
            "recent_activities": recent_activities,
            "audit_status": {
                "audit_events": audit_activity.get("count") or 0,
                "last_audit_at": audit_activity.get("last_event_at"),
                "open_risks": open_risks,
                "baseline_approval_percent": round((approved_baselines / total_baselines) * 100) if total_baselines else 0
            }
        }
