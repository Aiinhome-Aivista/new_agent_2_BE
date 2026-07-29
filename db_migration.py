import sys
import os
import mysql.connector

# Add project root to sys.path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from core.database import get_db_connection

def migrate():
    conn = get_db_connection()
    if not conn:
        print("Failed to connect to database.")
        return
        
    cursor = conn.cursor()
    
    try:
        # 1. Create entity_types table
        print("Creating entity_types table...")
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS entity_types (
                id INT AUTO_INCREMENT PRIMARY KEY,
                name VARCHAR(50) NOT NULL UNIQUE
            )
        """)
        
        # 2. Insert master data
        entities = [
            'FUNCTIONAL_SCOPE', 'DELIVERABLE', 'MILESTONE', 
            'TECH_STACK', 'CLIENT_DEPENDENCY', 'STAKEHOLDER', 
            'LEGAL', 'COMMERCIAL', 'ACTOR'
        ]
        
        print("Inserting master data into entity_types...")
        for entity in entities:
            try:
                cursor.execute("INSERT IGNORE INTO entity_types (name) VALUES (%s)", (entity,))
            except mysql.connector.Error as err:
                print(f"Error inserting {entity}: {err}")
                
        # 3. Alter scope_items to add entity_type_id
        print("Checking scope_items schema...")
        cursor.execute("SHOW COLUMNS FROM scope_items LIKE 'entity_type_id'")
        result = cursor.fetchone()
        
        if not result:
            print("Adding entity_type_id to scope_items...")
            cursor.execute("""
                ALTER TABLE scope_items 
                ADD COLUMN entity_type_id INT NULL,
                ADD CONSTRAINT fk_scope_entity_type 
                FOREIGN KEY (entity_type_id) REFERENCES entity_types(id)
            """)
        else:
            print("entity_type_id already exists in scope_items.")
            
        print("Checking metadata_json in scope_items...")
        cursor.execute("SHOW COLUMNS FROM scope_items LIKE 'metadata_json'")
        if not cursor.fetchone():
            print("Adding metadata_json to scope_items...")
            cursor.execute("ALTER TABLE scope_items ADD COLUMN metadata_json JSON NULL")
            
        print("Checking metadata_json in deliverables...")
        cursor.execute("SHOW COLUMNS FROM deliverables LIKE 'metadata_json'")
        if not cursor.fetchone():
            print("Adding metadata_json to deliverables...")
            cursor.execute("ALTER TABLE deliverables ADD COLUMN metadata_json JSON NULL")
            
        # 4. Alter scope_baselines table to add versioning columns
        print("Checking scope_baselines schema for versioning...")
        cursor.execute("SHOW COLUMNS FROM scope_baselines LIKE 'parser_version'")
        if not cursor.fetchone():
            print("Adding versioning columns to scope_baselines...")
            cursor.execute("""
                ALTER TABLE scope_baselines 
                ADD COLUMN parser_version VARCHAR(50) NULL,
                ADD COLUMN layout_version VARCHAR(50) NULL,
                ADD COLUMN extractor_version VARCHAR(50) NULL,
                ADD COLUMN llm_prompt_version VARCHAR(50) NULL
            """)
        else:
            print("Versioning columns already exist in scope_baselines.")
            
        conn.commit()
        print("Migration successful!")
        
    except mysql.connector.Error as err:
        print(f"Migration failed: {err}")
        conn.rollback()
    finally:
        cursor.close()
        conn.close()

if __name__ == "__main__":
    migrate()
