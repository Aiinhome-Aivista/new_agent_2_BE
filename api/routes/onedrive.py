"""
OneDrive API Routes
===================
All endpoints for Microsoft OneDrive / SharePoint account management and inbox operations.

Security:
  - Client Secret is NEVER returned to the client after storage.
  - All stored secrets are encrypted via Fernet AES before DB insert.
  - tenant_id, client_id, and folder/drive identifiers are safe for display.
"""
import logging
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from core.database import get_db
from api.dependencies.auth import require_roles
import mysql.connector

logger = logging.getLogger("onedrive_routes")
router = APIRouter()


# ── Pydantic Models ──────────────────────────────────────────────────────────

class AddOneDriveAccountRequest(BaseModel):
    label: str                               # e.g., "Corporate SharePoint" or "PMO OneDrive"
    tenant_id: str                           # Azure AD Directory / Tenant ID
    client_id: str                           # Azure AD Application ID
    client_secret: str                       # Plain client secret (encrypted before DB storage)
    drive_type: Optional[str] = "USER_DRIVE" # "USER_DRIVE" or "SHAREPOINT_DRIVE"
    target_user_email: Optional[str] = None  # For personal/business user OneDrive
    target_drive_id: Optional[str] = None    # For SharePoint document library
    folder_id: Optional[str] = "root"        # Folder item ID or "root"


class AssignOneDriveInboxRequest(BaseModel):
    project_id: int
    doc_type: Optional[str] = "MOM"


class ProcessOneDriveInboxRequest(BaseModel):
    project_id: int
    doc_type: Optional[str] = "MOM"


# ── OneDrive Accounts ────────────────────────────────────────────────────────

@router.get("/accounts")
def list_onedrive_accounts(
    current_user: dict = Depends(require_roles(["ADMIN", "ENGAGEMENT_MANAGER"])),
    db: mysql.connector.connection.MySQLConnection = Depends(get_db),
):
    """List all configured OneDrive accounts. Secrets are NEVER returned."""
    cursor = db.cursor(dictionary=True)
    cursor.execute(
        """
        SELECT id, label, tenant_id, client_id, drive_type,
               target_user_email, target_drive_id, folder_id,
               is_active, last_synced_at, created_at
        FROM onedrive_accounts
        ORDER BY created_at DESC
        """
    )
    rows = cursor.fetchall()
    cursor.close()
    return {"success": True, "data": rows}


