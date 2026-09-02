# pyrefly: ignore [missing-import]
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from typing import List, Optional
from core.database import get_db
from api.dependencies.auth import get_current_user, require_roles, verify_project_access
from repositories.tracker_repository import TrackerRepository
from repositories.baseline_repository import BaselineRepository
import mysql.connector
import json

# Ensure resolved columns exist in tracker_items table using Repository helper
TrackerRepository.ensure_schema_resolved_columns()

router = APIRouter()

@router.get("/")
def get_tracker_items(project_id: int, current_user: dict = Depends(get_current_user), db: mysql.connector.connection.MySQLConnection = Depends(get_db)):
    verify_project_access(project_id, current_user, db)
    
    # Auto-synchronize tracker items with active/completed baseline deliverables
    try:
        from api.routes.baseline import _rebuild_graph_and_recalculate
        cursor = db.cursor(dictionary=True)
        _rebuild_graph_and_recalculate(cursor, project_id, None)
        db.commit()
    except Exception as e:
        print(f"[TRACKER SYNC WARNING] Auto-sync on GET failed: {e}")

    items = TrackerRepository.get_tracker_items(db, project_id)
    return {"success": True, "data": items}

class ResolutionUpdate(BaseModel):
    resolution: str
    status: str

@router.post("/{item_id}/resolve")
def resolve_tracker_item(project_id: int, item_id: int, update: ResolutionUpdate, current_user: dict = Depends(require_roles(["ENGAGEMENT_MANAGER", "PROJECT_LEAD", "PMO_REVIEWER"])), db: mysql.connector.connection.MySQLConnection = Depends(get_db)):
    verify_project_access(project_id, current_user, db)
    
    item = TrackerRepository.get_tracker_item_by_id_and_project(db, item_id, project_id)
    if not item:
        raise HTTPException(status_code=404, detail="Item not found")
        
    TrackerRepository.resolve_item(db, item_id, update.resolution, update.status, current_user["id"])
    
    # If the tracker item is associated with a scope item (reference_id) or title match, mark the scope item as COMPLETED
    if item.get("reference_id"):
        BaselineRepository.update_scope_item_completion(db, item["reference_id"], project_id, "COMPLETED")
    
    # Also synchronize any matching scope_items by title
    try:
        from api.routes.baseline import _is_title_match
        cursor_sync = db.cursor(dictionary=True)
        cursor_sync.execute("SELECT id, name, scope_item_normalized FROM scope_items WHERE project_id = %s AND (completion_status = 'ACTIVE' OR completion_status IS NULL)", (project_id,))
        active_scopes = cursor_sync.fetchall() or []
        for sc in active_scopes:
            sc_name = sc.get("scope_item_normalized") or sc["name"]
            if _is_title_match(item.get("title", ""), sc_name):
                cursor_sync.execute("UPDATE scope_items SET completion_status = 'COMPLETED' WHERE id = %s", (sc["id"],))
                print(f"[TRACKER RESOLVE] Synchronized deliverable #{sc['id']} '{sc_name}' to COMPLETED")
    except Exception as e:
        print(f"[TRACKER SYNC WARNING] Failed to match scope item: {e}")
        
    db.commit()

    # Trigger automatic PMO graph recalculation across remaining active items
    try:
        from api.routes.baseline import _rebuild_graph_and_recalculate
        cursor = db.cursor(dictionary=True)
        _rebuild_graph_and_recalculate(cursor, project_id, item.get("title"))
        db.commit()
    except Exception as e:
        print(f"[TRACKER ERROR] Recalculate failed: {e}")
    
    updated_item = TrackerRepository.get_resolved_item_details(db, item_id)
    return {"success": True, "message": "Tracker item resolved", "data": updated_item}

