import os
import sys
import mysql.connector
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '.env'))

from core.database import get_db_connection

conn = get_db_connection()
cursor = conn.cursor(dictionary=True)
cursor.execute("SELECT entity_id, action, details_json FROM audit_logs WHERE project_id = 1 AND entity_id = 383 ORDER BY id DESC")
print(cursor.fetchall())
