import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.database import get_db_connection

conn = get_db_connection()
cursor = conn.cursor(dictionary=True)
cursor.execute("SELECT id, project_id, title, status, execution_status, risk_score, execution_priority_score FROM tracker_items WHERE status = 'OPEN' ORDER BY id DESC LIMIT 15")
rows = cursor.fetchall()
print(f"FOUND {len(rows)} OPEN ITEMS:")
for r in rows:
    print(r)
cursor.close()
conn.close()
