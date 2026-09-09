from core.config import settings
from .base import BaseDBProvider
from .default_provider import DefaultDBProvider
from .aws_provider import AWSRDSProvider
from .azure_provider import AzureDBProvider

class DBFactory:
    _instance = None

    @classmethod
    def get_provider(cls) -> BaseDBProvider:
        if cls._instance is not None:
            return cls._instance

        provider_name = getattr(settings, "DB_PROVIDER", "DEFAULT")
        provider_name = provider_name.strip().upper()

        if provider_name == "AWS":
            cls._instance = AWSRDSProvider()
        elif provider_name == "AZURE":
            cls._instance = AzureDBProvider()
        else:
            cls._instance = DefaultDBProvider()

        return cls._instance
