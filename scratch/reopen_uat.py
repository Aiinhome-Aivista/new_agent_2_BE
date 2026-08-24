import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.database import get_db_connection
from api.routes.baseline import _rebuild_graph_and_recalculate

conn = get_db_connection()
cursor = conn.cursor(dictionary=True)
project_id = 46

# 1. Reopen tracker item 527 (User Acceptance Testing) because its deliverable 1418 is ACTIVE
cursor.execute("""
    UPDATE tracker_items 
    SET status = 'OPEN', execution_status = 'IN_PROGRESS', risk_status = 'OPEN',
        resolution = NULL, resolved_by = NULL, resolved_at = NULL
    WHERE id = 527
""")
conn.commit()

# 2. Run graph recalculation
_rebuild_graph_and_recalculate(cursor, project_id, "System Integration Testing (SIT), UAT, Production Deployment")
conn.commit()

cursor.close()
conn.close()
