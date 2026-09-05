"""
OneDrive Inbox Service
======================
Core business logic for the Microsoft OneDrive / SharePoint integration:
  1. `ensure_onedrive_tables()`     — auto-creates onedrive_accounts & onedrive_inbox tables
  2. `run_onedrive_sync()`          — polls active OneDrive accounts and stages new files
  3. `process_onedrive_inbox_item()`— downloads file, registers Document, indexes in RAG
"""
import json
import logging
import os
import uuid
from datetime import datetime
from typing import Optional, Dict, Any

logger = logging.getLogger("onedrive_inbox_service")


# ── Schema Auto-Migration ────────────────────────────────────────────────────

def ensure_onedrive_tables() -> None:
    """
    Create onedrive_accounts and onedrive_inbox tables if they do not exist.
    """
    from core.database import get_db_connection
    conn = get_db_connection()
    if not conn:
        logger.error("Cannot create onedrive tables: no DB connection")
        return
    try:
        cursor = conn.cursor()

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS onedrive_accounts (
                id                  INT AUTO_INCREMENT PRIMARY KEY,
                label               VARCHAR(255) NOT NULL,
                tenant_id           VARCHAR(255) NOT NULL,
                client_id           VARCHAR(255) NOT NULL,
                client_secret_enc   TEXT         NOT NULL,
                drive_type          ENUM('USER_DRIVE', 'SHAREPOINT_DRIVE') DEFAULT 'USER_DRIVE',
                target_user_email   VARCHAR(255) NULL,
                target_drive_id     VARCHAR(255) NULL,
                folder_id           VARCHAR(512) DEFAULT 'root',
                added_by            INT          NOT NULL,
                is_active           BOOLEAN      DEFAULT TRUE,
                last_synced_at      DATETIME     NULL,
                created_at          DATETIME     DEFAULT CURRENT_TIMESTAMP
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS onedrive_inbox (
                id                  INT AUTO_INCREMENT PRIMARY KEY,
                onedrive_account_id INT          NOT NULL,
                onedrive_file_id    VARCHAR(512) NOT NULL,
                filename            VARCHAR(512) NOT NULL,
                mime_type           VARCHAR(255),
                web_url             VARCHAR(1024) NULL,
                matched_project_id  INT          NULL,
                doc_type            VARCHAR(50)  DEFAULT 'MOM',
                status              ENUM('PENDING','ASSIGNED','PROCESSING','DONE','SKIPPED')
                                    DEFAULT 'PENDING',
                fetched_at          DATETIME     DEFAULT CURRENT_TIMESTAMP,
                processed_at        DATETIME     NULL,
                file_path           VARCHAR(1024) NULL,
                UNIQUE KEY uq_onedrive_file (onedrive_file_id)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
        """)

        conn.commit()
        cursor.close()
        logger.info("OneDrive tables ensured.")
    except Exception as exc:
        logger.error("ensure_onedrive_tables error: %s", exc)
    finally:
        try:
            conn.close()
        except Exception:
            pass


# ── Main OneDrive Sync Job ───────────────────────────────────────────────────

def run_onedrive_sync() -> Dict[str, Any]:
    """
    Poll all active onedrive_accounts for new files and insert them into onedrive_inbox.
    Stages items without processing them.
    """
    from core.database import get_db_connection
    from core.config import settings
    from services.drive_crypto import decrypt_credentials
    from services.drive_inbox_service import match_project_from_filename, _infer_doc_type
    from services.onedrive_service import OneDriveService

    conn = get_db_connection()
    if not conn:
        return {"success": False, "error": "No DB connection"}

    total_new = 0
    errors = []

    try:
        cursor = conn.cursor(dictionary=True)
        cursor.execute(
            "SELECT id, label, tenant_id, client_id, client_secret_enc, "
            "drive_type, target_user_email, target_drive_id, folder_id "
            "FROM onedrive_accounts WHERE is_active = 1"
        )
        accounts = cursor.fetchall()
        cursor.close()
    except Exception as exc:
        conn.close()
        return {"success": False, "error": str(exc)}

    look_back_hours = settings.ONEDRIVE_SYNC_INTERVAL_HOURS
    if look_back_hours <= 0:
        look_back_hours = max(1, round(settings.ONEDRIVE_SYNC_INTERVAL_MINUTES / 60) + 1)

    for acct in accounts:
        acct_id = acct["id"]
        label = acct["label"]
        logger.info("Syncing OneDrive account '%s' (id=%d)...", label, acct_id)

        try:
            raw_secret = decrypt_credentials(acct["client_secret_enc"])

            svc = OneDriveService(
                tenant_id=acct["tenant_id"],
                client_id=acct["client_id"],
                client_secret=raw_secret,
                drive_type=acct["drive_type"],
                target_user_email=acct["target_user_email"],
                target_drive_id=acct["target_drive_id"],
                folder_id=acct["folder_id"],
            )
            raw_secret = None  # Clear reference from memory

            files = svc.list_recent_files(since_hours=look_back_hours)
            logger.info("  Found %d file(s) in '%s'", len(files), label)

            for f in files:
                od_file_id = f["id"]
                filename = f["name"]
                mime_type = f.get("mimeType", "")
                web_url = f.get("webUrl", "")

                # Check if already in onedrive_inbox
                chk = conn.cursor(dictionary=True)
                chk.execute(
                    "SELECT id FROM onedrive_inbox WHERE onedrive_file_id = %s",
                    (od_file_id,),
                )
                existing = chk.fetchone()
                chk.close()
                if existing:
                    continue  # Already staged

                matched_project_id = match_project_from_filename(filename, conn)
                doc_type = _infer_doc_type(filename)

                ins = conn.cursor()
                ins.execute(
                    """
                    INSERT INTO onedrive_inbox
                        (onedrive_account_id, onedrive_file_id, filename, mime_type,
                         web_url, matched_project_id, doc_type, status)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, 'PENDING')
                    """,
                    (acct_id, od_file_id, filename, mime_type, web_url, matched_project_id, doc_type),
                )
                ins.close()
                total_new += 1
                logger.info("  Staged OneDrive file '%s' -> project_id=%s", filename, matched_project_id)

            # Update last_synced_at
            upd = conn.cursor()
            upd.execute(
                "UPDATE onedrive_accounts SET last_synced_at = NOW() WHERE id = %s",
                (acct_id,),
            )
            upd.close()
            conn.commit()

        except Exception as exc:
            logger.error("Error syncing OneDrive account '%s': %s", label, exc)
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


# ── Process a Single OneDrive Inbox Item ──────────────────────────────────────

def process_onedrive_inbox_item(inbox_id: int, project_id: int, doc_type: str, user_id: int) -> Dict[str, Any]:
    """
    Download the file from OneDrive/SharePoint, save it locally,
    register it as a Document, and index it in ChromaDB for RAG.
    """
    from core.database import get_db_connection
    from core.config import settings
    from services.drive_crypto import decrypt_credentials
    from services.onedrive_service import OneDriveService
    from repositories.document_repository import DocumentRepository
    from services.document_service import DocumentService
    from services.rag_service import RAGService

    conn = get_db_connection()
    if not conn:
        return {"success": False, "error": "No DB connection"}

    try:
        cursor = conn.cursor(dictionary=True)
        cursor.execute(
            """
            SELECT oi.*, oa.tenant_id, oa.client_id, oa.client_secret_enc,
                   oa.drive_type, oa.target_user_email, oa.target_drive_id, oa.folder_id
            FROM onedrive_inbox oi
            JOIN onedrive_accounts oa ON oa.id = oi.onedrive_account_id
            WHERE oi.id = %s
            """,
            (inbox_id,),
        )
        row = cursor.fetchone()
        cursor.close()

        if not row:
            conn.close()
            return {"success": False, "error": "OneDrive inbox item not found"}

        if row["status"] in ("PROCESSING", "DONE"):
            conn.close()
            return {"success": False, "error": f"Item already in status: {row['status']}"}

        # Mark item as PROCESSING
        upd = conn.cursor()
        upd.execute(
            "UPDATE onedrive_inbox SET status='PROCESSING', matched_project_id=%s, "
            "doc_type=%s WHERE id=%s",
            (project_id, doc_type, inbox_id),
        )
        upd.close()
        conn.commit()

        # Decrypt secret and instantiate service
        raw_secret = decrypt_credentials(row["client_secret_enc"])
        svc = OneDriveService(
            tenant_id=row["tenant_id"],
            client_id=row["client_id"],
            client_secret=raw_secret,
            drive_type=row["drive_type"],
            target_user_email=row["target_user_email"],
            target_drive_id=row["target_drive_id"],
            folder_id=row["folder_id"],
        )
        raw_secret = None

        file_bytes = svc.download_file(row["onedrive_file_id"])
        if not file_bytes:
            raise RuntimeError("Failed to download file from OneDrive")

        import io
        import re
        import tempfile
        from services.s3_service import S3Service
        
        cursor = conn.cursor(dictionary=True)
        cursor.execute("SELECT project_name FROM projects WHERE id = %s", (project_id,))
        project = cursor.fetchone()
        cursor.close()
        project_name = project.get("project_name", f"Project_{project_id}") if project else f"Project_{project_id}"

        filename = row["filename"]
        ext = os.path.splitext(filename)[1].lower() or ".docx"
        base_name = os.path.splitext(filename)[0]
        safe_base_name = re.sub(r'[^a-zA-Z0-9_\-]', '_', base_name)
        unique_filename = f"onedrive_{safe_base_name}_{uuid.uuid4().hex[:8]}{ext}"
        
        file_obj = io.BytesIO(file_bytes)
        storage_key = S3Service.upload_fileobj(file_obj, project_id, project_name, unique_filename)

        # Register document in repository
        document_id = DocumentRepository.create_document(
            db=conn,
            project_id=project_id,
            document_name=filename,
            document_type=doc_type,
            storage_key=storage_key,
            uploaded_by=user_id,
        )
        conn.commit()

        # Update inbox with local file_path
        upd2 = conn.cursor()
        upd2.execute(
            "UPDATE onedrive_inbox SET file_path=%s WHERE id=%s",
            (storage_key, inbox_id),
        )
        upd2.close()
        conn.commit()

        # Mark inbox item as DONE
        upd3 = conn.cursor()
        upd3.execute(
            "UPDATE onedrive_inbox SET status='DONE', processed_at=NOW() WHERE id=%s",
            (inbox_id,),
        )
        upd3.close()
        conn.commit()

        # Index in RAG ChromaDB
        doc_cursor = conn.cursor(dictionary=True)
        temp_path = os.path.join(tempfile.gettempdir(), f"temp_{uuid.uuid4()}{ext}")
        try:
            with open(temp_path, "wb") as fh:
                fh.write(file_bytes)
            chunks = DocumentService.parse_document(temp_path, ext)
        finally:
            if os.path.exists(temp_path):
                os.remove(temp_path)
        RAGService.index_document(project_id, document_id, filename, doc_type, chunks)
        doc_cursor.close()

        # Mark document completed
        DocumentRepository.update_processing_status(conn, document_id, "COMPLETED")
        conn.commit()

        conn.close()
        return {"success": True, "document_id": document_id}

    except Exception as exc:
        logger.error("process_onedrive_inbox_item error: %s", exc)
        try:
            fail_upd = conn.cursor()
            fail_upd.execute(
                "UPDATE onedrive_inbox SET status='PENDING' WHERE id=%s", (inbox_id,)
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
