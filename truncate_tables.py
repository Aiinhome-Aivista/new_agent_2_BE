import sys
import os
import shutil
import mysql.connector
# pyrefly: ignore [missing-import]
import chromadb
# pyrefly: ignore [missing-import]
from chromadb.config import Settings as ChromaSettings

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
    
    # Dynamically fetch all tables and truncate them
    print("Fetching all tables to truncate...")
    try:
        cursor.execute("SHOW TABLES")
        tables = [row[0] for row in cursor.fetchall()]
        
        cursor.execute("SET FOREIGN_KEY_CHECKS = 0")
        preserve_tables = [
            'users', 
            'master_document_types',
            'risk_parameter_config',
            'risk_threshold_config',
            'business_rule_config',
            'impact_matrix',
            'alert_rule_config'
        ]
        for table in tables:
            if table.lower() not in preserve_tables:
                print(f"Truncating table: {table}")
                cursor.execute(f"TRUNCATE TABLE {table}")
        cursor.execute("SET FOREIGN_KEY_CHECKS = 1")
        db.commit()
        print("MySQL tables truncated successfully (preserving 'users' and 'master_document_types').")
    except Exception as e:
        print(f"Error truncating tables: {e}")
        
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
        db_path = os.path.abspath(settings.CHROMA_PATH)
        # We try to completely remove the physical files so the folder is clean
        if os.path.exists(db_path):
            for item in os.listdir(db_path):
                # Skip bm25 folder as it is handled separately
                if item == "bm25":
                    continue
                item_path = os.path.join(db_path, item)
                try:
                    if os.path.isdir(item_path):
                        shutil.rmtree(item_path)
                    else:
                        os.remove(item_path)
                    print(f"Deleted: {item_path}")
                except Exception as e:
                    print(f"Note: Could not physical delete {item} (might be locked by backend server). Falling back to chromadb API deletion...")
                    # Fallback to API deletion if file is locked
                    client = chromadb.PersistentClient(path=db_path, settings=ChromaSettings(anonymized_telemetry=False))
                    for col in client.list_collections():
                        try:
                            # Handle both object return type and string return type
                            col_name = col.name if hasattr(col, 'name') else col
                            print(f"Deleting collection via API: {col_name}")
                            client.delete_collection(col_name)
                        except:
                            pass
    except Exception as e:
        print(f"Warning: Could not clear ChromaDB: {e}")

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
