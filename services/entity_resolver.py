from typing import List, Dict, Any
# pyrefly: ignore [missing-import]
from rapidfuzz import fuzz

class EntityResolver:
    """
    Phase 4-5: Resolves overlaps, merges duplicates, and maps relationships
    between extracted entities before sending them to the database.
    """
    
    @staticmethod
    def resolve_and_merge(entities: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        if not entities:
            return []
            
        from services.embedding_service import EmbeddingService
        import math
        
        def cosine_similarity(v1, v2):
            dot_product = sum(a * b for a, b in zip(v1, v2))
            magnitude1 = math.sqrt(sum(a * a for a in v1))
            magnitude2 = math.sqrt(sum(b * b for b in v2))
            if magnitude1 == 0 or magnitude2 == 0:
                return 0.0
            return dot_product / (magnitude1 * magnitude2)
            
        resolved = []
        
        # Batch encode all entity texts for semantic deduplication
        texts = [e.get("raw_text", "")[:500] for e in entities]
        embeddings = EmbeddingService.encode_batch(texts)
        
        for i, entity in enumerate(entities):
            is_duplicate = False
            for j, existing in enumerate(resolved):
                if existing.get("type") == entity.get("type"):
                    # Use cosine similarity
                    sim = cosine_similarity(existing["_embedding"], embeddings[i])
                    if sim >= 0.88:
                        is_duplicate = True
                        # Merge confidence (take the higher one)
                        existing["confidence"] = max(existing.get("confidence", 0), entity.get("confidence", 0))
                        # Merge text if it's shorter
                        if len(entity.get("raw_text", "")) > len(existing.get("raw_text", "")):
                            existing["raw_text"] = entity.get("raw_text", "")
                        break
                        
            if not is_duplicate:
                entity["_embedding"] = embeddings[i]
                resolved.append(entity)
                
        # Clean up temporary embeddings
        for r in resolved:
            if "_embedding" in r:
                del r["_embedding"]
                
        return EntityResolver.map_relationships(resolved)

    @staticmethod
    def map_relationships(entities: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Maps Entity Relationships. E.g. If a DELIVERABLE mentions a MILESTONE's deadline
        or name, we can link them.
        """
        # Separate entities by type
        milestones = [e for e in entities if e.get("type") == "MILESTONE"]
        deliverables = [e for e in entities if e.get("type") == "DELIVERABLE"]
        
        for deliverable in deliverables:
            deliv_deadline = deliverable.get("deadline")
            
            # 1. Match by exact deadline
            if deliv_deadline:
                for ms in milestones:
                    if ms.get("deadline") == deliv_deadline:
                        deliverable["related_milestone"] = ms.get("milestone_name")
                        break
            
            # 2. Match by textual reference
            if not deliverable.get("related_milestone"):
                deliv_text = deliverable.get("raw_text", "").lower()
                for ms in milestones:
                    ms_name = ms.get("milestone_name", "").lower()
                    if ms_name and ms_name in deliv_text and len(ms_name) > 5:
                        deliverable["related_milestone"] = ms.get("milestone_name")
                        break
                        
        return entities
