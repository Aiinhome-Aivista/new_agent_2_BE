import time
import mysql.connector
from mysql.connector import Error
from core.config import settings
from .base import BaseDBProvider

class DefaultDBProvider(BaseDBProvider):
    def get_connection(self):
        max_retries = 3
        retry_delay = 0.3  # seconds
        for attempt in range(max_retries):
            try:
                connection = mysql.connector.connect(
                    host=settings.DB_HOST,
                    port=settings.DB_PORT,
                    database=settings.DB_NAME,
                    user=settings.DB_USER,
                    password=settings.DB_PASSWORD,
                    charset='utf8mb4',
                    collation='utf8mb4_unicode_ci',
                    connect_timeout=10,
                    connection_timeout=10
                )
                if connection.is_connected():
                    return connection
            except Error as e:
                if attempt < max_retries - 1:
                    time.sleep(retry_delay * (attempt + 1))
                else:
                    print(f"Error connecting to Default MySQL after {max_retries} attempts: {e}")
                    return None
        return None
