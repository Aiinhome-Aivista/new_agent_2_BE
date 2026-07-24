import sys
import os
import mysql.connector

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from core.config import settings

def alter_schema():
    print(f"Connecting to database {settings.DB_NAME} at {settings.DB_HOST}...")
    db = mysql.connector.connect(
        host=settings.DB_HOST,
        user=settings.DB_USER,
        password=settings.DB_PASSWORD,
        database=settings.DB_NAME
    )
    cursor = db.cursor()
    
    alters = [
        "ALTER TABLE scope_items ADD COLUMN milestone VARCHAR(255) NULL;",
        "ALTER TABLE scope_items ADD COLUMN deadline_text VARCHAR(255) NULL;",
        "ALTER TABLE scope_items ADD COLUMN extraction_confidence DECIMAL(5,4) NULL;",
        "ALTER TABLE scope_items ADD COLUMN extraction_method VARCHAR(50) NULL;"
    ]
    
    for query in alters:
        try:
            print(f"Executing: {query}")
            cursor.execute(query)
        except mysql.connector.Error as err:
            if err.errno == 1060: # Duplicate column name
                print("Column already exists, skipping.")
            else:
                print(f"Error: {err}")
                
    # Also update schema.sql file
    schema_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'sql', 'schema.sql')
    with open(schema_path, 'r', encoding='utf-8') as f:
        content = f.read()
    
    if "milestone VARCHAR(255) NULL" not in content:
        old_cols = "    confidence DECIMAL(5,4),\n    deadline DATE NULL,\n"
        new_cols = "    confidence DECIMAL(5,4),\n    milestone VARCHAR(255) NULL,\n    deadline_text VARCHAR(255) NULL,\n    deadline DATE NULL,\n    extraction_confidence DECIMAL(5,4) NULL,\n    extraction_method VARCHAR(50) NULL,\n"
        content = content.replace(old_cols, new_cols)
        with open(schema_path, 'w', encoding='utf-8') as f:
            f.write(content)
        print("Updated schema.sql")

    db.commit()
    cursor.close()
    db.close()
    print("Done!")

if __name__ == "__main__":
    alter_schema()
