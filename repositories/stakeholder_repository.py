import mysql.connector
from typing import List, Dict, Any, Optional

class StakeholderRepository:
    @staticmethod
    def get_user_id_by_email(db: mysql.connector.connection.MySQLConnection, email: str) -> Optional[int]:
        cursor = db.cursor(dictionary=True)
        cursor.execute("SELECT id FROM users WHERE email = %s", (email,))
        row = cursor.fetchone()
        cursor.close()
        return row["id"] if row else None

    @staticmethod
    def create_stakeholder(db: mysql.connector.connection.MySQLConnection, project_id: int, name: str, email: Optional[str], role: str, responsibility: Optional[str], user_id: Optional[int]) -> int:
        cursor = db.cursor()
        sql = """
            INSERT INTO stakeholders (project_id, name, email, role, responsibility, user_id) 
            VALUES (%s, %s, %s, %s, %s, %s)
        """
        cursor.execute(sql, (project_id, name, email, role, responsibility, user_id))
        stakeholder_id = cursor.lastrowid
        cursor.close()
        return stakeholder_id

    @staticmethod
    def assign_user_to_project(db: mysql.connector.connection.MySQLConnection, project_id: int, user_id: int) -> None:
        cursor = db.cursor()
        cursor.execute("INSERT INTO project_users (project_id, user_id) VALUES (%s, %s)", (project_id, user_id))
        cursor.close()

    @staticmethod
    def get_stakeholder_by_id(db: mysql.connector.connection.MySQLConnection, stakeholder_id: int) -> Optional[Dict[str, Any]]:
        cursor = db.cursor(dictionary=True)
        cursor.execute("SELECT * FROM stakeholders WHERE id = %s", (stakeholder_id,))
        row = cursor.fetchone()
        cursor.close()
        return row

    @staticmethod
    def get_stakeholders_by_project(db: mysql.connector.connection.MySQLConnection, project_id: int) -> List[Dict[str, Any]]:
        cursor = db.cursor(dictionary=True)
        cursor.execute("SELECT * FROM stakeholders WHERE project_id = %s ORDER BY created_at DESC", (project_id,))
        rows = cursor.fetchall()
        cursor.close()
        return rows

    @staticmethod
    def get_stakeholder_in_project(db: mysql.connector.connection.MySQLConnection, stakeholder_id: int, project_id: int) -> Optional[Dict[str, Any]]:
        cursor = db.cursor(dictionary=True)
        cursor.execute("SELECT * FROM stakeholders WHERE id = %s AND project_id = %s", (stakeholder_id, project_id))
        row = cursor.fetchone()
        cursor.close()
        return row

    @staticmethod
    def update_stakeholder_fields(db: mysql.connector.connection.MySQLConnection, stakeholder_id: int, project_id: int, updates: Dict[str, Any]) -> None:
        if not updates:
            return
        cursor = db.cursor()
        clause_list = []
        values = []
        for k, v in updates.items():
            clause_list.append(f"{k} = %s")
            values.append(v)
        sql = f"UPDATE stakeholders SET {', '.join(clause_list)} WHERE id = %s AND project_id = %s"
        values.extend([stakeholder_id, project_id])
        cursor.execute(sql, tuple(values))
        cursor.close()

    @staticmethod
    def delete_stakeholder(db: mysql.connector.connection.MySQLConnection, stakeholder_id: int, project_id: int) -> None:
        cursor = db.cursor()
        cursor.execute("DELETE FROM stakeholders WHERE id = %s AND project_id = %s", (stakeholder_id, project_id))
        cursor.close()
