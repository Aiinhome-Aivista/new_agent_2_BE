import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.database import get_db_connection
from api.routes.baseline import _rebuild_graph_and_recalculate, _is_title_match

conn = get_db_connection()
cursor = conn.cursor(dictionary=True)
project_id = 46

cursor.execute("SELECT id, title FROM tracker_items WHERE project_id = %s", (project_id,))
rows = cursor.fetchall()
for r in rows:
    if _is_title_match(r['title'], 'Azure AD SSO'):
        cursor.execute("UPDATE tracker_items SET status='OPEN', execution_status='IN_PROGRESS', risk_status='OPEN', resolution=NULL WHERE id = %s", (r['id'],))

conn.commit()
_rebuild_graph_and_recalculate(cursor, project_id, None)
conn.commit()
cursor.close()
conn.close()
