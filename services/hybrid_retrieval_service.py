from services.embedding_service import EmbeddingService
from services.chroma_service import ChromaService
from services.bm25_service import BM25Service
from core.config import settings
from sentence_transformers import CrossEncoder

class HybridRetrievalService:
    _reranker = None

    @classmethod
    def get_reranker(cls):
        if cls._reranker is None and settings.ENABLE_RERANKER:
            cls._reranker = CrossEncoder(settings.RERANKER_MODEL)
        return cls._reranker

    @staticmethod
    def rrf(dense_results, sparse_results, k=60):
        # rrf_score = 1 / (k + rank)
        scores = {}
        
        # Process dense
        if dense_results and dense_results["ids"] and dense_results["ids"][0]:
            ids = dense_results["ids"][0]
            metadatas = dense_results["metadatas"][0]
            documents = dense_results["documents"][0]
            for rank, (doc_id, meta, doc) in enumerate(zip(ids, metadatas, documents)):
                if doc_id not in scores:
                    scores[doc_id] = {"score": 0, "metadata": meta, "text": doc}
                scores[doc_id]["score"] += 1.0 / (k + rank + 1)
                
        # Process sparse
        for rank, (score, doc) in enumerate(sparse_results):
            doc_id = doc["chunk_id"]
            if doc_id not in scores:
                scores[doc_id] = {"score": 0, "metadata": doc["metadata"], "text": doc["text"]}
            scores[doc_id]["score"] += 1.0 / (k + rank + 1)
            
        return sorted(scores.values(), key=lambda x: x["score"], reverse=True)

    @classmethod
    def retrieve(cls, project_id: int, query: str, document_types: list[str] = None):
        # 1. Dense retrieval
        query_embedding = EmbeddingService.encode(query)
        dense_res = ChromaService.search(project_id, query_embedding, document_types, settings.DENSE_TOP_K)
        
        # 2. Sparse retrieval
        sparse_res = BM25Service.search(project_id, query, document_types, settings.SPARSE_TOP_K)
        
        # 3. RRF
        fused = cls.rrf(dense_res, sparse_res, k=settings.RRF_K)[:settings.FUSION_TOP_K]
        
        # 4. Rerank
        if settings.ENABLE_RERANKER:
            try:
                reranker = cls.get_reranker()
                if reranker and fused:
                    pairs = [[query, item["text"]] for item in fused]
                    rerank_scores = reranker.predict(pairs)
                    for i, item in enumerate(fused):
                        item["rerank_score"] = float(rerank_scores[i])
                    fused.sort(key=lambda x: x["rerank_score"], reverse=True)
            except Exception as e:
                print(f"Reranker failed: {e}. Falling back to RRF.")
                
        return fused[:settings.RERANK_TOP_K]
