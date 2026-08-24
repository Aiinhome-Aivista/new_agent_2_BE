import sys, os, json, re
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.database import get_db_connection
from api.routes.baseline import _is_title_match

conn = get_db_connection()
project_id = 46
reopen_title = "Azure AD SSO"

cursor = conn.cursor(dictionary=True)

# Fetch all tracker items for project
cursor.execute("SELECT id, title, status FROM tracker_items WHERE project_id = %s", (project_id,))
rows = cursor.fetchall() or []

print("Searching for items matching reopen_title:", reopen_title)
for r in rows:
    if _is_title_match(r["title"], reopen_title):
        print(f"MATCH FOUND: ID {r['id']}, Title '{r['title']}', Current Status: '{r['status']}'")
        cursor.execute("""
            UPDATE tracker_items 
            SET status = 'OPEN', execution_status = 'IN_PROGRESS', risk_status = 'OPEN',
                resolution = NULL, resolved_by = NULL, resolved_at = NULL
            WHERE id = %s
        """, (r["id"],))

conn.commit()
cursor.close()
conn.close()
