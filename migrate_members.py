from core.database import get_db_connection

def migrate():
    conn = get_db_connection()
    if not conn:
        print("Could not connect to DB")
        return
    cursor = conn.cursor()
    
    try:
        cursor.execute("ALTER TABLE stakeholders ADD COLUMN user_id BIGINT NULL;")
        print("Added user_id column")
    except Exception as e:
        print("user_id status:", e)
        
    try:
        cursor.execute("ALTER TABLE stakeholders ADD COLUMN updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP;")
        print("Added updated_at column")
    except Exception as e:
        print("updated_at status:", e)
        
    conn.commit()
    cursor.close()
    conn.close()

if __name__ == "__main__":
    migrate()
