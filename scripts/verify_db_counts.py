import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from core.database import get_db_connection

conn = get_db_connection()
cursor = conn.cursor(dictionary=True)

for pid in [126, 125]:
    cursor.execute('SELECT id, version, status, source_document_id FROM scope_baselines WHERE project_id = %s ORDER BY version DESC LIMIT 1', (pid,))
    b = cursor.fetchone()
    if not b:
        print(f"No baseline for project {pid}")
        continue
    bid = b['id']
    print("=" * 70)
    print(f"PROJECT {pid} | Baseline ID: {bid} | Version: {b['version']} | Status: {b['status']} | Doc ID: {b['source_document_id']}")
    
    cursor.execute('SELECT record_category, scope_type, work_item_eligible, COUNT(*) as cnt FROM scope_items WHERE baseline_id = %s GROUP BY record_category, scope_type, work_item_eligible ORDER BY record_category', (bid,))
    for r in cursor.fetchall():
        print(f"  Category: {str(r['record_category']):<25} | ScopeType: {r['scope_type']} | Eligible: {r['work_item_eligible']} | Count: {r['cnt']}")

    cursor.execute('SELECT COUNT(*) as total FROM scope_items WHERE baseline_id = %s', (bid,))
    print(f"  Total scope_items rows: {cursor.fetchone()['total']}")

    cursor.execute('SELECT COUNT(*) as cnt FROM project_milestones WHERE baseline_id = %s', (bid,))
    m_cnt = cursor.fetchone()['cnt']
    cursor.execute('SELECT COUNT(*) as cnt FROM project_milestones WHERE baseline_id = %s AND (is_payment_milestone = 1 OR payment_percentage > 0)', (bid,))
    pm_cnt = cursor.fetchone()['cnt']
    cursor.execute('SELECT COUNT(*) as cnt FROM baseline_commitments WHERE baseline_id = %s', (bid,))
    c_cnt = cursor.fetchone()['cnt']
    cursor.execute('SELECT COUNT(*) as cnt FROM baseline_dependencies WHERE baseline_id = %s', (bid,))
    d_cnt = cursor.fetchone()['cnt']
    print(f"  Registers -> Milestones: {m_cnt} (Payment Milestones: {pm_cnt}) | Commitments: {c_cnt} | Dependencies: {d_cnt}")

cursor.close()
conn.close()

