import os
import re
import logging
from typing import Any, Optional

from core.config import settings
from .base import BaseStorageProvider

logger = logging.getLogger(__name__)

try:
    from azure.storage.blob import BlobServiceClient
except ImportError:
    BlobServiceClient = None

try:
    from azure.identity import DefaultAzureCredential
except ImportError:
    DefaultAzureCredential = None


class AzureStorageProvider(BaseStorageProvider):
    """Azure Blob Storage provider using DefaultAzureCredential (Managed Identity / Entra ID)
    with backward-compatible support for connection strings and full BaseStorageProvider contract."""

    def __init__(self):
        if not BlobServiceClient:
            raise ImportError("azure-storage-blob is not installed. Please install it to use Azure Blob Storage.")

        self.blob_service_client = None
        self.credential = None
        self.container_client = None

        # Read configuration from settings (with os.getenv fallback)
        self.account_name = (
            getattr(settings, "AZURE_STORAGE_ACCOUNT", "") or os.getenv("AZURE_STORAGE_ACCOUNT", "")
        ).strip()
        self.container_name = (
            getattr(settings, "AZURE_CONTAINER_NAME", "") or os.getenv("AZURE_CONTAINER_NAME", "")
        ).strip()
        self.connection_string = (
            getattr(settings, "AZURE_STORAGE_CONNECTION_STRING", "") or os.getenv("AZURE_STORAGE_CONNECTION_STRING", "")
        ).strip()

        if not self.container_name:
            raise ValueError("AZURE_CONTAINER_NAME environment variable is required.")

        try:
            # 1. Primary: Authenticate using Managed Identity / Entra ID via DefaultAzureCredential
            if self.account_name:
                if not DefaultAzureCredential:
                    raise ImportError("azure-identity is not installed. Please install azure-identity to use DefaultAzureCredential.")

                self.credential = DefaultAzureCredential()
                account_url = f"https://{self.account_name}.blob.core.windows.net"
                self.blob_service_client = BlobServiceClient(
                    account_url=account_url,
                    credential=self.credential,
                )
                logger.info(
                    "Azure Blob Storage initialized successfully using DefaultAzureCredential (Account: %s, Container: %s).",
                    self.account_name,
                    self.container_name,
                )

            # 2. Secondary fallback: Connection String
            elif self.connection_string:
                self.blob_service_client = BlobServiceClient.from_connection_string(self.connection_string)
                logger.info(
                    "Azure Blob Storage initialized successfully using Connection String (Container: %s).",
                    self.container_name,
                )

            else:
                raise ValueError(
                    "Either AZURE_STORAGE_ACCOUNT (for DefaultAzureCredential) or "
                    "AZURE_STORAGE_CONNECTION_STRING must be set in the environment."
                )

            self.container_client = self.blob_service_client.get_container_client(self.container_name)

        except Exception as e:
            logger.exception("Azure Blob Storage initialization failed: %s", e)
            raise

    def _get_blob_name(
        self,
        project_id: Optional[int] = None,
        project_name: Optional[str] = None,
        filename: str = "",
    ) -> str:
        """
        Builds the blob path.
        - If both project_id and project_name: '<project_id>_<project_name>/<filename>'
        - If only project_name: '<project_name>/<filename>'
        - If only project_id: '<project_id>/<filename>'
        - Otherwise: '<filename>'
        """
        folder_name = None
        safe_name = re.sub(r'[^a-zA-Z0-9_\- ]', '_', str(project_name)).strip() if project_name else ""

        if project_id is not None and safe_name:
            folder_name = f"{project_id}_{safe_name}"
        elif safe_name:
            folder_name = safe_name
        elif project_id is not None:
            folder_name = str(project_id)

        return f"{folder_name}/{filename}" if folder_name else str(filename)

    def upload_fileobj(self, fileobj: Any, project_id: Optional[int], project_name: Optional[str], filename: str) -> str:
        """
        Uploads/saves a file object to Azure Blob Storage (BaseStorageProvider implementation).
        Returns the resulting storage key (blob name).
        """
        if not self.blob_service_client or not self.container_client:
            raise Exception("Azure Blob Storage client is not initialized.")

        blob_name = self._get_blob_name(project_id=project_id, project_name=project_name, filename=filename)
        blob_client = self.container_client.get_blob_client(blob_name)

        if hasattr(fileobj, "seek"):
            try:
                fileobj.seek(0)
            except Exception:
                pass

        try:
            blob_client.upload_blob(fileobj, overwrite=True)
            logger.info(
                "Successfully uploaded %s to Azure Blob Storage as %s/%s",
                filename,
                self.container_name,
                blob_name,
            )
            print(f"[StorageService] Mode: AZURE | Uploaded to Azure Blob: {blob_name}")
            return blob_name
        except Exception as e:
            logger.exception("Failed to upload %s to Azure Blob Storage: %s", filename, e)
            raise Exception(f"Failed to upload to Azure Blob: {e}")

    def save_file(
        self,
        file_name: str,
        file_stream: Any,
        project_id: Optional[int] = None,
        project_name: Optional[str] = None,
    ) -> Optional[str]:
        """
        Uploads a file to Azure Blob Storage using the save_file signature.
        Returns the blob name on success, or None on failure.
        """
        try:
            return self.upload_fileobj(
                fileobj=file_stream,
                project_id=project_id,
                project_name=project_name,
                filename=file_name,
            )
        except Exception:
            return None

    def generate_presigned_url(self, storage_key: str, expiration: int = 3600) -> str:
        """
        Generates a presigned URL or appropriate access URL for the stored blob object.
        Attempts User Delegation SAS when using DefaultAzureCredential, or falls back to direct URL.
        """
        if not self.container_client:
            raise Exception("Azure Blob Storage client is not initialized.")

        blob_client = self.container_client.get_blob_client(storage_key)

        try:
            if self.credential and self.account_name:
                from datetime import datetime, timedelta, timezone
                from azure.storage.blob import generate_blob_sas, BlobSasPermissions

                start_time = datetime.now(timezone.utc) - timedelta(minutes=5)
                expiry_time = datetime.now(timezone.utc) + timedelta(seconds=expiration)

                user_delegation_key = self.blob_service_client.get_user_delegation_key(
                    key_start_time=start_time,
                    key_expiry_time=expiry_time,
                )
                sas_token = generate_blob_sas(
                    account_name=self.account_name,
                    container_name=self.container_name,
                    blob_name=storage_key,
                    user_delegation_key=user_delegation_key,
                    permission=BlobSasPermissions(read=True),
                    expiry=expiry_time,
                    start=start_time,
                )
                return f"{blob_client.url}?{sas_token}"
        except Exception as e:
            logger.warning("Could not generate Azure User Delegation SAS (%s). Returning direct blob URL.", e)

        return blob_client.url

    def delete_file(self, storage_key: str) -> None:
        """
        Deletes the file from Azure Blob Storage.
        """
        if not self.container_client:
            logger.error("Azure Blob client is not initialized.")
            return

        blob_client = self.container_client.get_blob_client(storage_key)
        try:
            blob_client.delete_blob()
            logger.info("Deleted Azure Blob object: %s", storage_key)
            print(f"[StorageService] Deleted Azure Blob object: {storage_key}")
        except Exception as e:
            logger.warning("Failed to delete Azure Blob object %s: %s", storage_key, e)
            print(f"[StorageService] Warning: Failed to delete Azure Blob object {storage_key}: {e}")

    def download_to_temp_file(self, storage_key: str, temp_path: str) -> str:
        """
        Downloads the file from Azure Blob Storage to a temporary local path.
        """
        if not self.container_client:
            raise Exception("Azure Blob Storage client is not initialized.")

        blob_client = self.container_client.get_blob_client(storage_key)
        try:
            with open(temp_path, "wb") as f:
                download_stream = blob_client.download_blob()
                f.write(download_stream.readall())
            return temp_path
        except Exception as e:
            logger.exception("Failed to download from Azure Blob (Key: %s): %s", storage_key, e)
            raise Exception(f"Failed to download from Azure Blob (Key: {storage_key}): {e}")

    def close(self) -> None:
        """Close Azure clients and release credential resources."""
        try:
            if self.blob_service_client:
                self.blob_service_client.close()
            if self.credential and hasattr(self.credential, "close"):
                self.credential.close()
            logger.info("Closed Azure Storage client and credential resources.")
        except Exception as e:
            logger.warning("Error while closing Azure Storage resources: %s", e)
