import chromadb
from chromadb.config import Settings as ChromaSettings
from core.config import settings
import os

class ChromaService:
    _client = None
    _collection = None

    @classmethod
    def get_client(cls):
        if cls._client is None:
            db_path = os.path.abspath(settings.CHROMA_PATH)
            os.makedirs(db_path, exist_ok=True)
            cls._client = chromadb.PersistentClient(path=db_path, settings=ChromaSettings(anonymized_telemetry=False))
        return cls._client

    @classmethod
    def get_collection(cls):
        if cls._collection is None:
            client = cls.get_client()
            cls._collection = client.get_or_create_collection(name="acse_documents")
        return cls._collection

    @classmethod
    def add_chunks(cls, project_id: int, document_id: int, document_name: str, document_type: str, chunks: list[dict], embeddings: list[list[float]]):
        collection = cls.get_collection()
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
                "page_number": chunk.get("page_number", -1) or -1, # chroma prefers numbers or strings, avoid None
                "chunk_index": chunk["chunk_index"]
            })
            
        if ids:
            collection.add(
                ids=ids,
                embeddings=embeddings,
                metadatas=metadatas,
                documents=documents
            )

    @classmethod
    def search(cls, project_id: int, query_embedding: list[float], document_types: list[str] = None, top_k: int = 10):
        collection = cls.get_collection()
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
            include=["documents", "metadatas", "distances"]
        )
        return results

    @classmethod
    def delete_document_chunks(cls, document_id: int):
        collection = cls.get_collection()
        collection.delete(where={"document_id": document_id})
