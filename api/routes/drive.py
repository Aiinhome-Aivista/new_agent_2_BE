"""
Drive API Routes
================
All endpoints for Drive account management and inbox operations.

Security:
  - Credentials JSON is NEVER returned to the client after storage.
  - All stored credentials are encrypted via drive_crypto before DB insert.
  - service_email and folder_id are safe to return (non-secret identifiers).
"""
import json
import logging
from typing import Optional

# pyrefly: ignore [missing-import]
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from core.database import get_db
from api.dependencies.auth import get_current_user, require_roles
import mysql.connector

logger = logging.getLogger("drive_routes")
router = APIRouter()


# ── Pydantic Models ──────────────────────────────────────────────────────────

class AddAccountRequest(BaseModel):
    label: str                   # Human-readable name, e.g. "PMO Team Drive"
    service_email: str           # Service account email (for display only)
    folder_id: str               # Google Drive Folder ID
    credentials_json: str        # Raw JSON string — encrypted before DB insert


class AssignInboxRequest(BaseModel):
    project_id: int
    doc_type: Optional[str] = "MOM"


class ProcessInboxRequest(BaseModel):
    project_id: int
    doc_type: Optional[str] = "MOM"


# ── Drive Accounts ───────────────────────────────────────────────────────────

@router.get("/accounts")
def list_drive_accounts(
    current_user: dict = Depends(require_roles(["ADMIN", "ENGAGEMENT_MANAGER"])),
    db: mysql.connector.connection.MySQLConnection = Depends(get_db),
):
    """List all configured Drive accounts. Credentials are NEVER returned."""
    cursor = db.cursor(dictionary=True)
    cursor.execute(
        "SELECT id, label, service_email, folder_id, is_active, last_synced_at, created_at "
        "FROM drive_accounts ORDER BY created_at DESC"
    )
    rows = cursor.fetchall()
    cursor.close()
    return {"success": True, "data": rows}


@router.post("/accounts")
def add_drive_account(
    body: AddAccountRequest,
    current_user: dict = Depends(require_roles(["ADMIN", "ENGAGEMENT_MANAGER"])),
    db: mysql.connector.connection.MySQLConnection = Depends(get_db),
):
    """
    Add a new Google Drive account.
    The raw credentials_json is encrypted before storage and never echoed back.
    """
    from services.drive_crypto import encrypt_credentials

    # Validate JSON is parseable before encrypting
    try:
        json.loads(body.credentials_json)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid credentials JSON: {exc}")

    encrypted = encrypt_credentials(body.credentials_json)

    cursor = db.cursor()
    cursor.execute(
        "INSERT INTO drive_accounts (label, service_email, folder_id, credentials_enc, added_by) "
        "VALUES (%s, %s, %s, %s, %s)",
        (body.label, body.service_email, body.folder_id, encrypted, current_user["id"]),
    )
    account_id = cursor.lastrowid
    cursor.close()
    db.commit()

    return {
        "success": True,
        "message": "Drive account added successfully.",
        "data": {"id": account_id, "label": body.label, "service_email": body.service_email},
    }


@router.delete("/accounts/{account_id}")
def delete_drive_account(
    account_id: int,
    current_user: dict = Depends(require_roles(["ADMIN", "ENGAGEMENT_MANAGER"])),
    db: mysql.connector.connection.MySQLConnection = Depends(get_db),
):
    cursor = db.cursor()
    cursor.execute("DELETE FROM drive_accounts WHERE id = %s", (account_id,))
    cursor.close()
    db.commit()
    return {"success": True, "message": "Drive account removed."}


# ── Manual Sync Trigger ───────────────────────────────────────────────────────

@router.post("/sync")
def trigger_drive_sync(
    current_user: dict = Depends(require_roles(["ADMIN", "ENGAGEMENT_MANAGER"])),
):
    """Manually trigger a Drive sync (same as the scheduled job)."""
    from services.drive_inbox_service import run_drive_sync
    try:
        result = run_drive_sync()
        return {"success": True, "data": result}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


# ── Drive Inbox ───────────────────────────────────────────────────────────────

