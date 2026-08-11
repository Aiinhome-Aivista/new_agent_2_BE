"""
Drive Inbox Service
====================
Core business logic for the Drive integration:
  1. `ensure_drive_tables()`    — auto-creates the two new DB tables on startup
  2. `match_project_from_filename()` — parses filename convention to find a project
  3. `run_drive_sync()`         — polls all active accounts, saves files, populates inbox
  4. `process_inbox_item()`     — downloads file locally, triggers OrchestratorAgent
"""
import json
import logging
import os
import re
import uuid
from datetime import datetime
from typing import Optional

logger = logging.getLogger("drive_inbox_service")


# ── Schema Auto-Migration ────────────────────────────────────────────────────

def ensure_drive_tables() -> None:
    """
    Create drive_accounts and drive_inbox tables if they do not exist.
    Uses the same safe pattern as the existing tracker auto-migration.
    """
    from core.database import get_db_connection
    conn = get_db_connection()
    if not conn:
        logger.error("Cannot create drive tables: no DB connection")
        return
    try:
        cursor = conn.cursor()

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS drive_accounts (
                id                INT AUTO_INCREMENT PRIMARY KEY,
                label             VARCHAR(255) NOT NULL,
                service_email     VARCHAR(255) NOT NULL,
                folder_id         VARCHAR(512) NOT NULL,
                credentials_enc   TEXT         NOT NULL,
                added_by          INT          NOT NULL,
                is_active         BOOLEAN      DEFAULT TRUE,
                last_synced_at    DATETIME     NULL,
                created_at        DATETIME     DEFAULT CURRENT_TIMESTAMP
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS drive_inbox (
                id                  INT AUTO_INCREMENT PRIMARY KEY,
                drive_account_id    INT          NOT NULL,
                drive_file_id       VARCHAR(512) NOT NULL,
                filename            VARCHAR(512) NOT NULL,
                mime_type           VARCHAR(255),
                matched_project_id  INT          NULL,
                doc_type            VARCHAR(50)  DEFAULT 'MOM',
                status              ENUM('PENDING','ASSIGNED','PROCESSING','DONE','SKIPPED')
                                    DEFAULT 'PENDING',
                fetched_at          DATETIME     DEFAULT CURRENT_TIMESTAMP,
                processed_at        DATETIME     NULL,
                file_path           VARCHAR(1024) NULL,
                UNIQUE KEY uq_drive_file (drive_file_id)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
        """)

        conn.commit()
        cursor.close()
        logger.info("Drive tables ensured.")
    except Exception as exc:
        logger.error("ensure_drive_tables error: %s", exc)
    finally:
        try:
            conn.close()
        except Exception:
            pass


# ── Filename → Project Matching ──────────────────────────────────────────────

def match_project_from_filename(filename: str, conn) -> Optional[int]:
    """
    Check if any project name is contained within the filename, ignoring spaces, 
    dashes, and underscores to be as flexible as possible.
    Returns the project_id of the longest matching project name.
    """
    # Remove all spaces, dashes, underscores, and extension for fuzzy matching
    base_name = filename.rsplit(".", 1)[0]
    normalized_file = base_name.replace("_", "").replace("-", "").replace(" ", "").lower()
    
    try:
        cursor = conn.cursor(dictionary=True)
        cursor.execute("SELECT id, project_name FROM projects WHERE is_active = 1")
        projects = cursor.fetchall()
        cursor.close()

        best_match = None
        max_len = 0

        for proj in projects:
            raw_name = proj.get("project_name") or ""
            proj_name_clean = raw_name.replace("_", "").replace("-", "").replace(" ", "").lower()
            
            if not proj_name_clean:
                continue
                
            # If the cleaned project name is found inside the cleaned filename
            if proj_name_clean in normalized_file:
                # In case multiple projects match (e.g. "Agent", "Agent 6"), take the most specific one
                if len(proj_name_clean) > max_len:
                    max_len = len(proj_name_clean)
                    best_match = proj["id"]
                    
        return best_match
    except Exception as exc:
        logger.warning("match_project_from_filename DB error: %s", exc)

    return None


def _infer_doc_type(filename: str) -> str:
    """Infer document type from filename."""
    fl = filename.lower()
    if "status" in fl:
        return "STATUS_REPORT"
    if "mom" in fl or "minutes" in fl or "meeting" in fl:
        return "MOM"
    return "MOM"  # default


# ── Main Sync Job ────────────────────────────────────────────────────────────

def run_drive_sync() -> dict:
    """
    Poll all active drive_accounts for new files and insert them into drive_inbox.
    Called by the scheduler — does NOT process files, only fetches and stages them.
    """
    from core.database import get_db_connection
    from core.config import settings
    from services.drive_crypto import decrypt_credentials
    from services.google_drive_service import GoogleDriveService

    conn = get_db_connection()
    if not conn:
        return {"success": False, "error": "No DB connection"}

    total_new = 0
    errors = []

    try:
        cursor = conn.cursor(dictionary=True)
        cursor.execute(
            "SELECT id, label, folder_id, credentials_enc, service_email "
            "FROM drive_accounts WHERE is_active = 1"
        )
        accounts = cursor.fetchall()
        cursor.close()
    except Exception as exc:
        conn.close()
        return {"success": False, "error": str(exc)}

    # Determine look-back window from settings
    look_back_hours = settings.DRIVE_SYNC_INTERVAL_HOURS
    if look_back_hours == 0:
        # Minute-based interval — look back at least 1 hour to not miss files
        look_back_hours = max(1, round(settings.DRIVE_SYNC_INTERVAL_MINUTES / 60) + 1)

    for acct in accounts:
        acct_id = acct["id"]
        label = acct["label"]
        logger.info("Syncing Drive account '%s' (id=%d)…", label, acct_id)

        try:
            # Decrypt — result must NOT be logged
            raw_creds_json = decrypt_credentials(acct["credentials_enc"])
            creds_dict = json.loads(raw_creds_json)
            raw_creds_json = None  # explicitly clear reference

            svc = GoogleDriveService(creds_dict, acct["folder_id"])
            creds_dict = None  # clear

            files = svc.list_recent_files(since_hours=look_back_hours)
            logger.info("  Found %d file(s) in '%s'", len(files), label)

            for f in files:
                drive_file_id = f["id"]
                filename = f["name"]
                mime_type = f.get("mimeType", "")

                # Check if already in inbox
                chk = conn.cursor(dictionary=True)
                chk.execute(
                    "SELECT id FROM drive_inbox WHERE drive_file_id = %s",
                    (drive_file_id,),
                )
                existing = chk.fetchone()
                chk.close()
                if existing:
                    continue  # Already staged — skip

                matched_project_id = match_project_from_filename(filename, conn)
                doc_type = _infer_doc_type(filename)

                ins = conn.cursor()
                ins.execute(
                    """
                    INSERT INTO drive_inbox
                        (drive_account_id, drive_file_id, filename, mime_type,
                         matched_project_id, doc_type, status)
                    VALUES (%s, %s, %s, %s, %s, %s, 'PENDING')
                    """,
                    (acct_id, drive_file_id, filename, mime_type,
                     matched_project_id, doc_type),
                )
                ins.close()
                total_new += 1
                logger.info(
                    "  Staged '%s' → project_id=%s", filename, matched_project_id
                )

            # Update last_synced_at
            upd = conn.cursor()
            upd.execute(
                "UPDATE drive_accounts SET last_synced_at = NOW() WHERE id = %s",
                (acct_id,),
            )
            upd.close()
            conn.commit()

        except Exception as exc:
            logger.error("Error syncing account '%s': %s", label, exc)
            errors.append({"account": label, "error": str(exc)})
            try:
                conn.rollback()
            except Exception:
                pass

    try:
        conn.close()
    except Exception:
        pass

    return {
        "success": True,
        "new_files_staged": total_new,
        "errors": errors,
    }


# ── Process a Single Inbox Item ───────────────────────────────────────────────

def process_inbox_item(inbox_id: int, project_id: int, doc_type: str, user_id: int) -> dict:
    """
    Download the file from Drive, save it locally, register it as a Document,
    and trigger the full OrchestratorAgent pipeline (same as manual upload).

    This is called by the API route when the user clicks ▶ Play.
    """
    from core.database import get_db_connection
    from core.config import settings
    from services.drive_crypto import decrypt_credentials
    from services.google_drive_service import GoogleDriveService
    from repositories.document_repository import DocumentRepository
    from services.document_service import DocumentService
    from services.rag_service import RAGService

    conn = get_db_connection()
    if not conn:
        return {"success": False, "error": "No DB connection"}

    try:
        cursor = conn.cursor(dictionary=True)

        # Load inbox row
        cursor.execute(
            "SELECT di.*, da.folder_id, da.credentials_enc, da.label "
            "FROM drive_inbox di "
            "JOIN drive_accounts da ON da.id = di.drive_account_id "
            "WHERE di.id = %s",
            (inbox_id,),
        )
        row = cursor.fetchone()
        cursor.close()

        if not row:
            conn.close()
            return {"success": False, "error": "Inbox item not found"}

        if row["status"] in ("PROCESSING", "DONE"):
            conn.close()
            return {"success": False, "error": f"Item already in status: {row['status']}"}

        # Mark processing
        upd = conn.cursor()
        upd.execute(
            "UPDATE drive_inbox SET status='PROCESSING', matched_project_id=%s, "
            "doc_type=%s WHERE id=%s",
            (project_id, doc_type, inbox_id),
        )
        upd.close()
        conn.commit()

        # Download from Drive — credentials decrypted in-memory only, never logged
        raw_creds_json = decrypt_credentials(row["credentials_enc"])
        creds_dict = json.loads(raw_creds_json)
        raw_creds_json = None  # clear

        svc = GoogleDriveService(creds_dict, row["folder_id"])
        creds_dict = None  # clear

        file_bytes = svc.download_file(row["drive_file_id"], row["mime_type"])
        if not file_bytes:
            raise RuntimeError("Failed to download file from Drive")

        # Save to local storage (same pattern as manual upload)
        storage_dir = os.path.join(settings.UPLOAD_PATH, str(project_id))
        os.makedirs(storage_dir, exist_ok=True)
        ext = ".docx"  # we always export as docx
        unique_filename = f"drive_{uuid.uuid4()}{ext}"
        storage_key = os.path.join(storage_dir, unique_filename)

        with open(storage_key, "wb") as fh:
            fh.write(file_bytes)

        # Register document record (uploaded_by = 0 = "SYSTEM/Drive Sync")
        document_id = DocumentRepository.create_document(
            db=conn,
            project_id=project_id,
            document_name=row["filename"],
            document_type=doc_type,
            storage_key=storage_key,
            uploaded_by=user_id,
        )
        conn.commit()

        # Update inbox with file_path
        upd2 = conn.cursor()
        upd2.execute(
            "UPDATE drive_inbox SET file_path=%s WHERE id=%s",
            (storage_key, inbox_id),
        )
        upd2.close()
        conn.commit()

        # Mark inbox item as DONE
        upd3 = conn.cursor()
        upd3.execute(
            "UPDATE drive_inbox SET status='DONE', processed_at=NOW() WHERE id=%s",
            (inbox_id,),
        )
        upd3.close()
        
        # NOTE: We do NOT run OrchestratorAgent here, so it does not auto-generate the Risk Tracker report.
        # The user will go to the Risk Tracker page and select the document manually.
        
        # Index in RAG (store in ChromaDB)
        doc_cursor = conn.cursor(dictionary=True)
        chunks = DocumentService.parse_document(storage_key, ext)
        RAGService.index_document(project_id, document_id, row["filename"], doc_type, chunks)
        doc_cursor.close()

        # Mark document completed
        DocumentRepository.update_processing_status(conn, document_id, "COMPLETED")
        conn.commit()

        conn.close()
        return {"success": True, "document_id": document_id}

    except Exception as exc:
        logger.error("process_inbox_item error: %s", exc)
        try:
            fail_upd = conn.cursor()
            fail_upd.execute(
                "UPDATE drive_inbox SET status='PENDING' WHERE id=%s", (inbox_id,)
            )
            fail_upd.close()
            conn.commit()
        except Exception:
            pass
        try:
            conn.close()
        except Exception:
            pass
        return {"success": False, "error": str(exc)}
