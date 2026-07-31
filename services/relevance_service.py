import numpy as np
from services.embedding_service import EmbeddingService
from services.llm_service import LLMService
from core.constants import REFERENCE_PROFILES



class RelevanceService:
    """
    Scores document relevance using vector embedding cosine similarity.
    
    Instead of sending text to an LLM (expensive, slow, inconsistent),
    this service embeds both the document text and a reference profile
    for the target document type, then measures cosine similarity.
    
    Benefits:
    - Zero LLM tokens consumed
    - Sub-second response time (< 1 second)
    - Deterministic and reproducible scores
    - Reuses the same sentence-transformers model already loaded for RAG
    """

    # Cache for embedded reference profiles (computed once, reused forever)
    _profile_cache: dict[str, list[float]] = {}

    @classmethod
    def _get_profile_embedding(cls, doc_type: str, profile_text: str) -> list[float]:
        """Get or compute the embedding for a reference profile (cached)."""
        cache_key = f"{doc_type}:{hash(profile_text)}"
        if cache_key not in cls._profile_cache:
            cls._profile_cache[cache_key] = EmbeddingService.encode(profile_text)
        return cls._profile_cache[cache_key]

    @classmethod
    def _cosine_similarity(cls, vec_a: list[float], vec_b: list[float]) -> float:
        """Compute cosine similarity between two vectors. Returns 0.0 to 1.0."""
        a = np.array(vec_a)
        b = np.array(vec_b)
        dot_product = np.dot(a, b)
        norm_a = np.linalg.norm(a)
        norm_b = np.linalg.norm(b)
        if norm_a == 0 or norm_b == 0:
            return 0.0
        return float(dot_product / (norm_a * norm_b))

    @classmethod
    def _get_reference_text(cls, document_type: str, db=None) -> str:
        """
        Get the reference profile text for a document type.
        
        - For standard types (EL, IFA, MOM, etc.) → uses built-in REFERENCE_PROFILES
        - For custom types → fetches the user-provided description from the database
        """
        # Check built-in profiles first
        if document_type in REFERENCE_PROFILES:
            return REFERENCE_PROFILES[document_type]

        # For custom types, fetch description from DB
        if db is not None:
            try:
                cursor = db.cursor(dictionary=True)
                cursor.execute(
                    "SELECT description FROM document_types WHERE name = %s LIMIT 1",
                    (document_type,)
                )
                row = cursor.fetchone()
                cursor.close()
                if row and row.get("description", "").strip():
                    return row["description"]
            except Exception:
                pass

        # Fallback: use the type name itself as a minimal reference
        return f"A professional document of type {document_type}."

    @classmethod
    def expand_description(cls, type_name: str, short_description: str) -> str:
        """
        Uses the LLM to expand a short user-provided description into a rich
        ~200-word reference profile suitable for embedding-based comparison.
        
        This is called ONCE when a custom document type is created.
        The expanded text is stored in the DB and reused for all future uploads.
        
        Args:
            type_name: The document type name (e.g., "RESOURCES")
            short_description: The user's short description (e.g., "all resources")
            
        Returns:
            A rich ~200-word reference profile text
        """
        from core.prompts import get_relevance_expansion_prompt
        prompt = get_relevance_expansion_prompt(type_name, short_description)

        try:
            expanded = LLMService.generate(prompt)
            # Clean up any leading/trailing whitespace or quotes
            expanded = expanded.strip().strip('"').strip("'")
            if len(expanded) > 50:  # Sanity check: LLM returned something meaningful
                return expanded
        except Exception as e:
            print(f"LLM description expansion failed: {e}")

        # Fallback: return the original short description if LLM fails
        return short_description

    @classmethod
    def score_relevance(cls, document_text: str, document_type: str, db=None) -> dict:
        """
        Score how relevant a document's content is to the declared document type.
        
        Strategy:
        - Step 1: Fast embedding pre-filter to catch completely irrelevant files (< 10%)
        - Step 2: LLM always makes the final decision for everything else
        
        This ensures the same document ALWAYS gets the same accurate score.
        """
        # 1. Get the reference profile text
        reference_text = cls._get_reference_text(document_type, db)

        # 2. Embed both texts using the same model used for RAG
        doc_embedding = EmbeddingService.encode(document_text)
        ref_embedding = cls._get_profile_embedding(document_type, reference_text)

        # 3. Compute cosine similarity
        raw_similarity = cls._cosine_similarity(doc_embedding, ref_embedding)

        # 4. Scale to 0-100 percentage
        embedding_score = int(min(100, max(0, round(raw_similarity * 125))))
        type_label = document_type.replace("_", " ").title()

        # 5. Fast rejection: only for completely irrelevant content (< 10%)
        #    e.g. uploading a cooking recipe as an "Engagement Letter"
        if embedding_score < 10:
            return {
                "score": embedding_score,
                "reasoning": f"Document content is completely unrelated to {type_label}. Obvious mismatch, rejected."
            }

        # 6. LLM always makes the final decision for consistent, accurate scoring
        # Send a smaller chunk to the LLM to save tokens
        small_sample = document_text[:3000] 
        
        from core.prompts import get_relevance_scoring_prompt
        prompt = get_relevance_scoring_prompt(document_type, type_label, embedding_score, small_sample)
        
        try:
            res_json = LLMService.generate_json(prompt)
            final_score = int(res_json.get("score", embedding_score))
            reasoning = res_json.get("reasoning", f"AI verified with score {final_score}.")
            return {
                "score": final_score,
                "reasoning": reasoning
            }
        except Exception as e:
            # Fallback to embedding score if LLM fails
            print(f"LLM verification failed: {e}")
            return {
                "score": embedding_score,
                "reasoning": f"Document shows {embedding_score}% similarity to {type_label}. (AI verification unavailable, using embedding score)"
            }

