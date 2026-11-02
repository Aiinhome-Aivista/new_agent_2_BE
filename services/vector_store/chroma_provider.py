import os
import threading
from typing import List, Dict, Any, Optional
from core.config import settings
from .base import BaseVectorStoreProvider

class ChromaVectorStoreProvider(BaseVectorStoreProvider):
    """
    Local ChromaDB Vector Database Provider.
    Operates in embedded persistent mode on local disk.
    """

    def __init__(self):
        self._client = None
        self._collection = None
        self._lock = threading.RLock()

    def get_client(self):
        if self._client is None:
            with self._lock:
                if self._client is None:
                    import chromadb
                    from chromadb.config import Settings as ChromaSettings
                    db_path = os.path.abspath(settings.CHROMA_PATH)
                    os.makedirs(db_path, exist_ok=True)
                    self._client = chromadb.PersistentClient(
                        path=db_path,
                        settings=ChromaSettings(anonymized_telemetry=False)
                    )
        return self._client

    def get_collection(self):
        if self._collection is None:
            with self._lock:
                if self._collection is None:
                    client = self.get_client()
                    self._collection = client.get_or_create_collection(name="acse_documents")
        return self._collection

    def add_chunks(
        self,
        project_id: int,
        document_id: int,
        document_name: str,
        document_type: str,
        chunks: List[Dict[str, Any]],
        embeddings: List[List[float]],
    ) -> None:
        collection = self.get_collection()
        ids = []
        metadatas = []
        documents = []

        for chunk in chunks:
            chunk_id = f"proj_{project_id}_doc_{document_id}_chunk_{chunk['chunk_index']}"
            ids.append(chunk_id)
            documents.append(chunk["text"])
            metadatas.append({
                "project_id": project_id,
                "document_id": document_id,
                "document_name": document_name,
                "document_type": document_type,
                "page_number": chunk.get("page_number", -1) or -1,
                "chunk_index": chunk["chunk_index"],
            })

        if ids:
            collection.add(
                ids=ids,
                embeddings=embeddings,
                metadatas=metadatas,
                documents=documents,
            )

    def search(
        self,
        project_id: int,
        query_embedding: List[float],
        document_types: Optional[List[str]] = None,
        top_k: int = 10,
    ) -> Dict[str, Any]:
        collection = self.get_collection()
        where_filter = None
        if document_types:
            if len(document_types) == 1:
                where_filter = {"$and": [{"project_id": project_id}, {"document_type": document_types[0]}]}
            else:
                where_filter = {"$and": [{"project_id": project_id}, {"document_type": {"$in": document_types}}]}
        else:
            where_filter = {"project_id": project_id}

        results = collection.query(
            query_embeddings=[query_embedding],
            n_results=top_k,
            where=where_filter,
            include=["documents", "metadatas", "distances"],
        )
        return results

    def delete_document_chunks(self, document_id: int) -> None:
        collection = self.get_collection()
        collection.delete(where={"document_id": document_id})

    def probe_document(self, project_id: int, document_id: int) -> Dict[str, Any]:
        collection = self.get_collection()
        doc_filter = {
            "$and": [
                {"project_id": {"$eq": project_id}},
                {"document_id": {"$eq": document_id}},
            ]
        }
        res = collection.get(where=doc_filter, limit=5, include=["metadatas"])
        ids = res.get("ids") or []
        return {"ids": ids, "count": len(ids)}

