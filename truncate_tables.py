import sys
import os
import mysql.connector

# Add backend directory to sys.path to allow importing core
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from core.config import settings

def truncate_all_except_users():
    print(f"Connecting to database {settings.DB_NAME} at {settings.DB_HOST}...")
    db = mysql.connector.connect(
        host=settings.DB_HOST,
        user=settings.DB_USER,
        password=settings.DB_PASSWORD,
        database=settings.DB_NAME
    )
    cursor = db.cursor()
    
    # Get all tables
    cursor.execute("SHOW TABLES")
    tables = [row[0] for row in cursor.fetchall()]
    
    # Disable foreign key checks to allow truncation
    cursor.execute("SET FOREIGN_KEY_CHECKS = 0")
    
    truncated_count = 0
    for table in tables:
        if table.lower() != 'users':
            print(f"Truncating table: {table}")
            cursor.execute(f"TRUNCATE TABLE {table}")
            truncated_count += 1
            
    # Re-enable foreign key checks
    cursor.execute("SET FOREIGN_KEY_CHECKS = 1")
    
    db.commit()
    cursor.close()
    db.close()
    print(f"Successfully truncated {truncated_count} tables. 'users' table was preserved.")

if __name__ == "__main__":
    truncate_all_except_users()
