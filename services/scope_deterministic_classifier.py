import json
import os

class ScopeDeterministicClassifier:
    _rules = None

    @classmethod
    def _load_rules(cls):
        if cls._rules is None:
            rules_path = os.path.join(os.path.dirname(__file__), "..", "config", "deterministic_rules.json")
            with open(rules_path, "r", encoding="utf-8") as f:
                cls._rules = json.load(f)
        return cls._rules

    @classmethod
    def classify(cls, candidate: dict, combined_evidence: str) -> dict:
        """
        Evaluate candidate and evidence against deterministic rules.
        Returns a classification dict if highly confident, otherwise returns a low confidence result so LLM can fallback.
        """
        rules = cls._load_rules()
        
        # We should only check for deterministic keywords inside the candidate's actual sentence.
        # If we check the entire `combined_evidence` (which contains 3 retrieved paragraphs), 
        # a single mention of "excluded" anywhere in those 3 paragraphs will cause a false positive.
        search_text = candidate.get("description", "").lower()
        # Priority 1: Explicit Exclusions
        for kw in rules.get("explicit_exclusions", []):
            if kw.lower() in search_text:
                return {
                    "scope_type": "OUT_OF_SCOPE",
                    "confidence": 0.98,
                    "evidence_text": f"Explicitly excluded by phrase: '{kw}'"
                }
                
        # Priority 2: Customer Responsibilities
        for kw in rules.get("customer_responsibilities", []):
            if kw.lower() in search_text:
                return {
                    "scope_type": "OUT_OF_SCOPE",
                    "confidence": 0.98,
                    "evidence_text": f"Identified as customer responsibility by phrase: '{kw}'"
                }

        # Priority 3: Vendor Responsibilities
        for kw in rules.get("vendor_responsibilities", []):
            if kw.lower() in search_text:
                return {
                    "scope_type": "IN_SCOPE",
                    "confidence": 0.98,
                    "evidence_text": f"Identified as vendor responsibility by phrase: '{kw}'"
                }

        # Priority 4: Section-Based Rules (Fallback)
        section = candidate.get("section", "")
        if section:
            for sec in rules.get("out_scope_sections", []):
                if sec.lower() == section.lower():
                    return {
                        "scope_type": "OUT_OF_SCOPE",
                        "confidence": 0.99,
                        "evidence_text": f"Categorized based on document section: '{section}'"
                    }
                    
            for sec in rules.get("in_scope_sections", []):
                if sec.lower() == section.lower():
                    return {
                        "scope_type": "IN_SCOPE",
                        "confidence": 0.99,
                        "evidence_text": f"Categorized based on document section: '{section}'"
                    }

        # Priority 5: Ambiguous / No Strong Signals
        return {
            "scope_type": "UNCERTAIN",
            "confidence": 0.0,
            "evidence_text": "No deterministic rules matched strongly enough."
        }
