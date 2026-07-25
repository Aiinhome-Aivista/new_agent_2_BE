import mysql.connector
from core.config import settings

def main():
    try:
        db = mysql.connector.connect(
            host=settings.DB_HOST,
            port=settings.DB_PORT,
            database=settings.DB_NAME,
            user=settings.DB_USER,
            password=settings.DB_PASSWORD,
        )
        cursor = db.cursor(dictionary=True)
        
        print("=== PROJECTS ===")
        cursor.execute("SELECT id, project_name, client_name, monitoring_status FROM projects")
        for r in cursor.fetchall():
            print(r)
            
        print("\n=== DOCUMENTS ===")
        cursor.execute("SELECT id, project_id, document_name, document_type, processing_status FROM documents")
        for r in cursor.fetchall():
            print(r)
            
        print("\n=== BASELINES ===")
        cursor.execute("SELECT id, project_id, version, status FROM scope_baselines")
        for r in cursor.fetchall():
            print(r)
            
        print("\n=== DELIVERABLES ===")
        cursor.execute("SELECT id, baseline_id, name, deadline, owner FROM deliverables")
        for r in cursor.fetchall():
            print(r)
            
        cursor.close()
        db.close()
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    main()
