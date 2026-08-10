import mysql.connector
import json

db_config = {
    'user': 'root',
    'password': '',
    'host': '127.0.0.1',
    'database': 'project_dashboard',
    'use_pure': False
}

try:
    conn = mysql.connector.connect(**db_config)
    cursor = conn.cursor(dictionary=True)
    
    # Check documents
    cursor.execute("SELECT id, project_id, processing_status, processing_step, processing_progress FROM documents ORDER BY id DESC LIMIT 5")
    docs = cursor.fetchall()
    print("Recent Documents:")
    print(json.dumps(docs, indent=2))
    
    # Check tracker items
    cursor.execute("SELECT id, project_id, source_document_id, deliverable_name, status, execution_status, execution_priority, risk_severity, risk_level, confidence FROM tracker_items ORDER BY id DESC LIMIT 5")
    items = cursor.fetchall()
    print("\nRecent Tracker Items:")
    print(json.dumps(items, indent=2))
    
    cursor.close()
    conn.close()
except Exception as e:
    print("DB Error:", e)
