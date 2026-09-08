import os
import shutil
import re
from core.config import settings
from .base import BaseStorageProvider

class LocalStorageProvider(BaseStorageProvider):
    def _get_local_path(self, project_id: int, project_name: str, filename: str) -> str:
        safe_name = re.sub(r'[^a-zA-Z0-9_\-]', '_', project_name) if project_name else "Project"
        project_folder = f"{project_id}_{safe_name}"
        folder_path = os.path.join(settings.UPLOAD_PATH, project_folder)
        os.makedirs(folder_path, exist_ok=True)
        local_path = os.path.join(folder_path, filename)
        return local_path.replace("\\", "/")

    def upload_fileobj(self, fileobj, project_id: int, project_name: str, filename: str) -> str:
        if hasattr(fileobj, "seek"):
            try:
                fileobj.seek(0)
            except Exception:
                pass
        
        local_path = self._get_local_path(project_id, project_name, filename)
        try:
            with open(local_path, "wb") as buffer:
                shutil.copyfileobj(fileobj, buffer)
            print(f"[StorageService] Mode: LOCAL | Saved locally: {local_path}")
            return local_path
        except Exception as e:
            raise Exception(f"Failed to save file locally: {e}")

    def generate_presigned_url(self, storage_key: str, expiration: int = 3600) -> str:
        # Local storage doesn't have a presigned URL mechanism built-in.
        # Can return just the path or a constructed URL if the backend serves it.
        return storage_key

    def delete_file(self, storage_key: str) -> None:
        if os.path.exists(storage_key):
            try:
                os.remove(storage_key)
                print(f"[StorageService] Deleted local file: {storage_key}")
            except Exception as e:
                print(f"[StorageService] Warning: Failed to delete local file {storage_key}: {e}")

    def download_to_temp_file(self, storage_key: str, temp_path: str) -> str:
        if os.path.exists(storage_key):
            shutil.copyfile(storage_key, temp_path)
            return temp_path
        raise Exception(f"Local file not found: {storage_key}")