@router.post("/{item_id}/reactivate")
def reactivate_tracker_item(project_id: int, item_id: int, current_user: dict = Depends(require_roles(["ENGAGEMENT_MANAGER", "PROJECT_LEAD", "PMO_REVIEWER"])), db: mysql.connector.connection.MySQLConnection = Depends(get_db)):
    verify_project_access(project_id, current_user, db)
    
    item = TrackerRepository.get_tracker_item_by_id_and_project(db, item_id, project_id)
    if not item:
        raise HTTPException(status_code=404, detail="Item not found")
        
    TrackerRepository.reactivate_item(db, item_id, current_user["id"])
    
    # If the tracker item is associated with a scope item (reference_id) or title match, mark the scope item as ACTIVE
    if item.get("reference_id"):
        BaselineRepository.update_scope_item_completion(db, item["reference_id"], project_id, "ACTIVE")
        
    # Also synchronize any matching scope_items by title back to ACTIVE
    try:
        from api.routes.baseline import _is_title_match
        cursor_sync = db.cursor(dictionary=True)
        cursor_sync.execute("SELECT id, name, scope_item_normalized FROM scope_items WHERE project_id = %s AND completion_status = 'COMPLETED'", (project_id,))
        completed_scopes = cursor_sync.fetchall() or []
        for sc in completed_scopes:
            sc_name = sc.get("scope_item_normalized") or sc["name"]
            if _is_title_match(item.get("title", ""), sc_name):
                cursor_sync.execute("UPDATE scope_items SET completion_status = 'ACTIVE' WHERE id = %s", (sc["id"],))
                print(f"[TRACKER REACTIVATE] Synchronized deliverable #{sc['id']} '{sc_name}' back to ACTIVE")
    except Exception as e:
        print(f"[TRACKER SYNC WARNING] Failed to match scope item on reactivate: {e}")

    db.commit()

    # Trigger automatic PMO graph recalculation across active items
    try:
        from api.routes.baseline import _rebuild_graph_and_recalculate
        cursor = db.cursor(dictionary=True)
        _rebuild_graph_and_recalculate(cursor, project_id, None)
        db.commit()
    except Exception as e:
        print(f"[TRACKER ERROR] Recalculate failed: {e}")
    
    updated_item = TrackerRepository.get_tracker_item_by_id_and_project(db, item_id, project_id)
    return {"success": True, "message": "Tracker item reactivated", "data": updated_item}


@router.post("/{item_id}/confirm-resolution")
def confirm_suggested_resolution(
    project_id: int,
    item_id: int,
    current_user: dict = Depends(require_roles(["ENGAGEMENT_MANAGER", "PROJECT_LEAD", "PMO_REVIEWER"])),
    db: mysql.connector.connection.MySQLConnection = Depends(get_db)
):
    """
    PM confirms a PENDING_CONFIRMATION suggestion -> item becomes RESOLVED.
    Generic: works for any tracker item type.
    """
    verify_project_access(project_id, current_user, db)

    item = TrackerRepository.get_tracker_item_by_id_and_project(db, item_id, project_id)
    if not item:
        raise HTTPException(status_code=404, detail="Tracker item not found")

    # Preserve existing reasoning, clear pending_suggestion
    try:
        r = json.loads(item.get('reasoning') or '{}')
        if isinstance(r, dict):
            r.pop('pending_suggestion', None)
        reasoning_updated = json.dumps(r)
    except Exception:
        reasoning_updated = item.get('reasoning') or '{}'

    cursor = db.cursor(dictionary=True)
    cursor.execute("""
        UPDATE tracker_items
        SET status = 'RESOLVED',
            risk_status = 'RESOLVED',
            risk_score = 0,
            execution_priority_score = 0,
            risk_severity_score = 0,
            resolved_at = NOW(),
            resolved_by = %s,
            resolution = 'Confirmed by PM',
            reasoning = %s
        WHERE id = %s
    """, (current_user['id'], reasoning_updated, item_id))

    # If the tracker item is associated with a scope item (reference_id) or title match, mark the scope item as COMPLETED
    if item.get("reference_id"):
        BaselineRepository.update_scope_item_completion(db, item["reference_id"], project_id, "COMPLETED")

    try:
        from api.routes.baseline import _is_title_match
        cursor_sync = db.cursor(dictionary=True)
        cursor_sync.execute("SELECT id, name, scope_item_normalized FROM scope_items WHERE project_id = %s AND (completion_status = 'ACTIVE' OR completion_status IS NULL)", (project_id,))
        active_scopes = cursor_sync.fetchall() or []
        for sc in active_scopes:
            sc_name = sc.get("scope_item_normalized") or sc["name"]
            if _is_title_match(item.get("title", ""), sc_name):
                cursor_sync.execute("UPDATE scope_items SET completion_status = 'COMPLETED' WHERE id = %s", (sc["id"],))
                print(f"[TRACKER RESOLVE] Synchronized deliverable #{sc['id']} '{sc_name}' to COMPLETED")
    except Exception as e:
        print(f"[TRACKER SYNC WARNING] Failed to match scope item: {e}")

    # Audit log
    cursor.execute("""
        INSERT INTO audit_logs (project_id, agent_name, action, entity_type, entity_id, details_json)
        VALUES (%s, %s, 'CONFIRMED_RESOLUTION', 'TRACKER_ITEM', %s, %s)
    """, (project_id, current_user.get('name', 'PM'), item_id,
          json.dumps({"confirmed_by": current_user.get('name'), "item_title": item.get('title')})))

    db.commit()

    # Trigger automatic PMO graph recalculation across remaining active items
    try:
        from api.routes.baseline import _rebuild_graph_and_recalculate
        _rebuild_graph_and_recalculate(cursor, project_id, item.get("title"))
        db.commit()
    except Exception as e:
        print(f"[TRACKER ERROR] Recalculate failed: {e}")

    cursor.close()
    updated_item = TrackerRepository.get_resolved_item_details(db, item_id)
    return {"success": True, "message": "Resolution confirmed by PM", "status": "resolved", "item_id": item_id, "data": updated_item}


