import re
from core.config import settings
from .base import BaseStorageProvider

try:
    from azure.storage.blob import BlobServiceClient
except ImportError:
    BlobServiceClient = None

class AzureStorageProvider(BaseStorageProvider):
    def __init__(self):
        if not BlobServiceClient:
            raise Exception("azure-storage-blob is not installed. Please install it to use Azure Blob Storage.")
        
        self.connection_string = settings.AZURE_STORAGE_CONNECTION_STRING
        self.container_name = settings.AZURE_CONTAINER_NAME
        
        if not self.connection_string or not self.container_name:
            raise ValueError("AZURE_STORAGE_CONNECTION_STRING and AZURE_CONTAINER_NAME must be set in the environment.")
        
        self.blob_service_client = BlobServiceClient.from_connection_string(self.connection_string)
        self.container_client = self.blob_service_client.get_container_client(self.container_name)

    def _get_blob_name(self, project_id: int, project_name: str, filename: str) -> str:
        safe_name = re.sub(r'[^a-zA-Z0-9_\-]', '_', project_name) if project_name else "Project"
        project_folder = f"{project_id}_{safe_name}"
        return f"{project_folder}/{filename}"

    def upload_fileobj(self, fileobj, project_id: int, project_name: str, filename: str) -> str:
        if hasattr(fileobj, "seek"):
            try:
                fileobj.seek(0)
            except Exception:
                pass

        blob_name = self._get_blob_name(project_id, project_name, filename)
        blob_client = self.container_client.get_blob_client(blob_name)
        
        try:
            blob_client.upload_blob(fileobj, overwrite=True)
            print(f"[StorageService] Mode: AZURE | Uploaded to Azure Blob: {blob_name}")
            return blob_name
        except Exception as e:
            raise Exception(f"Failed to upload to Azure Blob: {e}")

    def generate_presigned_url(self, storage_key: str, expiration: int = 3600) -> str:
        # Note: In a real implementation, you would generate a User Delegation SAS token or Account SAS.
        # This requires additional permissions or `azure.storage.blob.generate_blob_sas`.
        # We will return the direct URL if it's publicly accessible, or you can implement SAS logic here.
        blob_client = self.container_client.get_blob_client(storage_key)
        return blob_client.url

    def delete_file(self, storage_key: str) -> None:
        blob_client = self.container_client.get_blob_client(storage_key)
        try:
            blob_client.delete_blob()
            print(f"[StorageService] Deleted Azure Blob object: {storage_key}")
        except Exception as e:
            print(f"[StorageService] Warning: Failed to delete Azure Blob object {storage_key}: {e}")

    def download_to_temp_file(self, storage_key: str, temp_path: str) -> str:
        blob_client = self.container_client.get_blob_client(storage_key)
        try:
            with open(temp_path, "wb") as f:
                download_stream = blob_client.download_blob()
                f.write(download_stream.readall())
            return temp_path
        except Exception as e:
            raise Exception(f"Failed to download from Azure Blob (Key: {storage_key}): {e}")
