import os
import json
import logging
import requests
from typing import List, Dict, Any, Optional
from core.config import settings
from .base import BaseVectorStoreProvider

logger = logging.getLogger(__name__)

class AzureVectorSearchProvider(BaseVectorStoreProvider):
    """
    Azure AI Search / Azure Vector Database Provider.
    Supports enterprise cloud vector storage, vector queries, and hybrid metadata filtering.
    Communicates via Azure AI Search REST API (no heavyweight external SDK required).
    """

    def __init__(self):
        self.endpoint = (getattr(settings, "AZURE_SEARCH_ENDPOINT", "") or os.getenv("AZURE_SEARCH_ENDPOINT", "")).strip().rstrip("/")
        self.api_key = (getattr(settings, "AZURE_SEARCH_API_KEY", "") or os.getenv("AZURE_SEARCH_API_KEY", "")).strip()
        self.index_name = (getattr(settings, "AZURE_SEARCH_INDEX_NAME", "") or os.getenv("AZURE_SEARCH_INDEX_NAME", "acse-documents-index")).strip()
        self.api_version = getattr(settings, "AZURE_SEARCH_API_VERSION", "2024-07-01")
        self._index_initialized = False

        if not self.endpoint:
            logger.warning("[AzureVectorStore] AZURE_SEARCH_ENDPOINT is not configured.")

    @property
    def is_configured(self) -> bool:
        return bool(self.endpoint and self.api_key)

    def _headers(self) -> Dict[str, str]:
        return {
            "Content-Type": "application/json",
            "api-key": self.api_key,
        }

    def _ensure_index(self, vector_dimensions: int = 384) -> None:
        """
        Ensures the Azure AI Search index exists with appropriate vector and metadata fields.
        """
        if self._index_initialized or not self.is_configured:
            return

        check_url = f"{self.endpoint}/indexes/{self.index_name}?api-version={self.api_version}"
        try:
            resp = requests.get(check_url, headers=self._headers(), timeout=10)
            if resp.status_code == 200:
                self._index_initialized = True
                return
            elif resp.status_code == 404:
                # Create index
                create_url = f"{self.endpoint}/indexes?api-version={self.api_version}"
                index_schema = {
                    "name": self.index_name,
                    "fields": [
                        {"name": "id", "type": "Edm.String", "key": True, "filterable": True},
                        {"name": "project_id", "type": "Edm.Int32", "filterable": True, "facetable": True},
                        {"name": "document_id", "type": "Edm.Int32", "filterable": True, "facetable": True},
                        {"name": "document_name", "type": "Edm.String", "searchable": True},
                        {"name": "document_type", "type": "Edm.String", "filterable": True, "facetable": True},
                        {"name": "page_number", "type": "Edm.Int32", "filterable": True},
                        {"name": "chunk_index", "type": "Edm.Int32", "filterable": True},
                        {"name": "content", "type": "Edm.String", "searchable": True},
                        {
                            "name": "vector",
                            "type": "Collection(Edm.Single)",
                            "searchable": True,
                            "dimensions": vector_dimensions,
                            "vectorSearchProfile": "acse-vector-profile",
                        },
                    ],
                    "vectorSearch": {
                        "algorithms": [
                            {"name": "acse-hnsw", "kind": "hnsw"}
                        ],
                        "profiles": [
                            {"name": "acse-vector-profile", "algorithm": "acse-hnsw"}
                        ]
                    }
                }
                c_resp = requests.post(create_url, headers=self._headers(), json=index_schema, timeout=15)
                if c_resp.status_code in (200, 201):
                    logger.info(f"[AzureVectorStore] Successfully created Azure Search index: {self.index_name}")
                    self._index_initialized = True
                else:
                    logger.warning(f"[AzureVectorStore] Could not auto-create index: {c_resp.status_code} {c_resp.text}")
        except Exception as ex:
            logger.warning(f"[AzureVectorStore] Index validation check failed: {ex}")

    def add_chunks(
        self,
        project_id: int,
        document_id: int,
        document_name: str,
        document_type: str,
        chunks: List[Dict[str, Any]],
        embeddings: List[List[float]],
    ) -> None:
        if not self.is_configured:
            logger.warning("[AzureVectorStore] Azure Search not configured. Ingestion skipped.")
            return

        dims = len(embeddings[0]) if embeddings and len(embeddings) > 0 else 384
        self._ensure_index(vector_dimensions=dims)

        docs_to_upload = []
        for i, chunk in enumerate(chunks):
            cid = f"proj_{project_id}_doc_{document_id}_chunk_{chunk['chunk_index']}"
            docs_to_upload.append({
                "@search.action": "mergeOrUpload",
                "id": cid,
                "project_id": int(project_id),
                "document_id": int(document_id),
                "document_name": str(document_name),
                "document_type": str(document_type),
                "page_number": int(chunk.get("page_number", -1) or -1),
                "chunk_index": int(chunk["chunk_index"]),
                "content": str(chunk.get("text", "")),
                "vector": embeddings[i] if i < len(embeddings) else [],
            })

        # Batch upload up to 1000 items per request
        batch_size = 500
        url = f"{self.endpoint}/indexes/{self.index_name}/docs/index?api-version={self.api_version}"
        for b_idx in range(0, len(docs_to_upload), batch_size):
            batch = docs_to_upload[b_idx:b_idx + batch_size]
            try:
                resp = requests.post(url, headers=self._headers(), json={"value": batch}, timeout=30)
                if resp.status_code not in (200, 201):
                    logger.error(f"[AzureVectorStore] Failed uploading chunk batch: {resp.status_code} {resp.text}")
            except Exception as e:
                logger.error(f"[AzureVectorStore] Exception indexing chunk batch to Azure: {e}")

    def search(
        self,
        project_id: int,
        query_embedding: List[float],
        document_types: Optional[List[str]] = None,
        top_k: int = 10,
    ) -> Dict[str, Any]:
        if not self.is_configured:
            logger.warning("[AzureVectorStore] Azure Search not configured. Returning empty search.")
            return {"documents": [[]], "metadatas": [[]], "distances": [[]], "ids": [[]]}

        filter_clause = f"project_id eq {project_id}"
        if document_types:
            types_str = ", ".join(f"'{dt}'" for dt in document_types)
            filter_clause += f" and search.in(document_type, '{types_str}')"

        url = f"{self.endpoint}/indexes/{self.index_name}/docs/search?api-version={self.api_version}"
        payload = {
            "count": True,
            "top": top_k,
            "filter": filter_clause,
            "select": "id, content, project_id, document_id, document_name, document_type, page_number, chunk_index",
            "vectors": [
                {
                    "value": query_embedding,
                    "k": top_k,
                    "fields": "vector",
                }
            ],
        }

        try:
            resp = requests.post(url, headers=self._headers(), json=payload, timeout=20)
            if resp.status_code == 200:
                data = resp.json()
                results = data.get("value", [])
                
                doc_texts = []
                metadatas = []
                distances = []
                ids = []

                for r in results:
                    ids.append(r.get("id"))
                    doc_texts.append(r.get("content", ""))
                    score = r.get("@search.score", 1.0)
                    distances.append(max(0.0, 1.0 - (score / 2.0)))
                    metadatas.append({
                        "project_id": r.get("project_id"),
                        "document_id": r.get("document_id"),
                        "document_name": r.get("document_name"),
                        "document_type": r.get("document_type"),
                        "page_number": r.get("page_number"),
                        "chunk_index": r.get("chunk_index"),
                    })

                return {
                    "documents": [doc_texts],
                    "metadatas": [metadatas],
                    "distances": [distances],
                    "ids": [ids],
                }
            else:
                logger.error(f"[AzureVectorStore] Search query failed: {resp.status_code} {resp.text}")
        except Exception as e:
            logger.error(f"[AzureVectorStore] Exception querying Azure Search: {e}")

        return {"documents": [[]], "metadatas": [[]], "distances": [[]], "ids": [[]]}

    def delete_document_chunks(self, document_id: int) -> None:
        if not self.is_configured:
            return

        # Query all IDs matching document_id
        url_search = f"{self.endpoint}/indexes/{self.index_name}/docs/search?api-version={self.api_version}"
        payload = {
            "filter": f"document_id eq {document_id}",
            "select": "id",
            "top": 1000,
        }
        try:
            resp = requests.post(url_search, headers=self._headers(), json=payload, timeout=20)
            if resp.status_code == 200:
                hits = resp.json().get("value", [])
                if hits:
                    del_payload = {"value": [{"@search.action": "delete", "id": h["id"]} for h in hits]}
                    url_idx = f"{self.endpoint}/indexes/{self.index_name}/docs/index?api-version={self.api_version}"
                    requests.post(url_idx, headers=self._headers(), json=del_payload, timeout=20)
        except Exception as e:
            logger.error(f"[AzureVectorStore] Exception deleting document chunks: {e}")

    def probe_document(self, project_id: int, document_id: int) -> Dict[str, Any]:
        if not self.is_configured:
            return {"ids": [], "count": 0}

        url = f"{self.endpoint}/indexes/{self.index_name}/docs/search?api-version={self.api_version}"
        payload = {
            "filter": f"project_id eq {project_id} and document_id eq {document_id}",
            "select": "id",
            "top": 5,
            "count": True,
        }
        try:
            resp = requests.post(url, headers=self._headers(), json=payload, timeout=10)
            if resp.status_code == 200:
                data = resp.json()
                total = data.get("@odata.count", len(data.get("value", [])))
                ids = [item["id"] for item in data.get("value", [])]
                return {"ids": ids, "count": total}
        except Exception:
            pass
        return {"ids": [], "count": 0}

    def get_collection(self) -> Any:
        """
        Returns a collection-like adapter wrapper for backward compatibility
        with legacy Chroma collection.get() calls.
        """
        class _AzureCollectionAdapter:
            def __init__(self, provider):
                self._provider = provider

            def get(self, where=None, limit=10, include=None):
                p_id = None
                d_id = None
                if where:
                    and_list = where.get("$and", [])
                    for cond in and_list:
                        if "project_id" in cond:
                            p_val = cond["project_id"]
                            p_id = p_val.get("$eq", p_val) if isinstance(p_val, dict) else p_val
                        if "document_id" in cond:
                            d_val = cond["document_id"]
                            d_id = d_val.get("$eq", d_val) if isinstance(d_val, dict) else d_val

                if p_id is not None and d_id is not None:
                    probe = self._provider.probe_document(int(p_id), int(d_id))
                    return {"ids": probe.get("ids", [])}
                return {"ids": []}

        return _AzureCollectionAdapter(self)

