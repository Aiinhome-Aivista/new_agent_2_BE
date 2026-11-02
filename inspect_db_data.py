import sys
sys.path.insert(0, 'new_agent_2_BE')
from core.database import get_db_connection

conn = get_db_connection()
cur = conn.cursor(dictionary=True)

for pid in [126, 125]:
    print(f"==================== PROJECT {pid} ====================")
    cur.execute("SELECT id, status, version FROM scope_baselines WHERE project_id = %s", (pid,))
    b = cur.fetchone()
    if not b:
        print("No baseline found")
        continue
    bid = b['id']
    print(f"Baseline ID: {bid}, status: {b['status']}, version: {b['version']}")
    
    cur.execute("SELECT COUNT(*) as cnt FROM scope_items WHERE baseline_id = %s", (bid,))
    print(f"Total scope_items in DB: {cur.fetchone()['cnt']}")
    
    cur.execute("""
        SELECT record_category, work_item_eligible, scope_type, COUNT(*) as cnt 
        FROM scope_items 
        WHERE baseline_id = %s 
        GROUP BY record_category, work_item_eligible, scope_type
        ORDER BY record_category, work_item_eligible
    """, (bid,))
    print("Breakdown by (record_category, work_item_eligible, scope_type):")
    for r in cur.fetchall():
        print(" ", r)
        
    cur.execute("SELECT COUNT(*) as cnt FROM project_milestones WHERE baseline_id = %s", (bid,))
    print(f"Total project_milestones for baseline {bid}: {cur.fetchone()['cnt']}")
    
    cur.execute("""
        SELECT id, name, sequence, status, planned_date 
        FROM project_milestones 
        WHERE baseline_id = %s 
        ORDER BY sequence, id
    """, (bid,))
    ms_rows = cur.fetchall()
    print("Milestones list:")
    for m in ms_rows:
        print(" ", m)
        
    cur.execute("""
        SELECT md.id, pm1.name as parent_name, pm2.name as child_name, md.dependency_type
        FROM milestone_dependencies md
        JOIN project_milestones pm1 ON md.parent_milestone_id = pm1.id
        JOIN project_milestones pm2 ON md.child_milestone_id = pm2.id
        WHERE pm1.baseline_id = %s
    """, (bid,))
    dep_rows = cur.fetchall()
    print(f"Total milestone dependencies: {len(dep_rows)}")
    for d in dep_rows:
        print(" ", d)

    # Let's inspect scope_items where record_category in ('COMMITMENT', 'MILESTONE', 'CLIENT_RESPONSIBILITY', 'CLIENT_DEPENDENCY', 'CONTEXTUAL_INFORMATION')
    cur.execute("""
        SELECT record_category, COUNT(*) as cnt 
        FROM scope_items 
        WHERE baseline_id = %s 
        GROUP BY record_category
    """, (bid,))
    print("scope_items by record_category:")
    for r in cur.fetchall():
        print(" ", r)
        
    print()

cur.close()
conn.close()