@router.get("/inbox")
def list_drive_inbox(
    project_id: Optional[int] = None,
    status: Optional[str] = None,
    current_user: dict = Depends(require_roles(["ADMIN", "ENGAGEMENT_MANAGER", "PROJECT_LEAD"])),
    db: mysql.connector.connection.MySQLConnection = Depends(get_db),
):
    """
    List Drive Inbox items.
    Optional filters: project_id, status (PENDING / ASSIGNED / DONE / SKIPPED).
    """
    filters = ["1=1"]
    params = []
    if project_id is not None:
        filters.append("di.matched_project_id = %s")
        params.append(project_id)
    if status:
        filters.append("di.status = %s")
        params.append(status.upper())

    where = " AND ".join(filters)
    cursor = db.cursor(dictionary=True)
    cursor.execute(
        f"""
        SELECT di.id, di.drive_file_id, di.filename, di.mime_type,
               di.matched_project_id, di.doc_type, di.status,
               di.fetched_at, di.processed_at,
               da.label AS account_label, da.service_email
        FROM drive_inbox di
        JOIN drive_accounts da ON da.id = di.drive_account_id
        WHERE {where}
        ORDER BY di.fetched_at DESC
        LIMIT 200
        """,
        params,
    )
    rows = cursor.fetchall()
    cursor.close()
    return {"success": True, "data": rows}


@router.patch("/inbox/{inbox_id}/assign")
def assign_inbox_item(
    inbox_id: int,
    body: AssignInboxRequest,
    current_user: dict = Depends(require_roles(["ADMIN", "ENGAGEMENT_MANAGER", "PROJECT_LEAD"])),
    db: mysql.connector.connection.MySQLConnection = Depends(get_db),
):
    """Manually assign a Drive Inbox item to a project."""
    cursor = db.cursor()
    cursor.execute(
        "UPDATE drive_inbox SET matched_project_id=%s, doc_type=%s, status='ASSIGNED' "
        "WHERE id=%s AND status IN ('PENDING','ASSIGNED')",
        (body.project_id, body.doc_type, inbox_id),
    )
    cursor.close()
    db.commit()
    return {"success": True, "message": "Inbox item assigned."}


@router.post("/inbox/{inbox_id}/process")
def process_inbox_item_route(
    inbox_id: int,
    body: ProcessInboxRequest,
    current_user: dict = Depends(require_roles(["ADMIN", "ENGAGEMENT_MANAGER", "PROJECT_LEAD"])),
):
    """
    Manually trigger processing of a Drive Inbox item.
    Runs the full LLM extraction + Dependency Graph + Risk pipeline.
    """
    from services.drive_inbox_service import process_inbox_item
    result = process_inbox_item(inbox_id, body.project_id, body.doc_type, current_user["id"])
    if not result.get("success"):
        raise HTTPException(status_code=500, detail=result.get("error", "Processing failed"))
    return {"success": True, "data": result}


@router.patch("/inbox/{inbox_id}/skip")
def skip_inbox_item(
    inbox_id: int,
    current_user: dict = Depends(require_roles(["ADMIN", "ENGAGEMENT_MANAGER", "PROJECT_LEAD"])),
    db: mysql.connector.connection.MySQLConnection = Depends(get_db),
):
    """Mark a Drive Inbox item as skipped/ignored."""
    cursor = db.cursor()
    cursor.execute(
        "UPDATE drive_inbox SET status='SKIPPED' WHERE id=%s", (inbox_id,)
    )
    cursor.close()
    db.commit()
    return {"success": True, "message": "Inbox item skipped."}


@router.patch("/inbox/{inbox_id}/resume")
def resume_inbox_item(
    inbox_id: int,
    current_user: dict = Depends(require_roles(["ADMIN", "ENGAGEMENT_MANAGER", "PROJECT_LEAD"])),
    db: mysql.connector.connection.MySQLConnection = Depends(get_db),
):
    """Mark a skipped Drive Inbox item as ASSIGNED or PENDING again."""
    cursor = db.cursor(dictionary=True)
    
    # Check if it has a matched_project_id to determine the new status
    cursor.execute("SELECT matched_project_id FROM drive_inbox WHERE id=%s", (inbox_id,))
    item = cursor.fetchone()
    if not item:
        cursor.close()
        raise HTTPException(status_code=404, detail="Inbox item not found")
        
    new_status = "ASSIGNED" if item.get("matched_project_id") else "PENDING"
    
    cursor.execute(
        "UPDATE drive_inbox SET status=%s WHERE id=%s", (new_status, inbox_id)
    )
    cursor.close()
    db.commit()
    return {"success": True, "message": f"Inbox item resumed as {new_status}."}


@router.delete("/inbox/{inbox_id}")
def delete_inbox_item(
    inbox_id: int,
    current_user: dict = Depends(require_roles(["ADMIN", "ENGAGEMENT_MANAGER", "PROJECT_LEAD"])),
    db: mysql.connector.connection.MySQLConnection = Depends(get_db),
):
    """Completely delete a Drive Inbox item from the database."""
    cursor = db.cursor()
    cursor.execute("DELETE FROM drive_inbox WHERE id=%s", (inbox_id,))
    cursor.close()
    db.commit()
    return {"success": True, "message": "Inbox item deleted forever."}
