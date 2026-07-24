import json
from services.hybrid_retrieval_service import HybridRetrievalService
from services.llm_service import LLMService
from services.scope_deterministic_classifier import ScopeDeterministicClassifier
import difflib
class ScopeClassifier:
    """
    Uses Hybrid Retrieval to find supporting evidence for a candidate item,
    and then uses a small LLM prompt to classify it.
    """
    
    @classmethod
    def classify_candidate(cls, project_id: int, candidate: dict) -> dict:
        # Retrieve supporting evidence for the candidate using hybrid search
        search_query = candidate["name"] + " " + candidate["description"]
        # Only search EL and IFA to determine scope explicitly
        retrieved_chunks = HybridRetrievalService.retrieve(project_id, search_query, document_types=["EL", "IFA"])
        
        # Take the top 3 most relevant chunks to keep the context window small, deduplicating them first
        evidence_texts = []
        seen_texts = []
        for chunk in retrieved_chunks:
            text = chunk['text']
            is_duplicate = False
            for seen in seen_texts:
                if difflib.SequenceMatcher(None, text.lower(), seen.lower()).ratio() > 0.85:
                    is_duplicate = True
                    break
            if not is_duplicate:
                seen_texts.append(text)
                evidence_texts.append(f"Evidence {len(evidence_texts)+1}:\n{text}")
            
            if len(evidence_texts) >= 3:
                break
            
        combined_evidence = "\n\n".join(evidence_texts)
        if not combined_evidence:
            combined_evidence = "No specific supporting evidence found in the contract."

        # Deterministic Classification Step
        deterministic_result = ScopeDeterministicClassifier.classify(candidate, combined_evidence)
        if deterministic_result.get("confidence", 0.0) >= 0.95:
            print(f"[Deterministic] '{candidate['name']}' -> {deterministic_result['scope_type']} (Reason: {deterministic_result['evidence_text']})")
            candidate["scope_type"] = deterministic_result["scope_type"]
            candidate["confidence"] = deterministic_result["confidence"]
            candidate["evidence_text"] = deterministic_result["evidence_text"]
            return candidate
            
        print(f"[LLM Fallback] '{candidate['name']}' was ambiguous. Calling LLM...")
            
        prompt = f"""
You are an expert contract analyst. Your task is to classify ONE specific candidate scope item based ONLY on the provided supporting evidence retrieved from the contract.

Candidate Item:
- Name: {candidate["name"]}
- Raw Description: {candidate["description"]}
- Found in Section: {candidate["section"]}

Supporting Evidence from Contract:
{combined_evidence}

Task:
Classify this candidate as "IN_SCOPE", "OUT_OF_SCOPE", or "UNCERTAIN".
- If the evidence clearly states the vendor provides it, choose IN_SCOPE.
- If the evidence states it's excluded, or it's the client's responsibility, or it's an assumption, choose OUT_OF_SCOPE.
- If there is not enough evidence to be sure, choose UNCERTAIN.

Output your result strictly as JSON:
{{
  "scope_type": "IN_SCOPE", 
  "confidence": 0.9, 
  "evidence_text": "<Brief 1-sentence reasoning quoting the evidence>"
}}
"""
        
        try:
            result = LLMService.generate_json(prompt)
            # Merge classification results into the candidate dictionary
            candidate["scope_type"] = result.get("scope_type", "UNCERTAIN")
            candidate["confidence"] = result.get("confidence", 0.5)
            candidate["evidence_text"] = result.get("evidence_text", "No reasoning provided.")
        except Exception as e:
            print(f"Failed to classify candidate {candidate['name']}: {e}")
            if deterministic_result.get("scope_type") != "UNCERTAIN":
                print(f"[Failsafe] Falling back to deterministic low-confidence result for '{candidate['name']}'.")
                candidate["scope_type"] = deterministic_result["scope_type"]
                candidate["confidence"] = deterministic_result["confidence"]
                candidate["evidence_text"] = deterministic_result["evidence_text"]
            else:
                candidate["scope_type"] = "UNCERTAIN"
                candidate["confidence"] = 0.0
                candidate["evidence_text"] = "LLM classification failed."
            
        return candidate
