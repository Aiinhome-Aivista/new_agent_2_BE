"""
Google Drive Service
====================
Stateless helper that authenticates with a per-account service-account JSON
and fetches / downloads files from a specified Drive folder.

Security notes:
  - Credentials JSON is NEVER logged.
  - Only the minimal Drive scope (readonly) is requested.
  - No credentials are written to disk after service construction.
"""
import io
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional

logger = logging.getLogger("google_drive_service")

SCOPES = ["https://www.googleapis.com/auth/drive.readonly"]


class GoogleDriveService:
    """
    Per-account Drive helper.  Constructed with the raw credentials dict
    and a folder ID — both come from the encrypted DB row, never from .env.
    """

    def __init__(self, credentials_dict: dict, folder_id: str):
        self._folder_id = folder_id
        self._service = self._build_service(credentials_dict)

    # ── Internal ────────────────────────────────────────────────────────────

    @staticmethod
    def _build_service(credentials_dict: dict):
        """Build a Drive API service from a service-account credentials dict."""
        try:
            from google.oauth2 import service_account
            from googleapiclient.discovery import build

            creds = service_account.Credentials.from_service_account_info(
                credentials_dict,
                scopes=SCOPES,
            )
            return build("drive", "v3", credentials=creds, cache_discovery=False)
        except Exception as exc:
            # Never log the credentials dict itself
            logger.error("Failed to build Drive service: %s", exc)
            raise RuntimeError(f"Google Drive authentication failed: {exc}") from exc

    # ── Public API ───────────────────────────────────────────────────────────

    def list_recent_files(self, since_hours: int = 24) -> list[dict]:
        """
        Return files in the configured folder modified within the last
        `since_hours` hours.  Supports .docx, .txt and native Google Docs.
        """
        cutoff = datetime.now(timezone.utc) - timedelta(hours=max(since_hours, 1))
        cutoff_str = cutoff.strftime("%Y-%m-%dT%H:%M:%S")

        query = (
            f"'{self._folder_id}' in parents"
            f" and modifiedTime > '{cutoff_str}'"
            f" and trashed = false"
            f" and ("
            f"   mimeType = 'application/vnd.google-apps.document'"
            f"   or mimeType = 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'"
            f"   or mimeType = 'text/plain'"
            f" )"
        )

        try:
            result = (
                self._service.files()
                .list(
                    q=query,
                    fields="files(id,name,mimeType,modifiedTime,size)",
                    orderBy="modifiedTime desc",
                    pageSize=50,
                )
                .execute()
            )
            return result.get("files", [])
        except Exception as exc:
            logger.error("Drive list_recent_files error: %s", exc)
            return []

    def download_file(self, file_id: str, mime_type: str) -> Optional[bytes]:
        """
        Download a file by ID.
        - Native Google Docs are exported as .docx.
        - All other types are downloaded directly.
        Returns raw bytes or None on failure.
        """
        try:
            if mime_type == "application/vnd.google-apps.document":
                # Export native Google Doc as .docx
                request = self._service.files().export_media(
                    fileId=file_id,
                    mimeType="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                )
            else:
                request = self._service.files().get_media(fileId=file_id)

            buf = io.BytesIO()
            from googleapiclient.http import MediaIoBaseDownload
            downloader = MediaIoBaseDownload(buf, request, chunksize=4 * 1024 * 1024)
            done = False
            while not done:
                _, done = downloader.next_chunk()
            return buf.getvalue()
        except Exception as exc:
            logger.error("Drive download_file error for %s: %s", file_id, exc)
            return None

    def extract_text(self, file_bytes: bytes, mime_type: str, filename: str) -> str:
        """
        Extract plain text from downloaded bytes.
        Supports .docx (via python-docx) and plain text.
        """
        ext = filename.lower().rsplit(".", 1)[-1] if "." in filename else ""

        # Google Docs exported as docx, or uploaded .docx
        if ext == "docx" or mime_type in (
            "application/vnd.google-apps.document",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ):
            try:
                import docx
                doc = docx.Document(io.BytesIO(file_bytes))
                return "\n".join(p.text for p in doc.paragraphs if p.text.strip())
            except Exception as exc:
                logger.warning("docx extraction failed (%s), falling back to raw: %s", filename, exc)

        # Plain text fallback
        try:
            return file_bytes.decode("utf-8", errors="replace")
        except Exception:
            return ""
