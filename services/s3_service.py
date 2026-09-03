import os
import boto3
from botocore.exceptions import ClientError
from core.config import settings

class S3Service:
    @classmethod
    def _get_client(cls):
        return boto3.client(
            's3',
            aws_access_key_id=settings.AWS_ACCESS_KEY_ID,
            aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY,
            region_name=settings.AWS_DEFAULT_REGION
        )
        
    @classmethod
    def get_s3_key(cls, project_id: int, filename: str) -> str:
        # Expected structure: Agents_Doc/Agent_12/{project_id}/{filename}
        base_folder = settings.AWS_S3_BASE_FOLDER.strip("/")
        agent_folder = settings.AWS_S3_AGENT_FOLDER.strip("/")
        
        # Combine parts, filtering out empty strings in case some are not set
        parts = [p for p in [base_folder, agent_folder, str(project_id), filename] if p]
        return "/".join(parts)

    @classmethod
    def upload_fileobj(cls, fileobj, project_id: int, filename: str) -> str:
        s3 = cls._get_client()
        s3_key = cls.get_s3_key(project_id, filename)
        
        try:
            s3.upload_fileobj(fileobj, settings.AWS_S3_BUCKET_NAME, s3_key)
            return s3_key
        except ClientError as e:
            raise Exception(f"Failed to upload to S3: {e}")

    @classmethod
    def generate_presigned_url(cls, s3_key: str, expiration=3600) -> str:
        s3 = cls._get_client()
        try:
            response = s3.generate_presigned_url('get_object',
                                                    Params={'Bucket': settings.AWS_S3_BUCKET_NAME,
                                                            'Key': s3_key},
                                                    ExpiresIn=expiration)
            return response
        except ClientError as e:
            raise Exception(f"Failed to generate presigned URL: {e}")

    @classmethod
    def delete_file(cls, s3_key: str):
        if os.path.exists(s3_key):
            try:
                os.remove(s3_key)
                return
            except Exception:
                pass
        s3 = cls._get_client()
        try:
            s3.delete_object(Bucket=settings.AWS_S3_BUCKET_NAME, Key=s3_key)
        except ClientError as e:
            raise Exception(f"Failed to delete from S3: {e}")

    @classmethod
    def download_to_temp_file(cls, s3_key: str, temp_path: str):
        if os.path.exists(s3_key):
            import shutil
            shutil.copyfile(s3_key, temp_path)
            return temp_path
        s3 = cls._get_client()
        try:
            s3.download_file(settings.AWS_S3_BUCKET_NAME, s3_key, temp_path)
            return temp_path
        except ClientError as e:
            raise Exception(f"Failed to download from S3: {e}")

