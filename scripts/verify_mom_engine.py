import os
import sys
import json
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), '.env'))

from core.database import get_db_connection
from agents.orchestrator_agent import OrchestratorAgent
from datetime import datetime

MOM_WEEK_14 = """
Project ACSE - Week 14 Status Report
-------------------------------------
Activities:
- CRM Integration: In Progress. API integration in progress but blocked. Missing production VPN credentials. Progress: 40%.
- Azure AD SSO: Pending kickoff. Progress: 0%.
"""

MOM_WEEK_18 = """
Project ACSE - Week 18 Status Report
-------------------------------------
Activities:
- CRM Integration: Completed. Production VPN received and API integration successful. Progress: 100%.
- Azure AD SSO: In Progress. Development started. Progress: 60%.
"""

MOM_WEEK_24 = """
Project ACSE - Week 24 Status Report
-------------------------------------
Activities:
- Azure AD SSO: Completed. Progress: 100%.
- SIT: Starting testing phase. Progress: 10%.
"""

MOM_WEEK_30 = """
Project ACSE - Week 30 Status Report
-------------------------------------
Activities:
- CRM Integration: VPN Expired. Blocked until renewed.
- SIT: Completed. Progress: 100%.
"""

MOM_WEEK_31 = """
Project ACSE - Week 31 Status Report
-------------------------------------
Activities:
- SIT: Completed successfully. Progress: 100%.
"""

MOM_WEEK_32 = """
Project ACSE - Week 32 Status Report
-------------------------------------
Activities:
- CRM Integration: VPN Received Again. Issue resolved.
"""

def print_state(conn, label):
    cursor = conn.cursor(dictionary=True)
    print(f"\n--- {label} ---")
    cursor.execute("SELECT name, status FROM project_milestones WHERE project_id = 1")
    milestones = cursor.fetchall()
    print("MILESTONES:")
    for m in milestones:
        if m['status'] != 'PENDING':
            print(f"  {m['name']}: {m['status']}")
            
    cursor.execute("SELECT id, title, status, risk_score, reasoning FROM tracker_items WHERE project_id = 1 ORDER BY id ASC")
    trackers = cursor.fetchall()
    print("TRACKER ITEMS:")
    for t in trackers:
        print(f"  [{t['id']}] {t['title']} | Status: {t['status']} | Score: {t['risk_score']}")
        print(f"      Reasoning: {t['reasoning'][:50]}...")
        
    cursor.execute("SELECT entity_id, action, details_json FROM audit_logs WHERE project_id = 1 ORDER BY created_at ASC")
    logs = cursor.fetchall()
    print("AUDIT LOGS:")
    for l in logs:
        dets = json.loads(l['details_json'])
        print(f"  [Item {l['entity_id']}] {l['action']} | Score: {dets.get('risk_score', 'N/A')}")
        
    cursor.close()

def get_or_create_document(conn, doc_id, filename, uploaded_at):
    cursor = conn.cursor()
    cursor.execute("SELECT id FROM documents WHERE id = %s", (doc_id,))
    if not cursor.fetchone():
        cursor.execute("INSERT INTO documents (id, project_id, document_name, document_type, processing_status, storage_key, uploaded_by, uploaded_at) VALUES (%s, 1, %s, 'MOM', 'COMPLETED', %s, 1, %s)", (doc_id, filename, filename, uploaded_at))
    conn.commit()
    cursor.close()

def main():
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    
    # Cleanup previous test state
    cursor.execute("DELETE FROM deliverable_progress WHERE project_id = 1")
    cursor.execute("DELETE FROM audit_logs WHERE project_id = 1")
    cursor.execute("DELETE FROM tracker_items WHERE project_id = 1")
    cursor.execute("DELETE FROM risk_evaluations WHERE project_id = 1")
    cursor.execute("UPDATE project_milestones SET status = 'PENDING' WHERE project_id = 1")
    conn.commit()
    
    get_or_create_document(conn, 101, "week14.txt", "2026-08-01 10:00:00")
    get_or_create_document(conn, 103, "week18.txt", "2026-08-29 10:00:00")
    get_or_create_document(conn, 106, "week24.txt", "2026-10-10 10:00:00")
    get_or_create_document(conn, 107, "week30.txt", "2026-11-21 10:00:00")
    get_or_create_document(conn, 108, "week31.txt", "2026-11-28 10:00:00")
    get_or_create_document(conn, 109, "week32.txt", "2026-12-05 10:00:00")
    
    # Test A: Week 14 to Week 18 (CRM In Progress -> CRM Completed)
    print(">>> Uploading Week 14 MoM (Test A)...")
    OrchestratorAgent.run_workflow(1, 101, MOM_WEEK_14, cursor)
    conn.commit()
    print_state(conn, "State after Week 14 (CRM In Progress)")
    
    # Test B: Week 18 (CRM Completed, Azure Started, SIT should be blocked)
    print(">>> Uploading Week 18 MoM (Test B)...")
    OrchestratorAgent.run_workflow(1, 103, MOM_WEEK_18, cursor)
    conn.commit()
    print_state(conn, "State after Week 18 (CRM Completed, Azure In Progress, SIT Blocked)")
    
    # Test C: Week 24 (Azure Completed, SIT Ready)
    print(">>> Uploading Week 24 MoM (Test C)...")
    OrchestratorAgent.run_workflow(1, 106, MOM_WEEK_24, cursor)
    conn.commit()
    print_state(conn, "State after Week 24 (Azure Completed, SIT Ready)")
    
    # Test D: Week 31 (SIT Completed, UAT Ready)
    print(">>> Uploading Week 31 MoM (Test D)...")
    OrchestratorAgent.run_workflow(1, 108, MOM_WEEK_31, cursor)
    conn.commit()
    print_state(conn, "State after Week 31 (SIT Completed, UAT Ready)")
    
    # Test E: Re-upload Week 18 (Stale Document Protection)
    print(">>> Uploading Week 18 MoM AGAIN (Test E: Stale Document Protection)...")
    OrchestratorAgent.run_workflow(1, 103, MOM_WEEK_18, cursor)
    conn.commit()
    print_state(conn, "State after Week 18 Re-upload (No regressions expected)")
    
    # Test F & G: Week 30 VPN Expired, Week 32 VPN Received Again (Recurrence handling)
    print(">>> Uploading Week 30 MoM (Test F: VPN Expired)...")
    OrchestratorAgent.run_workflow(1, 107, MOM_WEEK_30, cursor)
    conn.commit()
    print_state(conn, "State after Week 30 (VPN Expired New Risk)")
    
    print(">>> Uploading Week 32 MoM (Test G: VPN Received Again)...")
    OrchestratorAgent.run_workflow(1, 109, MOM_WEEK_32, cursor)
    conn.commit()
    print_state(conn, "State after Week 32 (VPN Received Again, Second Risk Resolved)")
    
    conn.close()

if __name__ == "__main__":
    main()
