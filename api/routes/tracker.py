# pyrefly: ignore [missing-import]
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from typing import List, Optional
from core.database import get_db, db_cursor
from core.helpers import safe_json_loads, safe_json_dumps, is_title_match
from api.dependencies.auth import get_current_user, require_roles, verify_project_access
from repositories.tracker_repository import TrackerRepository
from repositories.baseline_repository import BaselineRepository
import mysql.connector
import json

router = APIRouter()


def _safe_recalculate(cursor_or_db, project_id: int, completed_title: Optional[str] = None) -> None:
    """Safely runs tracker graph recalculation across remaining active items."""
    try:
        from api.routes.baseline import _rebuild_graph_and_recalculate
        if hasattr(cursor_or_db, 'cursor'):
            with db_cursor(cursor_or_db) as cursor:
                _rebuild_graph_and_recalculate(cursor, project_id, completed_title)
                cursor_or_db.commit()
        else:
            _rebuild_graph_and_recalculate(cursor_or_db, project_id, completed_title)
    except Exception as e:
        print(f"[TRACKER SYNC WARNING] Graph recalculation error: {e}")


@router.get("/")
def get_tracker_items(
    project_id: int,
    current_user: dict = Depends(get_current_user),
    db: mysql.connector.connection.MySQLConnection = Depends(get_db)
):
    try:
        verify_project_access(project_id, current_user, db)
        
        # Auto-synchronize tracker items with active/completed baseline deliverables
        _safe_recalculate(db, project_id, None)

        items = TrackerRepository.get_tracker_items(db, project_id)
        return {"success": True, "data": items}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch tracker items: {str(e)}")


class ResolutionUpdate(BaseModel):
    resolution: str
    status: str


@router.post("/{item_id}/resolve")
def resolve_tracker_item(
    project_id: int,
    item_id: int,
    update: ResolutionUpdate,
    current_user: dict = Depends(require_roles(["ENGAGEMENT_MANAGER", "PROJECT_LEAD", "PMO_REVIEWER"])),
    db: mysql.connector.connection.MySQLConnection = Depends(get_db)
):
    try:
        verify_project_access(project_id, current_user, db)
        item = TrackerRepository.get_tracker_item_by_id_and_project(db, item_id, project_id)
        if not item:
            raise HTTPException(status_code=404, detail="Tracker item not found")

        # Resolve the item
        TrackerRepository.resolve_tracker_item(
            db=db,
            item_id=item_id,
            resolved_by=current_user["id"],
            resolution=update.resolution,
            status=update.status
        )

        # Audit log
        TrackerRepository.log_audit_trail(
            db=db,
            project_id=project_id,
            agent_name=current_user.get("name", "User"),
            action="RESOLVED_ITEM",
            entity_type="TRACKER_ITEM",
            entity_id=item_id,
            details={"resolution": update.resolution, "status": update.status}
        )

        # If the tracker item is associated with a scope item (reference_id) or title match, mark the scope item as COMPLETED
        if item.get("reference_id"):
            BaselineRepository.update_scope_item_completion(db, item["reference_id"], project_id, "COMPLETED")
            
        try:
            with db_cursor(db) as cursor_sync:
                cursor_sync.execute("SELECT id, name, scope_item_normalized FROM scope_items WHERE project_id = %s AND (completion_status = 'ACTIVE' OR completion_status IS NULL)", (project_id,))
                active_scopes = cursor_sync.fetchall() or []
                for sc in active_scopes:
                    sc_name = sc.get("scope_item_normalized") or sc["name"]
                    if is_title_match(item.get("title", ""), sc_name):
                        cursor_sync.execute("UPDATE scope_items SET completion_status = 'COMPLETED' WHERE id = %s", (sc["id"],))
                        print(f"[TRACKER RESOLVE] Synchronized deliverable #{sc['id']} '{sc_name}' to COMPLETED")
        except Exception as e:
            print(f"[TRACKER SYNC WARNING] Failed to match scope item: {e}")
            
        db.commit()

        # Trigger automatic PMO graph recalculation across remaining active items
        _safe_recalculate(db, project_id, item.get("title"))
        
        updated_item = TrackerRepository.get_resolved_item_details(db, item_id)
        return {"success": True, "message": "Tracker item resolved", "data": updated_item}
    except HTTPException:
        raise
    except Exception as e:
        try:
            db.rollback()
        except Exception:
            pass
        raise HTTPException(status_code=500, detail=f"Failed to resolve tracker item #{item_id}: {str(e)}")


