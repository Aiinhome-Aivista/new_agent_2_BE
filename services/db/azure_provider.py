import mysql.connector
from mysql.connector import Error
from core.config import settings
from .base import BaseDBProvider

class AzureDBProvider(BaseDBProvider):
    def get_connection(self):
        try:
            connection = mysql.connector.connect(
                host=settings.AZURE_DB_HOST,
                port=settings.AZURE_DB_PORT,
                database=settings.AZURE_DB_DATABASE,
                user=settings.AZURE_DB_USER,
                password=settings.AZURE_DB_PASSWORD,
                charset='utf8mb4',
                collation='utf8mb4_unicode_ci'
            )
            if connection.is_connected():
                return connection
        except Error as e:
            print(f"Error connecting to Azure DB MySQL: {e}")
            return None
