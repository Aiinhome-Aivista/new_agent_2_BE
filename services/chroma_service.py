from typing import List, Dict, Any, Optional
from services.vector_store.factory import VectorStoreFactory

class ChromaService:
    """
    ChromaService facade for vector database interactions.
    Delegates all operations dynamically to the active provider (ChromaDB or Azure AI Search)
    resolved by VectorStoreFactory, ensuring 100% backward compatibility for all existing callers.
    """

    @classmethod
    def get_provider(cls):
        return VectorStoreFactory.get_provider()

    @classmethod
    def get_client(cls):
        provider = cls.get_provider()
        if hasattr(provider, "get_client"):
            return provider.get_client()
        return None

    @classmethod
    def get_collection(cls):
        return cls.get_provider().get_collection()

    @classmethod
    def add_chunks(
        cls,
        project_id: int,
        document_id: int,
        document_name: str,
        document_type: str,
        chunks: list,
        embeddings: list,
    ) -> None:
        return cls.get_provider().add_chunks(
            project_id=project_id,
            document_id=document_id,
            document_name=document_name,
            document_type=document_type,
            chunks=chunks,
            embeddings=embeddings,
        )

    @classmethod
    def search(
        cls,
        project_id: int,
        query_embedding: list,
        document_types: list = None,
        top_k: int = 10,
    ) -> Dict[str, Any]:
        return cls.get_provider().search(
            project_id=project_id,
            query_embedding=query_embedding,
            document_types=document_types,
            top_k=top_k,
        )

    @classmethod
    def delete_document_chunks(cls, document_id: int) -> None:
        return cls.get_provider().delete_document_chunks(document_id)

    @classmethod
    def probe_document(cls, project_id: int, document_id: int) -> Dict[str, Any]:
        return cls.get_provider().probe_document(project_id, document_id)

# Unified alias
VectorStoreService = ChromaService
