import os
import uuid
import mysql.connector
import json
from dotenv import load_dotenv

class EntityNormalizerService:
    def __init__(self, db_config=None):
        if not db_config:
            load_dotenv()
            self.db_config = {
                "host": os.getenv("DB_HOST", "localhost"),
                "user": os.getenv("DB_USER", "root"),
                "password": os.getenv("DB_PASSWORD", ""),
                "database": os.getenv("DB_NAME", "pm_database")
            }
        self.db = mysql.connector.connect(**self.db_config)

    def normalize(self, statement: str, project_id: int) -> str:
        """
        Normalizes an entity statement by checking the entity_registry for aliases.
        If a match is found in aliases or canonical_name (case-insensitive), returns the canonical_name.
        If no match is found, registers a new canonical entity and returns the statement.
        """
        if not statement or not statement.strip():
            return statement

        clean_stmt = statement.strip()
        lower_stmt = clean_stmt.lower()
        
        cursor = self.db.cursor(dictionary=True)
        try:
            # Simple check against canonical name
            cursor.execute(
                "SELECT canonical_name, aliases FROM entity_registry WHERE project_id = %s",
                (project_id,)
            )
            rows = cursor.fetchall()
            
            for row in rows:
                if row['canonical_name'].lower() == lower_stmt:
                    return row['canonical_name']
                
                aliases = []
                if row['aliases']:
                    try:
                        aliases = json.loads(row['aliases']) if isinstance(row['aliases'], str) else row['aliases']
                    except Exception:
                        pass
                
                if isinstance(aliases, list):
                    for alias in aliases:
                        if alias.strip().lower() == lower_stmt:
                            return row['canonical_name']
            
            # If we get here, no match found. Insert a new record.
            new_uuid = str(uuid.uuid4())
            cursor.execute(
                "INSERT INTO entity_registry (uuid, project_id, canonical_name, aliases) VALUES (%s, %s, %s, %s)",
                (new_uuid, project_id, clean_stmt, json.dumps([]))
            )
            self.db.commit()
            return clean_stmt
            
        except Exception as e:
            self.db.rollback()
            print(f"Error in normalization: {e}")
            return clean_stmt
        finally:
            cursor.close()

    def __del__(self):
        if hasattr(self, 'db') and self.db.is_connected():
            self.db.close()
