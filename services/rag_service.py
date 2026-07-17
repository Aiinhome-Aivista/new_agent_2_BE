from services.embedding_service import EmbeddingService
from services.chroma_service import ChromaService
from services.bm25_service import BM25Service
from services.hybrid_retrieval_service import HybridRetrievalService

class RAGService:
    @staticmethod
    def index_document(project_id: int, document_id: int, document_name: str, document_type: str, chunks: list[dict]):
        if not chunks:
            return

        # 1. Embed text
        texts = [c["text"] for c in chunks]
        embeddings = EmbeddingService.encode_batch(texts)
        
        # 2. Add to Chroma
        ChromaService.add_chunks(project_id, document_id, document_name, document_type, chunks, embeddings)
        
        # 3. Add to BM25
        BM25Service.add_chunks(project_id, document_id, document_name, document_type, chunks)

    @staticmethod
    def retrieve_evidence(project_id: int, query: str, document_types: list[str] = None):
        return HybridRetrievalService.retrieve(project_id, query, document_types)

    @staticmethod
    def delete_document(project_id: int, document_id: int):
        ChromaService.delete_document_chunks(document_id)
        BM25Service.delete_document_chunks(project_id, document_id)
