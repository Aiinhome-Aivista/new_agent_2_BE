from abc import ABC, abstractmethod
from typing import List, Dict, Any, Optional

class BaseVectorStoreProvider(ABC):
    """
    Abstract Base Class for Vector Database Providers.
    Standardizes chunk ingestion, semantic vector retrieval, probe querying, and deletion
    across local ChromaDB, Azure AI Search, and any future vector stores.
    """

    @abstractmethod
    def add_chunks(
        self,
        project_id: int,
        document_id: int,
        document_name: str,
        document_type: str,
        chunks: List[Dict[str, Any]],
        embeddings: List[List[float]],
    ) -> None:
        """
        Embeds and adds document chunks to the active vector database index.
        """
        pass

    @abstractmethod
    def search(
        self,
        project_id: int,
        query_embedding: List[float],
        document_types: Optional[List[str]] = None,
        top_k: int = 10,
    ) -> Dict[str, Any]:
        """
        Executes a vector similarity search filtered by project_id and optional document_types.
        Returns a standardized dictionary matching ChromaDB schema:
        {
            "documents": [[text1, text2, ...]],
            "metadatas": [[meta1, meta2, ...]],
            "distances": [[dist1, dist2, ...]],
            "ids": [[id1, id2, ...]]
        }
        """
        pass

    @abstractmethod
    def delete_document_chunks(self, document_id: int) -> None:
        """
        Deletes all vector chunks and metadata corresponding to document_id.
        """
        pass

    @abstractmethod
    def get_collection(self) -> Any:
        """
        Provides direct access to collection or wrapper for low-level collection queries.
        """
        pass

    @abstractmethod
    def probe_document(self, project_id: int, document_id: int) -> Dict[str, Any]:
        """
        Checks how many vectors are indexed for a project/document.
        Returns {"ids": [...], "count": int}.
        """
        pass

