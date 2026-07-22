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
    def get_projects_for_user(db: mysql.connector.connection.MySQLConnection, user_id: int) -> List[Dict[str, Any]]:
        cursor = db.cursor(dictionary=True)
        cursor.execute("""
            SELECT p.* FROM projects p
            JOIN project_users pu ON p.id = pu.project_id
            WHERE pu.user_id = %s
        """, (user_id,))
        projects = cursor.fetchall()
        cursor.close()
        return projects

    @staticmethod
    def get_all_projects(db: mysql.connector.connection.MySQLConnection) -> List[Dict[str, Any]]:
        cursor = db.cursor(dictionary=True)
        cursor.execute("SELECT * FROM projects")
        projects = cursor.fetchall()
        cursor.close()
        return projects

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
        cursor.execute("SELECT * FROM projects WHERE id = %s", (project_id,))
        project = cursor.fetchone()
        cursor.close()
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
