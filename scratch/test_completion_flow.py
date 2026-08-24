import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.database import get_db_connection
from api.routes.baseline import update_scope_item_completion, ScopeItemCompletionUpdate

conn = get_db_connection()
project_id = 46

# Find CRM scope item
cursor = conn.cursor(dictionary=True)
cursor.execute("SELECT id, name FROM scope_items WHERE project_id = %s AND LOWER(name) LIKE '%crm%'", (project_id,))
crm_item = cursor.fetchone()
cursor.close()

if crm_item:
    crm_id = crm_item["id"]
    print(f"Testing completion for CRM Scope Item ID {crm_id}: {crm_item['name']}")
    
    # 1. First trigger completion
    update_data = ScopeItemCompletionUpdate(
        completion_status="COMPLETED",
        completion_notes="Customer provided credentials on email and integration testing passed.",
        resolve_prerequisite_names=["Production CRM API credentials", "Production VPN access"]
    )
    
    res = update_scope_item_completion(
        project_id=project_id,
        item_id=crm_id,
        data=update_data,
        current_user={"id": 1, "email": "admin@example.com", "role": "ENGAGEMENT_MANAGER"},
        db=conn
    )
    print("Completion Result:", res)
    
    # 2. Inspect tracker_items now!
    cursor = conn.cursor(dictionary=True)
    cursor.execute("""
        SELECT id, title, status, execution_status, execution_priority_score, risk_score 
        FROM tracker_items 
        WHERE project_id = %s 
        ORDER BY CASE WHEN status='OPEN' THEN 1 ELSE 2 END, execution_priority_score DESC
    """, (project_id,))
    rows = cursor.fetchall()
    print("\nTRACKER ITEMS AFTER COMPLETION:")
    for r in rows:
        print(f"ID {r['id']} | {r['title']} | Status: {r['status']} | Exec: {r['execution_status']} | Priority: {r['execution_priority_score']} | Risk: {r['risk_score']}")
    cursor.close()

conn.close()
