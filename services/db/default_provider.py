import mysql.connector
from mysql.connector import Error
from core.config import settings
from .base import BaseDBProvider

class DefaultDBProvider(BaseDBProvider):
    def get_connection(self):
        try:
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
            print(f"Error connecting to Default MySQL: {e}")
            return None
