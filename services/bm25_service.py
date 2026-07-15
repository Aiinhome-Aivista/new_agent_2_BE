from rank_bm25 import BM25Okapi
import os
import json
from core.config import settings

class BM25Service:
    _indexes = {}
    _corpora = {}
    
    @staticmethod
    def get_index_path(project_id: int):
        path = os.path.join(settings.CHROMA_PATH, "bm25")
        os.makedirs(path, exist_ok=True)
        return os.path.join(path, f"proj_{project_id}.json")

    @classmethod
    def load_index(cls, project_id: int):
        if project_id in cls._indexes:
            return
            
        path = cls.get_index_path(project_id)
        if os.path.exists(path):
            with open(path, 'r', encoding='utf-8') as f:
                data = json.load(f)
                cls._corpora[project_id] = data["corpus"]
                tokenized_corpus = [doc["text"].lower().split() for doc in data["corpus"]]
                cls._indexes[project_id] = BM25Okapi(tokenized_corpus)
        else:
            cls._corpora[project_id] = []
            cls._indexes[project_id] = None

    @classmethod
    def save_index(cls, project_id: int):
        path = cls.get_index_path(project_id)
        with open(path, 'w', encoding='utf-8') as f:
            json.dump({"corpus": cls._corpora.get(project_id, [])}, f)

    @classmethod
    def add_chunks(cls, project_id: int, document_id: int, document_name: str, document_type: str, chunks: list[dict]):
        cls.load_index(project_id)
        
        for chunk in chunks:
            cls._corpora[project_id].append({
                "chunk_id": f"proj_{project_id}_doc_{document_id}_chunk_{chunk['chunk_index']}",
                "text": chunk["text"],
                "metadata": {
                    "project_id": project_id,
                    "document_id": document_id,
                    "document_name": document_name,
                    "document_type": document_type,
                    "page_number": chunk.get("page_number", -1) or -1,
                    "chunk_index": chunk["chunk_index"]
                }
            })
            
        tokenized_corpus = [doc["text"].lower().split() for doc in cls._corpora[project_id]]
        cls._indexes[project_id] = BM25Okapi(tokenized_corpus)
        cls.save_index(project_id)

    @classmethod
    def search(cls, project_id: int, query: str, document_types: list[str] = None, top_k: int = 10):
        cls.load_index(project_id)
        index = cls._indexes.get(project_id)
        corpus = cls._corpora.get(project_id)
        
        if not index or not corpus:
            return []
            
        tokenized_query = query.lower().split()
        doc_scores = index.get_scores(tokenized_query)
        
        results = []
        for idx, score in enumerate(doc_scores):
            if score > 0:
                doc = corpus[idx]
                if not document_types or doc["metadata"]["document_type"] in document_types:
                    results.append((score, doc))
                    
        results.sort(key=lambda x: x[0], reverse=True)
        return results[:top_k]