@router.post("/{item_id}/reactivate")
def reactivate_tracker_item(
    project_id: int,
    item_id: int,
    current_user: dict = Depends(require_roles(["ENGAGEMENT_MANAGER", "PROJECT_LEAD", "PMO_REVIEWER"])),
    db: mysql.connector.connection.MySQLConnection = Depends(get_db)
):
    try:
        verify_project_access(project_id, current_user, db)
        item = TrackerRepository.get_tracker_item_by_id_and_project(db, item_id, project_id)
        if not item:
            raise HTTPException(status_code=404, detail="Tracker item not found")

        TrackerRepository.reactivate_tracker_item(db=db, item_id=item_id)

        # Audit log
        TrackerRepository.log_audit_trail(
            db=db,
            project_id=project_id,
            agent_name=current_user.get("name", "User"),
            action="REACTIVATED_ITEM",
            entity_type="TRACKER_ITEM",
            entity_id=item_id,
            details={"previous_status": item.get("status")}
        )

        # If the tracker item is associated with a scope item (reference_id) or title match, reactivate the scope item
        if item.get("reference_id"):
            BaselineRepository.update_scope_item_completion(db, item["reference_id"], project_id, "ACTIVE")

        try:
            with db_cursor(db) as cursor_sync:
                cursor_sync.execute("SELECT id, name, scope_item_normalized FROM scope_items WHERE project_id = %s AND completion_status = 'COMPLETED'", (project_id,))
                completed_scopes = cursor_sync.fetchall() or []
                for sc in completed_scopes:
                    sc_name = sc.get("scope_item_normalized") or sc["name"]
                    if is_title_match(item.get("title", ""), sc_name):
                        cursor_sync.execute("UPDATE scope_items SET completion_status = 'ACTIVE' WHERE id = %s", (sc["id"],))
                        print(f"[TRACKER REACTIVATE] Reactivated deliverable #{sc['id']} '{sc_name}' to ACTIVE")
        except Exception as e:
            print(f"[TRACKER SYNC WARNING] Failed to match scope item: {e}")

        db.commit()

        # Trigger automatic PMO graph recalculation across remaining active items
        _safe_recalculate(db, project_id, None)

        updated_item = TrackerRepository.get_tracker_item_by_id_and_project(db, item_id, project_id)
        return {"success": True, "message": "Tracker item reactivated", "data": updated_item}
    except HTTPException:
        raise
    except Exception as e:
        try:
            db.rollback()
        except Exception:
            pass
        raise HTTPException(status_code=500, detail=f"Failed to reactivate tracker item #{item_id}: {str(e)}")


@router.post("/{item_id}/confirm-resolution")
def confirm_resolution_suggestion(
    project_id: int,
    item_id: int,
    current_user: dict = Depends(require_roles(["ENGAGEMENT_MANAGER", "PROJECT_LEAD", "PMO_REVIEWER"])),
    db: mysql.connector.connection.MySQLConnection = Depends(get_db)
):
    """
    PM confirms a PENDING_CONFIRMATION suggestion -> item is marked RESOLVED.
    Generic: works for any tracker item type (deliverable, milestone, action, SLA, etc.).
    """
    try:
        verify_project_access(project_id, current_user, db)

        item = TrackerRepository.get_tracker_item_by_id_and_project(db, item_id, project_id)
        if not item:
            raise HTTPException(status_code=404, detail="Tracker item not found")

        # Preserve existing reasoning, clear pending_suggestion
        r = safe_json_loads(item.get('reasoning'))
        if isinstance(r, dict):
            r.pop('pending_suggestion', None)
        reasoning_updated = safe_json_dumps(r)

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
            cursor_sync = db.cursor(dictionary=True)
            cursor_sync.execute("SELECT id, name, scope_item_normalized FROM scope_items WHERE project_id = %s AND (completion_status = 'ACTIVE' OR completion_status IS NULL)", (project_id,))
            active_scopes = cursor_sync.fetchall() or []
            for sc in active_scopes:
                sc_name = sc.get("scope_item_normalized") or sc["name"]
                if is_title_match(item.get("title", ""), sc_name):
                    cursor_sync.execute("UPDATE scope_items SET completion_status = 'COMPLETED' WHERE id = %s", (sc["id"],))
                    print(f"[TRACKER RESOLVE] Synchronized deliverable #{sc['id']} '{sc_name}' to COMPLETED")
            cursor_sync.close()
        except Exception as e:
            print(f"[TRACKER SYNC WARNING] Failed to match scope item: {e}")

        # Audit log
        cursor.execute("""
            INSERT INTO audit_logs (project_id, agent_name, action, entity_type, entity_id, details_json)
            VALUES (%s, %s, 'CONFIRMED_RESOLUTION', 'TRACKER_ITEM', %s, %s)
        """, (project_id, current_user.get('name', 'PM'), item_id,
              safe_json_dumps({"confirmed_by": current_user.get('name'), "item_title": item.get('title')})))

        db.commit()

        # Trigger automatic PMO graph recalculation across remaining active items
        _safe_recalculate(cursor, project_id, item.get("title"))
        db.commit()
        cursor.close()

        updated_item = TrackerRepository.get_resolved_item_details(db, item_id)
        return {"success": True, "message": "Resolution confirmed by PM", "status": "resolved", "item_id": item_id, "data": updated_item}
    except HTTPException:
        raise
    except Exception as e:
        try:
            db.rollback()
        except Exception:
            pass
        raise HTTPException(status_code=500, detail=f"Failed to confirm resolution for item #{item_id}: {str(e)}")


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
    try:
        verify_project_access(project_id, current_user, db)

        item = TrackerRepository.get_tracker_item_by_id_and_project(db, item_id, project_id)
        if not item:
            raise HTTPException(status_code=404, detail="Tracker item not found")

        r = safe_json_loads(item.get('reasoning'))
        if isinstance(r, dict):
            r.pop('pending_suggestion', None)
        reasoning_updated = safe_json_dumps(r)

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
              safe_json_dumps({"dismissed_by": current_user.get('name'), "item_title": item.get('title')})))

        db.commit()

        # Trigger automatic PMO graph recalculation across active items
        _safe_recalculate(cursor, project_id, None)
        db.commit()
        cursor.close()

        updated_item = TrackerRepository.get_tracker_item_by_id_and_project(db, item_id, project_id)
        return {"success": True, "message": "Suggestion dismissed", "status": "dismissed", "item_id": item_id, "data": updated_item}
    except HTTPException:
        raise
    except Exception as e:
        try:
            db.rollback()
        except Exception:
            pass
        raise HTTPException(status_code=500, detail=f"Failed to dismiss suggestion for item #{item_id}: {str(e)}")
