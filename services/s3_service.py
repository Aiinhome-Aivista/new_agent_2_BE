import os
import shutil
import re
import boto3
from botocore.exceptions import ClientError
from core.config import settings

class S3Service:
    @classmethod
    def is_s3_mode(cls) -> bool:
        """Check if storage mode is configured for AWS S3."""
        mode = getattr(settings, "STORAGE_MODE", "local")
        return str(mode).strip().lower() in ["aws_s3", "s3", "awss3"]

    @classmethod
    def _get_client(cls):
        return boto3.client(
            's3',
            aws_access_key_id=settings.AWS_ACCESS_KEY_ID,
            aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY,
            region_name=settings.AWS_DEFAULT_REGION
        )
        
    @classmethod
    def get_s3_key(cls, project_id: int, project_name: str, filename: str) -> str:
        # Expected structure: Agents_Doc/Agent_12/{project_id}_{project_name}/{filename}
        base_folder = settings.AWS_S3_BASE_FOLDER.strip("/")
        agent_folder = settings.AWS_S3_AGENT_FOLDER.strip("/")
        
        # Sanitize project name
        safe_name = re.sub(r'[^a-zA-Z0-9_\-]', '_', project_name) if project_name else "Project"
        project_folder = f"{project_id}_{safe_name}"
        
        # Combine parts, filtering out empty strings in case some are not set
        parts = [p for p in [base_folder, agent_folder, project_folder, filename] if p]
        return "/".join(parts)

    @classmethod
    def get_local_path(cls, project_id: int, project_name: str, filename: str) -> str:
        """Generates a structured local file storage path."""
        safe_name = re.sub(r'[^a-zA-Z0-9_\-]', '_', project_name) if project_name else "Project"
        project_folder = f"{project_id}_{safe_name}"
        folder_path = os.path.join(settings.UPLOAD_PATH, project_folder)
        os.makedirs(folder_path, exist_ok=True)
        local_path = os.path.join(folder_path, filename)
        return local_path.replace("\\", "/")

    @classmethod
    def upload_fileobj(cls, fileobj, project_id: int, project_name: str, filename: str) -> str:
        """
        Uploads/saves a file object based on STORAGE_MODE:
        - "aws_s3": Uploads to AWS S3 bucket under structured prefix.
        - "local": Saves to local UPLOAD_PATH folder.
        Returns the resulting storage key or local file path.
        """
        # Ensure file cursor is at beginning if seekable
        if hasattr(fileobj, "seek"):
            try:
                fileobj.seek(0)
            except Exception:
                pass

        if cls.is_s3_mode():
            s3 = cls._get_client()
            s3_key = cls.get_s3_key(project_id, project_name, filename)
            try:
                s3.upload_fileobj(fileobj, settings.AWS_S3_BUCKET_NAME, s3_key)
                print(f"[StorageService] Mode: AWS_S3 | Uploaded to S3: {s3_key}")
                return s3_key
            except ClientError as e:
                raise Exception(f"Failed to upload to S3: {e}")
        else:
            # Local Storage Mode
            local_path = cls.get_local_path(project_id, project_name, filename)
            try:
                with open(local_path, "wb") as buffer:
                    shutil.copyfileobj(fileobj, buffer)
                print(f"[StorageService] Mode: LOCAL | Saved locally: {local_path}")
                return local_path
            except Exception as e:
                raise Exception(f"Failed to save file locally: {e}")

    @classmethod
    def generate_presigned_url(cls, storage_key: str, expiration=3600) -> str:
        """
        Generates a presigned URL for S3 objects.
        """
        s3 = cls._get_client()
        try:
            response = s3.generate_presigned_url(
                'get_object',
                Params={'Bucket': settings.AWS_S3_BUCKET_NAME, 'Key': storage_key},
                ExpiresIn=expiration
            )
            return response
        except ClientError as e:
            raise Exception(f"Failed to generate presigned URL: {e}")

    @classmethod
    def delete_file(cls, storage_key: str):
        """
        Deletes the file from local storage if exists, or from S3 if S3 key.
        """
        # 1. Attempt local file deletion first
        if os.path.exists(storage_key):
            try:
                os.remove(storage_key)
                print(f"[StorageService] Deleted local file: {storage_key}")
                return
            except Exception as e:
                print(f"[StorageService] Warning: Failed to delete local file {storage_key}: {e}")

        # 2. Attempt S3 deletion if S3 client is configured
        try:
            s3 = cls._get_client()
            s3.delete_object(Bucket=settings.AWS_S3_BUCKET_NAME, Key=storage_key)
            print(f"[StorageService] Deleted S3 object: {storage_key}")
        except Exception as e:
            print(f"[StorageService] Warning: Failed to delete S3 object {storage_key}: {e}")

    @classmethod
    def download_to_temp_file(cls, storage_key: str, temp_path: str):
        """
        Copies local file to temp_path if local, or downloads from S3.
        """
        # 1. Check if file is already on local disk
        if os.path.exists(storage_key):
            shutil.copyfile(storage_key, temp_path)
            return temp_path

        # 2. Otherwise download from AWS S3
        s3 = cls._get_client()
        try:
            s3.download_file(settings.AWS_S3_BUCKET_NAME, storage_key, temp_path)
            return temp_path
        except ClientError as e:
            raise Exception(f"Failed to download from S3 (Key: {storage_key}): {e}")

# Alias for semantic clarity across codebase
StorageService = S3Service

