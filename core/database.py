import mysql.connector
from mysql.connector import Error
# pyrefly: ignore [missing-import]
from fastapi import HTTPException
from .config import settings

def get_db_connection():
    from services.db.factory import DBFactory
    return DBFactory.get_provider().get_connection()

def get_db():
    conn = get_db_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Database connection failed")
    try:
        yield conn
    finally:
        conn.close()
