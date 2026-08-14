# pyrefly: ignore [missing-import]
import mysql.connector
from typing import List, Dict, Any, Optional

class ProjectRepository:
    @staticmethod
    def create_project(db: mysql.connector.connection.MySQLConnection, project_name: str, client_name: Optional[str], description: Optional[str], start_date: Optional[str], end_date: Optional[str], created_by: int) -> int:
        cursor = db.cursor()
        sql = "INSERT INTO projects (project_name, client_name, description, start_date, end_date, created_by) VALUES (%s, %s, %s, %s, %s, %s)"
        cursor.execute(sql, (project_name, client_name, description, start_date, end_date, created_by))
        project_id = cursor.lastrowid
        cursor.close()
        return project_id

    @staticmethod
    def assign_user_to_project(db: mysql.connector.connection.MySQLConnection, project_id: int, user_id: int) -> None:
        cursor = db.cursor()
        cursor.execute("INSERT INTO project_users (project_id, user_id) VALUES (%s, %s)", (project_id, user_id))
        cursor.close()

    @staticmethod
    def attach_health_scores(db: mysql.connector.connection.MySQLConnection, projects: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        if not projects:
            return projects
            
        from datetime import datetime, date
        cursor = db.cursor(dictionary=True)
        
        try:
            cursor.execute("SELECT project_id, document_type FROM documents")
            docs = cursor.fetchall()
        except Exception:
            docs = []
            
        docs_by_project = {}
        for d in docs:
            pid = d["project_id"]
            if pid not in docs_by_project:
                docs_by_project[pid] = set()
            docs_by_project[pid].add(d["document_type"])
            
        try:
            cursor.execute("SELECT project_id, status FROM scope_baselines")
            baselines = cursor.fetchall()
        except Exception:
            baselines = []
            
        approved_baselines_by_project = {b["project_id"] for b in baselines if b.get("status") == "APPROVED"}
        
        try:
            cursor.execute("SELECT project_id, status FROM tracker_items")
            tracker_items = cursor.fetchall()
        except Exception:
            tracker_items = []
            
        tracker_by_project = {}
        for ti in tracker_items:
            pid = ti["project_id"]
            if pid not in tracker_by_project:
                tracker_by_project[pid] = {"total": 0, "open": 0}
            tracker_by_project[pid]["total"] += 1
            if ti.get("status") != "RESOLVED":
                tracker_by_project[pid]["open"] += 1
                
        cursor.close()
        
        today = date.today()
        
        for p in projects:
            pid = p["id"]
            m_status = p.get("monitoring_status") or "DRAFT"
            end_date = p.get("end_date")
            
            days_remaining = None
            if end_date:
                try:
                    if isinstance(end_date, str):
                        ed = datetime.fromisoformat(end_date.replace("Z", "")).date() if "T" in end_date else datetime.strptime(end_date[:10], "%Y-%m-%d").date()
                    elif isinstance(end_date, (datetime, date)):
                        ed = end_date.date() if isinstance(end_date, datetime) else end_date
                    else:
                        ed = None
                    if ed:
                        days_remaining = (ed - today).days
                except Exception:
                    days_remaining = None
                    
            p_docs = docs_by_project.get(pid, set())
            has_both_docs = ("EL" in p_docs) and ("IFA" in p_docs)
            baseline_approved = pid in approved_baselines_by_project
            
            p_tracker = tracker_by_project.get(pid, {"total": 0, "open": 0})
            total_risks = p_tracker["total"]
            open_risks = p_tracker["open"]
            
            if m_status in ["DRAFT", "BASELINE_PENDING_REVIEW"]:
                score = 0
                if has_both_docs:
                    score += 60
                if baseline_approved:
                    score += 40
                elif m_status == "BASELINE_PENDING_REVIEW":
                    score += 20
            else:
                score = 100
                if total_risks > 0:
                    score -= min(60, int(round((open_risks / total_risks) * 60)))
                if days_remaining is not None:
                    if days_remaining < 0:
                        score -= 40
                    elif days_remaining < 14:
                        score -= 20
                score = max(0, score)
                
            if score >= 70:
                rag = "GREEN"
            elif score >= 40:
                rag = "AMBER"
            else:
                rag = "RED"
                
            p["health_score"] = score
            p["rag_status"] = rag
            
        return projects

    @staticmethod
    def get_projects_for_user(db: mysql.connector.connection.MySQLConnection, user_id: int) -> List[Dict[str, Any]]:
        cursor = db.cursor(dictionary=True)
        cursor.execute("""
            SELECT p.* FROM projects p
            JOIN project_users pu ON p.id = pu.project_id
            WHERE pu.user_id = %s
        """, (user_id,))
        projects = cursor.fetchall()
        cursor.close()
        return ProjectRepository.attach_health_scores(db, projects)

    @staticmethod
    def get_all_projects(db: mysql.connector.connection.MySQLConnection) -> List[Dict[str, Any]]:
        cursor = db.cursor(dictionary=True)
        cursor.execute("SELECT * FROM projects")
        projects = cursor.fetchall()
        cursor.close()
        return ProjectRepository.attach_health_scores(db, projects)

    @staticmethod
    def check_user_project_assignment(db: mysql.connector.connection.MySQLConnection, project_id: int, user_id: int) -> bool:
        cursor = db.cursor(dictionary=True)
        cursor.execute("SELECT 1 FROM project_users WHERE project_id = %s AND user_id = %s", (project_id, user_id))
        assigned = cursor.fetchone() is not None
        cursor.close()
        return assigned

    @staticmethod
    def get_project_by_id(db: mysql.connector.connection.MySQLConnection, project_id: int) -> Optional[Dict[str, Any]]:
        cursor = db.cursor(dictionary=True)
        cursor.execute("""
            SELECT p.*, 
                (SELECT summary FROM risk_evaluations re WHERE re.project_id = p.id ORDER BY re.id DESC LIMIT 1) as latest_summary,
                (SELECT sub_agent_results FROM risk_evaluations re WHERE re.project_id = p.id ORDER BY re.id DESC LIMIT 1) as latest_sub_agent_results
            FROM projects p WHERE p.id = %s
        """, (project_id,))
        project = cursor.fetchone()
        cursor.close()
        if project:
            ProjectRepository.attach_health_scores(db, [project])
        return project

    @staticmethod
    def get_project_users(db: mysql.connector.connection.MySQLConnection, project_id: int) -> List[Dict[str, Any]]:
        cursor = db.cursor(dictionary=True)
        cursor.execute("""
            SELECT u.id, u.name, u.email, u.role 
            FROM users u 
            JOIN project_users pu ON u.id = pu.user_id 
            WHERE pu.project_id = %s
        """, (project_id,))
        users = cursor.fetchall()
        cursor.close()
        return users

    @staticmethod
    def get_project_start_date(db: mysql.connector.connection.MySQLConnection, project_id: int) -> Optional[Any]:
        cursor = db.cursor(dictionary=True)
        cursor.execute("SELECT start_date FROM projects WHERE id = %s", (project_id,))
        row = cursor.fetchone()
        cursor.close()
        return row["start_date"] if row else None

    @staticmethod
    def update_project_fields(db: mysql.connector.connection.MySQLConnection, project_id: int, updates: Dict[str, Any]) -> None:
        if not updates:
            return
        cursor = db.cursor()
        clause_list = []
        values = []
        for k, v in updates.items():
            clause_list.append(f"{k} = %s")
            values.append(v)
        sql = f"UPDATE projects SET {', '.join(clause_list)} WHERE id = %s"
        values.append(project_id)
        cursor.execute(sql, tuple(values))
        cursor.close()
