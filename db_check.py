import sys
import os
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from core.database import get_db_connection

def main():
    conn = get_db_connection()
    if not conn:
        print("Failed to connect")
        return
    cursor = conn.cursor(dictionary=True)
    cursor.execute("DESCRIBE scope_items;")
    for row in cursor.fetchall():
        print(row)
    cursor.close()
    conn.close()

if __name__ == "__main__":
    main()