@router.post("/{item_id}/dismiss-suggestion")
def dismiss_resolution_suggestion(
    project_id: int,
    item_id: int,
    current_user: dict = Depends(require_roles(["ENGAGEMENT_MANAGER", "PROJECT_LEAD", "PMO_REVIEWER"])),
    db: mysql.connector.connection.MySQLConnection = Depends(get_db)
):
    """
    PM dismisses a PENDING_CONFIRMATION suggestion -> item goes back to OPEN.
    Generic: works for any tracker item type.
    """
    verify_project_access(project_id, current_user, db)

    item = TrackerRepository.get_tracker_item_by_id_and_project(db, item_id, project_id)
    if not item:
        raise HTTPException(status_code=404, detail="Tracker item not found")

    try:
        r = json.loads(item.get('reasoning') or '{}')
        if isinstance(r, dict):
            r.pop('pending_suggestion', None)
        reasoning_updated = json.dumps(r)
    except Exception:
        reasoning_updated = item.get('reasoning') or '{}'

    cursor = db.cursor(dictionary=True)
    cursor.execute("""
        UPDATE tracker_items
        SET risk_status = 'OPEN',
            reasoning = %s
        WHERE id = %s
    """, (reasoning_updated, item_id))

    cursor.execute("""
        INSERT INTO audit_logs (project_id, agent_name, action, entity_type, entity_id, details_json)
        VALUES (%s, %s, 'DISMISSED_SUGGESTION', 'TRACKER_ITEM', %s, %s)
    """, (project_id, current_user.get('name', 'PM'), item_id,
          json.dumps({"dismissed_by": current_user.get('name'), "item_title": item.get('title')})))

    db.commit()

    # Trigger automatic PMO graph recalculation across active items
    try:
        from api.routes.baseline import _rebuild_graph_and_recalculate
        _rebuild_graph_and_recalculate(cursor, project_id, None)
        db.commit()
    except Exception as e:
        print(f"[TRACKER ERROR] Recalculate failed: {e}")

    cursor.close()
    updated_item = TrackerRepository.get_tracker_item_by_id_and_project(db, item_id, project_id)
    return {"success": True, "message": "Suggestion dismissed", "status": "dismissed", "item_id": item_id, "data": updated_item}
