import mysql.connector
import json

db = mysql.connector.connect(
    host="localhost",
    user="root",
    password="",
    database="acse_db"
)

cursor = db.cursor(dictionary=True)
cursor.execute("SELECT * FROM project_activities ORDER BY id DESC LIMIT 5")
print("activities:", json.dumps(cursor.fetchall(), indent=2, default=str))

cursor.execute("SELECT * FROM tracker_items ORDER BY id DESC LIMIT 5")
print("tracker_items:", json.dumps(cursor.fetchall(), indent=2, default=str))

cursor.execute("SELECT * FROM audit_logs ORDER BY id DESC LIMIT 5")
print("audit_logs:", json.dumps(cursor.fetchall(), indent=2, default=str))

cursor.execute("SELECT processing_status, processing_error FROM documents ORDER BY id DESC LIMIT 5")
print("documents:", json.dumps(cursor.fetchall(), indent=2, default=str))

db.close()