@router.post("/accounts")
def add_onedrive_account(
    body: AddOneDriveAccountRequest,
    current_user: dict = Depends(require_roles(["ADMIN", "ENGAGEMENT_MANAGER"])),
    db: mysql.connector.connection.MySQLConnection = Depends(get_db),
):
    """
    Add a new Microsoft OneDrive account.
    The raw client_secret is encrypted before storage.
    """
    from services.drive_crypto import encrypt_credentials

    if not body.tenant_id.strip() or not body.client_id.strip() or not body.client_secret.strip():
        raise HTTPException(status_code=400, detail="Tenant ID, Client ID, and Client Secret are required.")

    encrypted_secret = encrypt_credentials(body.client_secret)

    cursor = db.cursor()
    cursor.execute(
        """
        INSERT INTO onedrive_accounts
            (label, tenant_id, client_id, client_secret_enc, drive_type,
             target_user_email, target_drive_id, folder_id, added_by)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (
            body.label,
            body.tenant_id,
            body.client_id,
            encrypted_secret,
            body.drive_type or "USER_DRIVE",
            body.target_user_email,
            body.target_drive_id,
            body.folder_id or "root",
            current_user["id"],
        ),
    )
    account_id = cursor.lastrowid
    cursor.close()
    db.commit()

    return {
        "success": True,
        "message": "OneDrive account added successfully.",
        "data": {
            "id": account_id,
            "label": body.label,
            "tenant_id": body.tenant_id,
            "client_id": body.client_id,
            "drive_type": body.drive_type,
        },
    }


@router.delete("/accounts/{account_id}")
def delete_onedrive_account(
    account_id: int,
    current_user: dict = Depends(require_roles(["ADMIN", "ENGAGEMENT_MANAGER"])),
    db: mysql.connector.connection.MySQLConnection = Depends(get_db),
):
    cursor = db.cursor()
    cursor.execute("DELETE FROM onedrive_accounts WHERE id = %s", (account_id,))
    cursor.close()
    db.commit()
    return {"success": True, "message": "OneDrive account removed."}


# ── Manual Sync Trigger ───────────────────────────────────────────────────────

@router.post("/sync")
def trigger_onedrive_sync(
    current_user: dict = Depends(require_roles(["ADMIN", "ENGAGEMENT_MANAGER"])),
):
    """Manually trigger a OneDrive sync."""
    from services.onedrive_inbox_service import run_onedrive_sync
    try:
        result = run_onedrive_sync()
        return {"success": True, "data": result}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


# ── OneDrive Inbox ────────────────────────────────────────────────────────────

@router.get("/inbox")
def list_onedrive_inbox(
    project_id: Optional[int] = None,
    status: Optional[str] = None,
    current_user: dict = Depends(require_roles(["ADMIN", "ENGAGEMENT_MANAGER", "PROJECT_LEAD"])),
    db: mysql.connector.connection.MySQLConnection = Depends(get_db),
):
    """
    List OneDrive Inbox items.
    Optional filters: project_id, status.
    """
    filters = ["1=1"]
    params = []
    if project_id is not None:
        filters.append("oi.matched_project_id = %s")
        params.append(project_id)
    if status:
        filters.append("oi.status = %s")
        params.append(status.upper())

    where = " AND ".join(filters)
    cursor = db.cursor(dictionary=True)
    cursor.execute(
        f"""
        SELECT oi.id, oi.onedrive_file_id, oi.filename, oi.mime_type, oi.web_url,
               oi.matched_project_id, oi.doc_type, oi.status,
               oi.fetched_at, oi.processed_at,
               oa.label AS account_label, oa.drive_type, oa.target_user_email
        FROM onedrive_inbox oi
        JOIN onedrive_accounts oa ON oa.id = oi.onedrive_account_id
        WHERE {where}
        ORDER BY oi.fetched_at DESC
        LIMIT 200
        """,
        params,
    )
    rows = cursor.fetchall()
    cursor.close()
    return {"success": True, "data": rows}


@router.patch("/inbox/{inbox_id}/assign")
def assign_onedrive_inbox_item(
    inbox_id: int,
    body: AssignOneDriveInboxRequest,
    current_user: dict = Depends(require_roles(["ADMIN", "ENGAGEMENT_MANAGER", "PROJECT_LEAD"])),
    db: mysql.connector.connection.MySQLConnection = Depends(get_db),
):
    """Assign a OneDrive Inbox item to a project."""
    cursor = db.cursor()
    cursor.execute(
        "UPDATE onedrive_inbox SET matched_project_id=%s, doc_type=%s, status='ASSIGNED' "
        "WHERE id=%s AND status IN ('PENDING','ASSIGNED')",
        (body.project_id, body.doc_type, inbox_id),
    )
    cursor.close()
    db.commit()
    return {"success": True, "message": "OneDrive inbox item assigned."}


@router.post("/inbox/{inbox_id}/process")
def process_onedrive_inbox_item_route(
    inbox_id: int,
    body: ProcessOneDriveInboxRequest,
    current_user: dict = Depends(require_roles(["ADMIN", "ENGAGEMENT_MANAGER", "PROJECT_LEAD"])),
):
    """
    Manually trigger processing of a OneDrive item:
    Downloads file, registers it under project documents, indexes in RAG.
    """
    from services.onedrive_inbox_service import process_onedrive_inbox_item
    result = process_onedrive_inbox_item(inbox_id, body.project_id, body.doc_type, current_user["id"])
    if not result.get("success"):
        raise HTTPException(status_code=500, detail=result.get("error", "Processing failed"))
    return {"success": True, "data": result}


@router.patch("/inbox/{inbox_id}/skip")
def skip_onedrive_inbox_item(
    inbox_id: int,
    current_user: dict = Depends(require_roles(["ADMIN", "ENGAGEMENT_MANAGER", "PROJECT_LEAD"])),
    db: mysql.connector.connection.MySQLConnection = Depends(get_db),
):
    cursor = db.cursor()
    cursor.execute(
        "UPDATE onedrive_inbox SET status='SKIPPED' WHERE id=%s", (inbox_id,)
    )
    cursor.close()
    db.commit()
    return {"success": True, "message": "OneDrive inbox item skipped."}


@router.patch("/inbox/{inbox_id}/resume")
def resume_onedrive_inbox_item(
    inbox_id: int,
    current_user: dict = Depends(require_roles(["ADMIN", "ENGAGEMENT_MANAGER", "PROJECT_LEAD"])),
    db: mysql.connector.connection.MySQLConnection = Depends(get_db),
):
    cursor = db.cursor(dictionary=True)
    cursor.execute("SELECT matched_project_id FROM onedrive_inbox WHERE id=%s", (inbox_id,))
    item = cursor.fetchone()
    if not item:
        cursor.close()
        raise HTTPException(status_code=404, detail="OneDrive inbox item not found")

    new_status = "ASSIGNED" if item.get("matched_project_id") else "PENDING"
    cursor.execute(
        "UPDATE onedrive_inbox SET status=%s WHERE id=%s", (new_status, inbox_id)
    )
    cursor.close()
    db.commit()
    return {"success": True, "message": f"OneDrive inbox item resumed as {new_status}."}


@router.delete("/inbox/{inbox_id}")
def delete_onedrive_inbox_item(
    inbox_id: int,
    current_user: dict = Depends(require_roles(["ADMIN", "ENGAGEMENT_MANAGER", "PROJECT_LEAD"])),
    db: mysql.connector.connection.MySQLConnection = Depends(get_db),
):
    cursor = db.cursor()
    cursor.execute("DELETE FROM onedrive_inbox WHERE id=%s", (inbox_id,))
    cursor.close()
    db.commit()
    return {"success": True, "message": "OneDrive inbox item deleted."}
