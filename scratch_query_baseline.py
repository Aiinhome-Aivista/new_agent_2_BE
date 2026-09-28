from core.database import get_db_connection, db_cursor

conn = get_db_connection()
with db_cursor(conn) as cur:
    print('=== ALL SCOPE ITEMS PROJECT 86 ===')
    cur.execute('SELECT id, name, scope_type, deadline, category FROM scope_items WHERE project_id = 86 ORDER BY id')
    for r in cur.fetchall():
        print(f"{r['id']}: {r['name']}")




