import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.database import get_db_connection

conn = get_db_connection()
cursor = conn.cursor(dictionary=True)
project_id = 46

# 1. Check current items
cursor.execute("SELECT id, title, status, execution_status, execution_priority_score FROM tracker_items WHERE project_id = %s", (project_id,))
items = cursor.fetchall()
print("CURRENT TRACKER ITEMS:")
for it in items:
    print(f"ID {it['id']} | {it['title']} | Status: {it['status']} | Exec Status: {it['execution_status']} | Score: {it['execution_priority_score']}")

cursor.close()
conn.close()
