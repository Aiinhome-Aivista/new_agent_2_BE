from contextlib import contextmanager
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

@contextmanager
def db_cursor(db, dictionary: bool = True):
    """
    Context manager for database cursor that guarantees cursor closure.
    """
    cursor = db.cursor(dictionary=dictionary)
    try:
        yield cursor
    finally:
        try:
            cursor.close()
        except Exception:
            pass

@contextmanager
def db_transaction(db, dictionary: bool = True):
    """
    Context manager for safe database transactions.
    Yields a cursor, commits on success, and rolls back on exception.
    Always closes the cursor.
    """
    cursor = db.cursor(dictionary=dictionary)
    try:
        yield cursor
        db.commit()
    except Exception:
        try:
            db.rollback()
        except Exception:
            pass
        raise
    finally:
        try:
            cursor.close()
        except Exception:
            pass
