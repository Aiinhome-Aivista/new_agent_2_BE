import sys
import os
import json

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.database import get_db_connection, db_cursor

conn = get_db_connection()
with db_cursor(conn) as cur:
    for pid in [124, 125, 126]:
        cur.execute("SELECT id, project_id, version, status, created_at FROM scope_baselines WHERE project_id = %s", (pid,))
        print(f"\n--- Project {pid} Baselines ---")
        for b in cur.fetchall():
            print(" ", b)
            
        cur.execute("SELECT COUNT(*) as cnt FROM scope_items WHERE project_id = %s", (pid,))
        print(f"P{pid} scope_items count:", cur.fetchall())

        cur.execute("SELECT scope_type, category, COUNT(*) as cnt FROM scope_items WHERE project_id = %s GROUP BY scope_type, category", (pid,))
        print(f"P{pid} scope_items breakdown:", cur.fetchall())

        cur.execute("SELECT plan_assumptions FROM scope_items WHERE project_id = %s LIMIT 3", (pid,))
        rows = cur.fetchall()
        for i, r in enumerate(rows):
            print(f"P{pid} sample plan_assumptions {i}:", r['plan_assumptions'])
            
        cur.execute("SELECT COUNT(*) as cnt FROM project_milestones WHERE project_id = %s", (pid,))
        print(f"P{pid} project_milestones count:", cur.fetchall())
