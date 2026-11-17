"""DB migration: add HITL support columns."""
import sys
sys.path.insert(0, '.')
import mysql.connector
from core.config import settings

db = mysql.connector.connect(
    host=settings.DB_HOST,
    database=settings.DB_NAME,
    user=settings.DB_USER,
    password=settings.DB_PASSWORD
)
c = db.cursor()

# 1. Add hitl_conflict column to scope_baselines
try:
    c.execute(
        "ALTER TABLE scope_baselines ADD COLUMN hitl_conflict JSON NULL DEFAULT NULL"
    )
    print("Added hitl_conflict column to scope_baselines")
except Exception as e:
    print(f"hitl_conflict column: {e}")

# 2. Add PENDING_HITL_RESOLUTION to projects.monitoring_status enum
try:
    sql = (
        "ALTER TABLE projects MODIFY COLUMN monitoring_status "
        "ENUM('DRAFT','BASELINE_PENDING_REVIEW','PENDING_HITL_RESOLUTION','ACTIVE','PAUSED','CLOSED') "
        "NOT NULL DEFAULT 'DRAFT'"
    )
    c.execute(sql)
    print("Updated monitoring_status enum")
except Exception as e:
    print(f"monitoring_status enum: {e}")

db.commit()
c.close()
db.close()
print("Migration complete")
