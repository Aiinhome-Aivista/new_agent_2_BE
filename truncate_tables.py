import sys
import os
import shutil
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
    
    # Call Stored Procedure
    print("Calling stored procedure truncate_all_tables_except_users_and_master()...")
    try:
        cursor.callproc("truncate_all_tables_except_users_and_master")
        db.commit()
        print("MySQL tables truncated successfully (preserving 'users' and 'master_document_types').")
    except Exception as e:
        print(f"Error executing stored procedure: {e}")
        print("Falling back to manual table truncation...")
        # Fallback manual logic if SP doesn't exist
        cursor.execute("SHOW TABLES")
        tables = [row[0] for row in cursor.fetchall()]
        cursor.execute("SET FOREIGN_KEY_CHECKS = 0")
        for table in tables:
            if table.lower() not in ['users', 'master_document_types']:
                print(f"Truncating table: {table}")
                cursor.execute(f"TRUNCATE TABLE {table}")
        cursor.execute("SET FOREIGN_KEY_CHECKS = 1")
        db.commit()
        print("Manual fallback truncation completed.")
        
    cursor.close()
    db.close()
    
    # Clear Upload Directory
    upload_dir = os.path.abspath(settings.UPLOAD_PATH)
    print(f"Clearing upload directory: {upload_dir}...")
    if os.path.exists(upload_dir):
        for item in os.listdir(upload_dir):
            item_path = os.path.join(upload_dir, item)
            try:
                if os.path.isdir(item_path):
                    shutil.rmtree(item_path)
                else:
                    os.remove(item_path)
            except Exception as e:
                print(f"Warning: Could not remove upload item {item_path}: {e}")
    else:
        os.makedirs(upload_dir, exist_ok=True)
        
    # Clear ChromaDB Collections
    print("Clearing ChromaDB collections...")
    try:
        # pyrefly: ignore [missing-import]
        import chromadb
        # pyrefly: ignore [missing-import]
        from chromadb.config import Settings as ChromaSettings
        db_path = os.path.abspath(settings.CHROMA_PATH)
        client = chromadb.PersistentClient(path=db_path, settings=ChromaSettings(anonymized_telemetry=False))
        for col in client.list_collections():
            print(f"Deleting collection: {col.name}")
            client.delete_collection(col.name)
    except Exception as e:
        print(f"Warning: Could not clear ChromaDB collections: {e}")

    # Clear BM25 Index
    bm25_dir = os.path.join(os.path.abspath(settings.CHROMA_PATH), "bm25")
    print(f"Clearing BM25 index directory: {bm25_dir}...")
    if os.path.exists(bm25_dir):
        for item in os.listdir(bm25_dir):
            item_path = os.path.join(bm25_dir, item)
            try:
                if os.path.isdir(item_path):
                    shutil.rmtree(item_path)
                else:
                    os.remove(item_path)
            except Exception as e:
                print(f"Warning: Could not remove BM25 item {item_path}: {e}")
    else:
        os.makedirs(bm25_dir, exist_ok=True)

    print("System reset complete. Ready for new project and documents!")

if __name__ == "__main__":
    truncate_all_except_users()
