import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.database import get_db_connection
from api.routes.baseline import _rebuild_graph_and_recalculate

conn = get_db_connection()
cursor = conn.cursor(dictionary=True)
project_id = 46

print("Triggering Step 2A-2G Recalculation for Project 46...")
_rebuild_graph_and_recalculate(cursor, project_id, None)
conn.commit()

cursor.close()
conn.close()
