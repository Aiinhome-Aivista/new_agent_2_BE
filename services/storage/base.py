import abc
import typing

class BaseStorageProvider(abc.ABC):
    """
    Abstract base class for storage providers (Local, AWS S3, Azure Blob, etc.)
    """

    @abc.abstractmethod
    def upload_fileobj(self, fileobj: typing.Any, project_id: int, project_name: str, filename: str) -> str:
        """
        Uploads/saves a file object.
        Returns the resulting storage key or local file path.
        """
        pass

    @abc.abstractmethod
    def generate_presigned_url(self, storage_key: str, expiration: int = 3600) -> str:
        """
        Generates a presigned URL or appropriate access URL for the stored object.
        """
        pass

    @abc.abstractmethod
    def delete_file(self, storage_key: str) -> None:
        """
        Deletes the file from storage.
        """
        pass

    @abc.abstractmethod
    def download_to_temp_file(self, storage_key: str, temp_path: str) -> str:
        """
        Downloads or copies the file to a temporary local path.
        Returns the path to the temporary file.
        """
        pass
