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
        # 1. Milestones (from active project_milestones table)
        cursor.execute("SELECT * FROM project_milestones WHERE project_id = %s", (project_id,))
        milestones = cursor.fetchall()

        # 2. Tracker items (Actions, Risks, Dependencies, Changes, Issues from active tracker_items table)
        cursor.execute("SELECT * FROM tracker_items WHERE project_id = %s", (project_id,))
        tracker_items = cursor.fetchall()
        
        risks = [t for t in tracker_items if t.get('item_type') == 'RISK' or (t.get('risk_score') or 0) > 0]
        actions = [t for t in tracker_items if t.get('item_type') == 'ACTION_ITEM']
        dependencies = [t for t in tracker_items if t.get('item_type') == 'DEPENDENCY']
        changes = [t for t in tracker_items if t.get('item_type') in ('CHANGE_REQUEST', 'NEW_REQUEST')]
        issues = [t for t in tracker_items if t.get('item_type') == 'BLOCKER']
        
        # 3. Entity Links
        cursor.execute("SELECT * FROM entity_links WHERE project_id = %s AND is_active = TRUE", (project_id,))
        links = cursor.fetchall()
        
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
