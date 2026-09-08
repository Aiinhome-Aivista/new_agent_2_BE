import boto3
import re
from botocore.exceptions import ClientError
from core.config import settings
from .base import BaseStorageProvider

class AWSStorageProvider(BaseStorageProvider):
    def __init__(self):
        self.s3 = boto3.client(
            's3',
            aws_access_key_id=settings.AWS_ACCESS_KEY_ID,
            aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY,
            region_name=settings.AWS_DEFAULT_REGION
        )
        self.bucket = settings.AWS_S3_BUCKET_NAME

    def _get_s3_key(self, project_id: int, project_name: str, filename: str) -> str:
        base_folder = settings.AWS_S3_BASE_FOLDER.strip("/")
        agent_folder = settings.AWS_S3_AGENT_FOLDER.strip("/")
        safe_name = re.sub(r'[^a-zA-Z0-9_\-]', '_', project_name) if project_name else "Project"
        project_folder = f"{project_id}_{safe_name}"
        parts = [p for p in [base_folder, agent_folder, project_folder, filename] if p]
        return "/".join(parts)

    def upload_fileobj(self, fileobj, project_id: int, project_name: str, filename: str) -> str:
        if hasattr(fileobj, "seek"):
            try:
                fileobj.seek(0)
            except Exception:
                pass

        s3_key = self._get_s3_key(project_id, project_name, filename)
        try:
            self.s3.upload_fileobj(fileobj, self.bucket, s3_key)
            print(f"[StorageService] Mode: AWS_S3 | Uploaded to S3: {s3_key}")
            return s3_key
        except ClientError as e:
            raise Exception(f"Failed to upload to S3: {e}")

    def generate_presigned_url(self, storage_key: str, expiration: int = 3600) -> str:
        try:
            response = self.s3.generate_presigned_url(
                'get_object',
                Params={'Bucket': self.bucket, 'Key': storage_key},
                ExpiresIn=expiration
            )
            return response
        except ClientError as e:
            raise Exception(f"Failed to generate presigned URL: {e}")

    def delete_file(self, storage_key: str) -> None:
        try:
            self.s3.delete_object(Bucket=self.bucket, Key=storage_key)
            print(f"[StorageService] Deleted S3 object: {storage_key}")
        except Exception as e:
            print(f"[StorageService] Warning: Failed to delete S3 object {storage_key}: {e}")

    def download_to_temp_file(self, storage_key: str, temp_path: str) -> str:
        try:
            self.s3.download_file(self.bucket, storage_key, temp_path)
            return temp_path
        except ClientError as e:
            raise Exception(f"Failed to download from S3 (Key: {storage_key}): {e}")
