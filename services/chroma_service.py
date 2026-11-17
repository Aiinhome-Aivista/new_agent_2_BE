from services.vector_store_service import VectorStoreService

class ChromaService(VectorStoreService):
    """
    ChromaService facade for vector database interactions.
    Subclasses VectorStoreService to avoid duplicate code while providing
    100% backward compatibility for all existing callers.
    """

    @classmethod
    def get_client(cls):
        provider = cls.get_provider()
        if hasattr(provider, "get_client"):
            return provider.get_client()
        return None
