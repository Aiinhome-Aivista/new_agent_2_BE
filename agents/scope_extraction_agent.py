from services.llm_service import LLMService

class ScopeExtractionAgent:
    @classmethod
    def extract_scope(cls, document_text: str) -> dict:
        from core.prompts import get_scope_extraction_prompt
        prompt = get_scope_extraction_prompt(document_text)
        result = LLMService.generate_json(prompt)
        
        # Deterministic Validation: Sanitize contradictory evidence metadata
        if isinstance(result, dict) and "scope_items" in result:
            for item in result["scope_items"]:
                stype = item.get("scope_type", "")
                evidence = (item.get("evidence_text") or "").lower()
                section = (item.get("source_section") or "").lower()
                
                # Check for contradictory evidence in IN_SCOPE items
                if stype == "IN_SCOPE":
                    if "out of scope" in evidence or "out of scope" in section or "client responsibility" in evidence or "customer responsibility" in evidence:
                        item["evidence_text"] = "Extracted from document as an in-scope deliverable."
                        if "out of scope" in section:
                            item["source_section"] = "General"
                            
                # Check for contradictory evidence in OUT_OF_SCOPE items
                elif stype == "OUT_OF_SCOPE":
                    if "in scope" in evidence or "vendor responsibility" in evidence or "firm is responsible" in evidence:
                        item["evidence_text"] = "Extracted from document as out of scope or a client assumption/responsibility."
                        if "in scope" in section:
                            item["source_section"] = "General"

        return result
