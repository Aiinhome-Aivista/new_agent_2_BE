import json
import logging
from services.llm_service import LLMService

logger = logging.getLogger(__name__)

class ScopeClassifier:
    """
    Phase 5: LLM Validation & Confidence Scoring
    Validates the entity extracted by specialized extractors, resolves ambiguity, and assigns confidence.
    """
    
    @classmethod
    def validate_entity(cls, entity: dict) -> dict:
        entity_type = entity.get("type", "UNKNOWN")
        raw_text = entity.get("raw_text", "")
        
        prompt = f"""
You are an expert contract analyst. Your task is to validate and score a single extracted entity from a contract.

Entity Type: {entity_type}
Extracted Text: {raw_text}
Found in Section: {entity.get("source_section", "Unknown")}

Task:
1. Validate if this text truly belongs to the {entity_type} category.
2. Provide a clean, concise name for the entity (max 10 words).
3. Assign a confidence score (0.0 to 1.0) based on how clearly the text represents the entity type.
4. Extract a 1-sentence "evidence" quote strictly from the Extracted Text itself. DO NOT use the section header or page header as evidence.

Output your result strictly as JSON:
{{
  "is_valid": true,
  "cleaned_name": "<concise name>",
  "confidence": 0.9, 
  "evidence_text": "<Brief 1-sentence quote exactly from Extracted Text>"
}}
"""
        
        try:
            result = LLMService.generate_json(prompt)
            entity["is_valid"] = result.get("is_valid", True)
            if not entity.get("name"):
                entity["name"] = result.get("cleaned_name", raw_text[:50])
            if not entity.get("description"):
                entity["description"] = raw_text
            
            # Blend existing confidence with LLM confidence
            existing_conf = entity.get("confidence", 0.5)
            llm_conf = result.get("confidence", 0.5)
            final_conf = (existing_conf + llm_conf) / 2.0
            entity["confidence"] = final_conf
            
            # Apply explicit confidence threshold
            if final_conf < 0.70:
                entity["status"] = "REVIEW_REQUIRED"
            
            entity["evidence_text"] = result.get("evidence_text", "Extracted via structural rules.")
        except Exception as e:
            logger.error(f"Failed to validate entity {raw_text[:30]}: {e}", exc_info=True)
            entity["is_valid"] = True
            entity["name"] = entity.get("name", raw_text[:50])
            entity["description"] = entity.get("description", raw_text)
            entity["evidence_text"] = "LLM validation failed, fallback to structural extraction."
            if entity.get("confidence", 0.5) < 0.70:
                entity["status"] = "REVIEW_REQUIRED"
            
        return entity
