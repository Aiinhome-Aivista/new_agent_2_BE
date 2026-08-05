# pyrefly: ignore [missing-import]
from fastapi import APIRouter, Depends, HTTPException, Query
from api.dependencies.auth import get_current_user
from core.database import get_db
import mysql.connector
import os
import json

router = APIRouter(prefix="/projects/{project_id}/registers", tags=["Registers"])

@router.get("/")
def get_project_registers(
    project_id: int,
    db: mysql.connector.connection.MySQLConnection = Depends(get_db),
    current_user: dict = Depends(get_current_user)
):
    """
    Returns the new nested PMO Register Architecture format.
    """
    cursor = db.cursor(dictionary=True)
    try:
        # 1. Milestones
        cursor.execute("SELECT * FROM milestones WHERE project_id = %s", (project_id,))
        milestones = cursor.fetchall()

        # 1. Risks (Fallback to tracker_items for risks)
        cursor.execute("SELECT * FROM tracker_items WHERE project_id = %s", (project_id,))
        tracker_items = cursor.fetchall()
        risks = [t for t in tracker_items if t.get('item_type') == 'RISK']
        
        # 2. Actions
        cursor.execute("SELECT * FROM action_items WHERE project_id = %s", (project_id,))
        actions = cursor.fetchall()
        
        # 3. Dependencies
        cursor.execute("SELECT * FROM dependencies WHERE project_id = %s", (project_id,))
        dependencies = cursor.fetchall()
        
        # 4. Changes
        cursor.execute("SELECT * FROM change_requests WHERE project_id = %s", (project_id,))
        changes = cursor.fetchall()
        
        # 5. Issues
        cursor.execute("SELECT * FROM issues WHERE project_id = %s", (project_id,))
        issues = cursor.fetchall()
        
        # 6. Entity Links
        cursor.execute("SELECT * FROM entity_links WHERE project_id = %s AND is_active = TRUE", (project_id,))
        links = cursor.fetchall()
        
        # Helper to parse metadata
        def _parse_meta(items):
            for i in items:
                if 'metadata' in i and isinstance(i['metadata'], str):
                    try:
                        i['metadata'] = json.loads(i['metadata'])
                    except Exception:
                        pass
                
        _parse_meta(milestones)
        _parse_meta(actions)
        _parse_meta(dependencies)
        _parse_meta(changes)
        _parse_meta(issues)
        
        return {
            "registers": {
                "milestones": milestones,
                "risks": risks,
                "actions": actions,
                "dependencies": dependencies,
                "changes": changes,
                "issues": issues
            },
            "links": links
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cursor.close()
