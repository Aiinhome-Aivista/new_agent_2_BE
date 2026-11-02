import os
from typing import Optional
from core.config import settings
from .base import BaseVectorStoreProvider

class VectorStoreFactory:
    """
    Factory for obtaining the active Vector Database Provider.
    Supports switching between:
      - 'CHROMA' / 'LOCAL' -> ChromaVectorStoreProvider (default)
      - 'AZURE' / 'AZURE_SEARCH' -> AzureVectorSearchProvider
    Configured via VECTOR_DB_PROVIDER in .env.
    """
    _instance: Optional[BaseVectorStoreProvider] = None
    _provider_name: Optional[str] = None

    @classmethod
    def get_provider(cls) -> BaseVectorStoreProvider:
        provider_name = (
            getattr(settings, "VECTOR_DB_PROVIDER", "")
            or os.getenv("VECTOR_DB_PROVIDER", "")
            or getattr(settings, "VECTOR_STORE_MODE", "chroma")
        )
        if not provider_name or provider_name.upper() in ("DEFAULT", "LOCAL", ""):
            provider_name = "chroma"

        provider_name = provider_name.strip().lower()

        # Cache instance unless provider switched
        if cls._instance is not None and cls._provider_name == provider_name:
            return cls._instance

        if provider_name in ("azure", "azure_search", "azure_vector", "azure_ai_search"):
            from .azure_search_provider import AzureVectorSearchProvider
            cls._instance = AzureVectorSearchProvider()
        else:
            from .chroma_provider import ChromaVectorStoreProvider
            cls._instance = ChromaVectorStoreProvider()

        cls._provider_name = provider_name
        return cls._instance

    @classmethod
    def reset(cls) -> None:
        """Resets cached provider instance."""
        cls._instance = None
        cls._provider_name = None

