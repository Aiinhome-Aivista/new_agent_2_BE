import sys
import os
import getpass

# Add backend directory to sys.path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.database import get_db_connection
from core.security import get_password_hash

def create_admin():
    print("--- Create Admin User ---")
    name = input("Admin Name: ")
    email = input("Admin Email: ")
    password = getpass.getpass("Admin Password: ")
    
    if not name or not email or not password:
        print("All fields are required!")
        return

    hashed_password = get_password_hash(password)
    
    conn = get_db_connection()
    if not conn:
        print("Failed to connect to the database. Check your .env configuration and MySQL server.")
        return
        
    try:
        cursor = conn.cursor()
        
        # Check if user already exists
        cursor.execute("SELECT id FROM users WHERE email = %s", (email,))
        if cursor.fetchone():
            print("User with this email already exists.")
            return
            
        sql = """
        INSERT INTO users (name, email, password_hash, role, is_active)
        VALUES (%s, %s, %s, %s, %s)
        """
        val = (name, email, hashed_password, 'ADMIN', True)
        
        cursor.execute(sql, val)
        conn.commit()
        
        print("Admin user created successfully.")
        
    except Exception as e:
        print(f"Error creating admin user: {e}")
    finally:
        if conn.is_connected():
            cursor.close()
            conn.close()

if __name__ == "__main__":
    create_admin()
