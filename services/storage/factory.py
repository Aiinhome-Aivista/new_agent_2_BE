from core.config import settings
from .base import BaseStorageProvider
from .local_storage import LocalStorageProvider
from .aws_storage import AWSStorageProvider
from .azure_storage import AzureStorageProvider

class StorageFactory:
    _instance = None

    @classmethod
    def get_provider(cls) -> BaseStorageProvider:
        if cls._instance is not None:
            return cls._instance

        # Check CLOUD_PROVIDER first, fallback to STORAGE_MODE for backward compatibility
        provider_name = getattr(settings, "CLOUD_PROVIDER", None)
        if not provider_name or provider_name == "DEFAULT":
            provider_name = getattr(settings, "STORAGE_MODE", "local")

        provider_name = provider_name.strip().lower()

        if provider_name in ["aws", "aws_s3", "s3", "awss3"]:
            cls._instance = AWSStorageProvider()
        elif provider_name == "azure":
            cls._instance = AzureStorageProvider()
        else:
            cls._instance = LocalStorageProvider()

        return cls._instance
