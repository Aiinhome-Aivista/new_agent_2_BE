import os
import sys

# Ensure the project root is in the path so modules can be imported
sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))

from core.database import get_db_connection
from core.risk_config_tables import create_risk_config_tables

def run_migrations_started_at():
    print("Running database migration for started_at column...")
    conn = get_db_connection()
    if not conn:
        print("Migration: Failed to connect.")
        return
    try:
        cursor = conn.cursor()
        cursor.execute("SHOW COLUMNS FROM documents LIKE 'processing_started_at'")
        col_exists = cursor.fetchone()
        if not col_exists:
            print("Migration: Adding 'processing_started_at' column...")
            cursor.execute("ALTER TABLE documents ADD COLUMN processing_started_at TIMESTAMP NULL DEFAULT NULL")
            conn.commit()
            print("Migration: Column added.")
        else:
            print("Migration: Column already exists.")
            
        # Clear stuck processing states
        print("Migration: Resetting stuck PROCESSING documents...")
        cursor.execute("UPDATE documents SET processing_status = 'FAILED', processing_error = 'Server restarted or process crashed', processing_progress = 0, processing_step = 'Failed' WHERE processing_status = 'PROCESSING'")
        conn.commit()
        print("Migration: Reset complete.")
        
        cursor.close()
    except Exception as e:
        print(f"Migration error: {e}")
    finally:
        conn.close()

def run_tracker_migrations():
    print("Running database migration for tracker_items columns...")
    conn = get_db_connection()
    if not conn:
        print("Migration: Failed to connect.")
        return
    try:
        cursor = conn.cursor()
        
        cursor.execute("SHOW COLUMNS FROM tracker_items LIKE 'risk_origin'")
        if not cursor.fetchone():
            print("Migration: Adding 'risk_origin' column...")
            cursor.execute("ALTER TABLE tracker_items ADD COLUMN risk_origin VARCHAR(255) NULL DEFAULT NULL")
            conn.commit()
            
        cursor.execute("SHOW COLUMNS FROM tracker_items LIKE 'previous_highest_score'")
        if not cursor.fetchone():
            print("Migration: Adding 'previous_highest_score' column...")
            cursor.execute("ALTER TABLE tracker_items ADD COLUMN previous_highest_score INT NULL DEFAULT 0")
            conn.commit()

        cursor.execute("SHOW COLUMNS FROM tracker_items LIKE 'execution_status'")
        if not cursor.fetchone():
            print("Migration: Adding decoupled status columns...")
            cursor.execute("ALTER TABLE tracker_items ADD COLUMN execution_status VARCHAR(50) NULL DEFAULT 'NOT_STARTED'")
            cursor.execute("ALTER TABLE tracker_items ADD COLUMN risk_status VARCHAR(50) NULL DEFAULT 'OPEN'")
            cursor.execute("ALTER TABLE tracker_items ADD COLUMN graph_role VARCHAR(100) NULL DEFAULT 'DOWNSTREAM_ACTIVITY'")
            cursor.execute("ALTER TABLE tracker_items ADD COLUMN canonical_id VARCHAR(255) NULL DEFAULT ''")
            cursor.execute("ALTER TABLE tracker_items ADD COLUMN recommended_action TEXT NULL")
            conn.commit()
            
        print("Migration: Updating item_type ENUM...")
        cursor.execute("ALTER TABLE tracker_items MODIFY COLUMN item_type enum('ACTIVITY','NEW_REQUEST','CHANGE_REQUEST','BLOCKER','ACTION_ITEM','DECISION','RISK_MENTIONED','DEPENDENCY') NOT NULL")
        conn.commit()
            
        cursor.close()
    except Exception as e:
        print(f"Tracker Migration error: {e}")
    finally:
        conn.close()

def create_rag_tables():
    conn = get_db_connection()
    if not conn:
        print("RAG Table Setup: Failed to connect to database")
        return
    cursor = conn.cursor()
    try:
        # Create sessions table
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS rag_chat_sessions (
            id BIGINT PRIMARY KEY AUTO_INCREMENT,
            project_id BIGINT NOT NULL,
            session_name VARCHAR(255) NOT NULL,
            created_by BIGINT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
            CONSTRAINT fk_rag_session_project FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE,
            CONSTRAINT fk_rag_session_user FOREIGN KEY (created_by) REFERENCES users(id) ON DELETE CASCADE
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
        """)
        
        # Create messages table
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS rag_chat_messages (
            id BIGINT PRIMARY KEY AUTO_INCREMENT,
            session_id BIGINT NOT NULL,
            role ENUM('USER', 'ASSISTANT') NOT NULL,
            content TEXT NOT NULL,
            citations_json JSON NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            CONSTRAINT fk_rag_message_session FOREIGN KEY (session_id) REFERENCES rag_chat_sessions(id) ON DELETE CASCADE
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
        """)
        conn.commit()
        print("RAG tables successfully checked/created.")
    except Exception as e:
        print(f"RAG Tables Setup Error: {e}")
        conn.rollback()
    finally:
        cursor.close()
        conn.close()

if __name__ == "__main__":
    print("--- Starting Database Initialization ---")
    create_risk_config_tables()
    create_rag_tables()
    run_migrations_started_at()
    run_tracker_migrations()
    print("--- Database Initialization Complete ---")
