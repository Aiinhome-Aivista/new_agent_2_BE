import mysql.connector
from typing import List, Dict, Any, Optional

class UserRepository:
    @staticmethod
    def get_user_by_email(db: mysql.connector.connection.MySQLConnection, email: str) -> Optional[Dict[str, Any]]:
        cursor = db.cursor(dictionary=True)
        cursor.execute("SELECT id, name, email, password_hash, role, is_active FROM users WHERE email = %s", (email,))
        user = cursor.fetchone()
        cursor.close()
        return user

    @staticmethod
    def get_user_by_id(db: mysql.connector.connection.MySQLConnection, user_id: int) -> Optional[Dict[str, Any]]:
        cursor = db.cursor(dictionary=True)
        cursor.execute("SELECT id, name, email, role, is_active FROM users WHERE id = %s", (user_id,))
        user = cursor.fetchone()
        cursor.close()
        return user

    @staticmethod
    def get_users(db: mysql.connector.connection.MySQLConnection, role: Optional[str] = None) -> List[Dict[str, Any]]:
        cursor = db.cursor(dictionary=True)
        if role:
            cursor.execute("SELECT id, name, email, role, is_active, created_at FROM users WHERE role = %s", (role,))
        else:
            cursor.execute("SELECT id, name, email, role, is_active, created_at FROM users")
        users = cursor.fetchall()
        cursor.close()
        return users

    @staticmethod
    def create_user(db: mysql.connector.connection.MySQLConnection, name: str, email: str, password_hash: str, role: str) -> int:
        cursor = db.cursor()
        sql = "INSERT INTO users (name, email, password_hash, role) VALUES (%s, %s, %s, %s)"
        cursor.execute(sql, (name, email, password_hash, role))
        new_id = cursor.lastrowid
        cursor.close()
        return new_id
