import mysql.connector

conn = mysql.connector.connect(
    host='187.127.163.17', port=3306,
    user='aiinhome_3', password='Aiin@2026',
    database='acse_db'
)
cur = conn.cursor()

cur.execute('SHOW COLUMNS FROM scope_items')
existing = {row[0] for row in cur.fetchall()}
print('Existing cols count:', len(existing))

new_cols = [
    ("estimated_start_date",   "DATE NULL"),
    ("estimated_deadline",     "DATE NULL"),
    ("date_provenance",        "VARCHAR(30) NULL"),
    ("estimation_confidence",  "VARCHAR(20) NULL"),
    ("estimation_basis",       "TEXT NULL"),
    ("plan_assumptions",       "TEXT NULL"),
    ("estimated_effort_low",   "DECIMAL(6,2) NULL"),
    ("estimated_effort_likely","DECIMAL(6,2) NULL"),
    ("estimated_effort_high",  "DECIMAL(6,2) NULL"),
    ("baseline_approval_status","VARCHAR(20) NULL DEFAULT 'PENDING_REVIEW'"),
]

for col_name, col_def in new_cols:
    if col_name not in existing:
        sql = f"ALTER TABLE scope_items ADD COLUMN {col_name} {col_def}"
        print(f"  Adding {col_name}...")
        cur.execute(sql)
        conn.commit()
        print(f"    -> OK")
    else:
        print(f"  {col_name} already exists, skipping.")

print("Migration complete.")
cur.close()
conn.close()
