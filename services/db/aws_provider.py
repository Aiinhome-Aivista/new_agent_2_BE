import mysql.connector
from mysql.connector import Error
from core.config import settings
from .base import BaseDBProvider

class AWSRDSProvider(BaseDBProvider):
    def get_connection(self):
        try:
            connection = mysql.connector.connect(
                host=settings.AWS_RDS_HOST,
                port=settings.AWS_RDS_PORT,
                database=settings.AWS_RDS_DATABASE,
                user=settings.AWS_RDS_USER,
                password=settings.AWS_RDS_PASSWORD,
                charset='utf8mb4',
                collation='utf8mb4_unicode_ci'
            )
            if connection.is_connected():
                return connection
        except Error as e:
            print(f"Error connecting to AWS RDS MySQL: {e}")
            return None
