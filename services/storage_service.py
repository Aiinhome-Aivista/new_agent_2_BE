from services.storage.factory import StorageFactory

class StorageService:
    """
    Facade for interacting with the active storage provider.
    """

    @classmethod
    def upload_fileobj(cls, fileobj, project_id: int, project_name: str, filename: str) -> str:
        return StorageFactory.get_provider().upload_fileobj(fileobj, project_id, project_name, filename)

    @classmethod
    def generate_presigned_url(cls, storage_key: str, expiration: int = 3600) -> str:
        return StorageFactory.get_provider().generate_presigned_url(storage_key, expiration)

    @classmethod
    def delete_file(cls, storage_key: str) -> None:
        return StorageFactory.get_provider().delete_file(storage_key)

    @classmethod
    def download_to_temp_file(cls, storage_key: str, temp_path: str) -> str:
        return StorageFactory.get_provider().download_to_temp_file(storage_key, temp_path)

# Keeping S3Service alias for backward compatibility temporarily if needed,
# but it's better to update imports.
S3Service = StorageService
