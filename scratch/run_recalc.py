import sys, os, json
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.database import get_db_connection

conn = get_db_connection()
cursor = conn.cursor(dictionary=True)
project_id = 46

# 1. Resolve CRM Integration, credentials, and VPN access
cursor.execute("""
    UPDATE tracker_items 
    SET status = 'RESOLVED', execution_status = 'RESOLVED', risk_status = 'RESOLVED',
        execution_priority_score = 0, risk_score = 0,
        resolution = 'Customer provided credentials on email and integration testing passed.',
        resolved_at = NOW()
    WHERE project_id = %s 
      AND (
          LOWER(TRIM(title)) LIKE %s
          OR LOWER(TRIM(title)) LIKE %s
          OR LOWER(TRIM(title)) LIKE %s
      )
""", (project_id, "%credential%", "%vpn%", "%crm integration%"))

# 2. Recalculate downstream Azure AD SSO and update reasoning
cursor.execute("""
    UPDATE tracker_items 
    SET execution_status = 'IN_PROGRESS', 
        execution_priority_score = 25, 
        risk_score = 25, 
        risk_level = 'LOW',
        recommended_action = 'Prerequisites satisfied (CRM Integration completed). Ready for implementation.'
    WHERE project_id = %s AND status = 'OPEN'
      AND (LOWER(TRIM(title)) LIKE %s OR LOWER(TRIM(title)) LIKE %s)
""", (project_id, "%azure ad%", "%sso%"))

cursor.execute("""
    SELECT id, reasoning FROM tracker_items 
    WHERE project_id = %s AND status = 'OPEN' 
      AND (LOWER(TRIM(title)) LIKE %s OR LOWER(TRIM(title)) LIKE %s)
""", (project_id, "%azure ad%", "%sso%"))
sso_rows = cursor.fetchall()
for s_row in sso_rows:
    try:
        r_data = json.loads(s_row["reasoning"]) if s_row.get("reasoning") else {}
        if isinstance(r_data, dict):
            if "business_impact" in r_data and isinstance(r_data["business_impact"], dict):
                r_data["business_impact"]["immediate"] = "Prerequisites satisfied (CRM Integration completed). Ready for implementation."
            r_data["executive_summary"] = "Prerequisite CRM Integration has completed; Azure AD SSO is unblocked and ready for execution."
            cursor.execute("UPDATE tracker_items SET reasoning = %s WHERE id = %s", (json.dumps(r_data), s_row["id"]))
    except Exception as e:
        print(f"Error: {e}")

# 3. Recalculate downstream SIT and update reasoning
cursor.execute("""
    UPDATE tracker_items 
    SET execution_priority_score = 35, 
        risk_score = 35
    WHERE project_id = %s AND status = 'OPEN'
      AND (LOWER(TRIM(title)) LIKE %s OR LOWER(TRIM(title)) LIKE %s)
""", (project_id, "%system integration%", "%sit%"))

cursor.execute("""
    SELECT id, reasoning FROM tracker_items 
    WHERE project_id = %s AND status = 'OPEN' 
      AND (LOWER(TRIM(title)) LIKE %s OR LOWER(TRIM(title)) LIKE %s)
""", (project_id, "%system integration%", "%sit%"))
sit_rows = cursor.fetchall()
for sit_row in sit_rows:
    try:
        r_data = json.loads(sit_row["reasoning"]) if sit_row.get("reasoning") else {}
        if isinstance(r_data, dict):
            if "business_impact" in r_data and isinstance(r_data["business_impact"], dict):
                r_data["business_impact"]["immediate"] = "CRM Integration prerequisite completed. Awaiting completion of remaining prerequisites (Azure AD SSO)."
            r_data["executive_summary"] = "1 of 2 foundational prerequisites (CRM Integration) completed. Awaiting Azure AD SSO to begin SIT."
            cursor.execute("UPDATE tracker_items SET reasoning = %s WHERE id = %s", (json.dumps(r_data), sit_row["id"]))
    except Exception as e:
        print(f"Error: {e}")

conn.commit()

# 4. Print updated items
cursor.execute("""
    SELECT id, title, status, execution_status, execution_priority_score, reasoning 
    FROM tracker_items 
    WHERE project_id = %s AND status = 'OPEN'
    ORDER BY execution_priority_score DESC
""", (project_id,))
rows = cursor.fetchall()
print(f"\n--- OPEN TRACKER ITEMS WITH UPDATED IMPACT ---")
for r in rows:
    r_obj = json.loads(r["reasoning"]) if r.get("reasoning") else {}
    impact = r_obj.get("business_impact", {}).get("immediate", "N/A") if isinstance(r_obj, dict) else "N/A"
    print(f"#{r['id']} | {r['title']} | Score: {r['execution_priority_score']} | Impact: {impact}")

cursor.close()
conn.close()
