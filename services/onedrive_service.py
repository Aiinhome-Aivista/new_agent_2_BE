"""
OneDrive / Microsoft Graph API Service
======================================
Stateless helper that authenticates using Microsoft Entra ID (Azure AD)
Application (Client Credentials) flow and interacts with Microsoft Graph API
to list and download files from OneDrive or SharePoint document libraries.

Security notes:
  - Client secret is NEVER logged or cached in plain text on disk.
  - In-memory token caching with expiration checks.
  - Scoped to minimal required Microsoft Graph permissions.
"""
import io
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional, List, Dict, Any
import requests

logger = logging.getLogger("onedrive_service")

GRAPH_BASE_URL = "https://graph.microsoft.com/v1.0"
LOGIN_BASE_URL = "https://login.microsoftonline.com"


class OneDriveService:
    """
    Per-account OneDrive / Microsoft Graph helper.
    Configured with tenant_id, client_id, and encrypted client_secret.
    """

    def __init__(
        self,
        tenant_id: str,
        client_id: str,
        client_secret: str,
        drive_type: str = "USER_DRIVE",
        target_user_email: Optional[str] = None,
        target_drive_id: Optional[str] = None,
        folder_id: Optional[str] = None,
    ):
        self._tenant_id = tenant_id.strip()
        self._client_id = client_id.strip()
        self._client_secret = client_secret.strip()
        self._drive_type = drive_type or "USER_DRIVE"
        self._target_user_email = target_user_email.strip() if target_user_email else None
        self._target_drive_id = target_drive_id.strip() if target_drive_id else None
        self._folder_id = folder_id.strip() if folder_id else "root"

        self._access_token: Optional[str] = None
        self._token_expires_at: Optional[datetime] = None

    # ── Authentication ────────────────────────────────────────────────────────

    def _get_access_token(self) -> str:
        """Obtain or reuse a valid OAuth 2.0 access token via Client Credentials."""
        now = datetime.now(timezone.utc)
        if self._access_token and self._token_expires_at and (self._token_expires_at - now).total_seconds() > 60:
            return self._access_token

        token_url = f"{LOGIN_BASE_URL}/{self._tenant_id}/oauth2/v2.0/token"
        payload = {
            "client_id": self._client_id,
            "client_secret": self._client_secret,
            "scope": "https://graph.microsoft.com/.default",
            "grant_type": "client_credentials",
        }

        try:
            resp = requests.post(token_url, data=payload, timeout=20)
            if resp.status_code != 200:
                logger.error("Microsoft Graph token error (status %d): %s", resp.status_code, resp.text)
                raise RuntimeError(f"Microsoft Entra authentication failed: HTTP {resp.status_code} - {resp.text}")

            data = resp.json()
            token = data.get("access_token")
            expires_in = data.get("expires_in", 3600)

            if not token:
                raise RuntimeError("Access token missing in Microsoft response.")

            self._access_token = token
            self._token_expires_at = now + timedelta(seconds=expires_in)
            return self._access_token
        except Exception as exc:
            logger.error("Failed to authenticate with Microsoft Graph: %s", exc)
            raise RuntimeError(f"OneDrive authentication failed: {exc}") from exc

    def _get_headers(self) -> Dict[str, str]:
        token = self._get_access_token()
        return {
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
        }

    # ── Folder & Item Endpoints ───────────────────────────────────────────────

    def _build_children_url(self) -> str:
        """Construct the Microsoft Graph URL for listing children of the target folder."""
        folder = self._folder_id if self._folder_id and self._folder_id != "root" else None

        if self._drive_type == "SHAREPOINT_DRIVE" and self._target_drive_id:
            if folder:
                return f"{GRAPH_BASE_URL}/drives/{self._target_drive_id}/items/{folder}/children"
            return f"{GRAPH_BASE_URL}/drives/{self._target_drive_id}/root/children"

        # Default: USER_DRIVE
        if self._target_user_email:
            if folder:
                return f"{GRAPH_BASE_URL}/users/{self._target_user_email}/drive/items/{folder}/children"
            return f"{GRAPH_BASE_URL}/users/{self._target_user_email}/drive/root/children"

        if folder:
            return f"{GRAPH_BASE_URL}/drive/items/{folder}/children"
        return f"{GRAPH_BASE_URL}/drive/root/children"

    # ── Public API ───────────────────────────────────────────────────────────

    def list_recent_files(self, since_hours: int = 24) -> List[Dict[str, Any]]:
        """
        List files modified within the last `since_hours` hours.
        Supports .docx, .doc, .txt, and .pdf.
        """
        cutoff = datetime.now(timezone.utc) - timedelta(hours=max(since_hours, 1))
        url = self._build_children_url()
        params = {
            "$select": "id,name,file,lastModifiedDateTime,size,webUrl,@microsoft.graph.downloadUrl",
            "$top": "100",
        }

        matching_files: List[Dict[str, Any]] = []

        try:
            headers = self._get_headers()
            resp = requests.get(url, headers=headers, params=params, timeout=30)
            if resp.status_code != 200:
                logger.error("OneDrive list files error (%d): %s", resp.status_code, resp.text)
                return []

            data = resp.json()
            items = data.get("value", [])

            for item in items:
                # Must be a file, not a folder
                if "file" not in item and "folder" in item:
                    continue

                filename = item.get("name", "")
                ext = filename.lower().rsplit(".", 1)[-1] if "." in filename else ""
                if ext not in ("docx", "doc", "txt", "pdf"):
                    continue

                last_mod_str = item.get("lastModifiedDateTime")
                if last_mod_str:
                    try:
                        # Parse ISO 8601 string (e.g. 2026-03-01T12:00:00Z)
                        clean_ts = last_mod_str.replace("Z", "+00:00")
                        mod_time = datetime.fromisoformat(clean_ts)
                        if mod_time < cutoff:
                            continue
                    except Exception as parse_err:
                        logger.warning("Could not parse timestamp %s: %s", last_mod_str, parse_err)

                mime_type = item.get("file", {}).get("mimeType", "")
                if not mime_type:
                    if ext in ("docx", "doc"):
                        mime_type = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
                    elif ext == "pdf":
                        mime_type = "application/pdf"
                    else:
                        mime_type = "text/plain"

                matching_files.append({
                    "id": item.get("id"),
                    "name": filename,
                    "mimeType": mime_type,
                    "lastModifiedDateTime": last_mod_str,
                    "size": item.get("size", 0),
                    "webUrl": item.get("webUrl", ""),
                    "downloadUrl": item.get("@microsoft.graph.downloadUrl"),
                })

            return matching_files

        except Exception as exc:
            logger.error("Error in OneDrive list_recent_files: %s", exc)
            return []

    def download_file(self, file_id: str, download_url: Optional[str] = None) -> Optional[bytes]:
        """
        Download the binary contents of a OneDrive file.
        Uses the pre-authenticated download URL if provided, or requests /content.
        """
        try:
            # 1. Direct download URL if available (fastest, pre-signed by Microsoft)
            if download_url:
                resp = requests.get(download_url, stream=True, timeout=60)
                if resp.status_code == 200:
                    return resp.content

            # 2. Fallback to /content endpoint
            headers = self._get_headers()
            if self._drive_type == "SHAREPOINT_DRIVE" and self._target_drive_id:
                content_url = f"{GRAPH_BASE_URL}/drives/{self._target_drive_id}/items/{file_id}/content"
            elif self._target_user_email:
                content_url = f"{GRAPH_BASE_URL}/users/{self._target_user_email}/drive/items/{file_id}/content"
            else:
                content_url = f"{GRAPH_BASE_URL}/drive/items/{file_id}/content"

            resp = requests.get(content_url, headers=headers, allow_redirects=True, timeout=60)
            if resp.status_code == 200:
                return resp.content

            logger.error("Failed to download file %s: status %d", file_id, resp.status_code)
            return None
        except Exception as exc:
            logger.error("OneDrive download_file error for %s: %s", file_id, exc)
            return None

    def extract_text(self, file_bytes: bytes, filename: str) -> str:
        """Extract plain text from downloaded file bytes."""
        ext = filename.lower().rsplit(".", 1)[-1] if "." in filename else ""

        if ext in ("docx", "doc"):
            try:
                import docx
                doc = docx.Document(io.BytesIO(file_bytes))
                return "\n".join(p.text for p in doc.paragraphs if p.text.strip())
            except Exception as exc:
                logger.warning("docx extraction failed (%s), fallback: %s", filename, exc)

        try:
            return file_bytes.decode("utf-8", errors="replace")
        except Exception:
            return ""
