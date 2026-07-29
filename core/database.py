import mysql.connector
from mysql.connector import Error
# pyrefly: ignore [missing-import]
from fastapi import HTTPException
from .config import settings

from mysql.connector import pooling

# Initialize a global connection pool
try:
    db_pool = mysql.connector.pooling.MySQLConnectionPool(
        pool_name="backend_pool",
        pool_size=10,
        pool_reset_session=True,
        host=settings.DB_HOST,
        port=settings.DB_PORT,
        database=settings.DB_NAME,
        user=settings.DB_USER,
        password=settings.DB_PASSWORD,
        charset='utf8mb4',
        collation='utf8mb4_unicode_ci'
    )
except Error as e:
    print(f"Error initializing connection pool: {e}")
    db_pool = None

def get_db_connection():
    try:
        if db_pool:
            connection = db_pool.get_connection()
            if connection.is_connected():
                return connection
        # Fallback to direct connection if pool fails or is exhausted
        connection = mysql.connector.connect(
            host=settings.DB_HOST,
            port=settings.DB_PORT,
            database=settings.DB_NAME,
            user=settings.DB_USER,
            password=settings.DB_PASSWORD,
            charset='utf8mb4',
            collation='utf8mb4_unicode_ci'
        )
        if connection.is_connected():
            return connection
    except Error as e:
        print(f"Error connecting to MySQL: {e}")
        return None

def get_db():
    conn = get_db_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Database connection failed")
    try:
        yield conn
    finally:
        conn.close()
