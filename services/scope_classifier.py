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
    def classify_candidates_batch(cls, project_id: int, candidates: list[dict]) -> list[dict]:
        print(f"[LLM] Preparing {len(candidates)} candidates for classification...")
        
        llm_batch = []
        for candidate in candidates:
            # 1. Retrieve supporting evidence
            search_query = candidate["name"] + " " + candidate.get("description", "")
            retrieved_chunks = HybridRetrievalService.retrieve(project_id, search_query, document_types=["EL", "IFA"])
            
            # Filter and Rank chunks
            filtered_chunks = []
            bad_sections = {"Out of Scope", "Assumptions", "Client Responsibilities", "Customer Responsibilities"}
            for chunk in retrieved_chunks:
                meta = chunk.get("metadata", {}) or {}
                chunk_section = meta.get("section", "General")
                cand_section = candidate.get("section", "General")
                if chunk_section in bad_sections and cand_section not in bad_sections:
                    continue
                if cand_section in bad_sections and chunk_section not in bad_sections:
                    continue
                filtered_chunks.append(chunk)
                
            candidate_name_lower = candidate["name"].lower()
            candidate_idx = candidate.get("chunk_index", 0)
            
            strictly_filtered_chunks = []
            for chunk in filtered_chunks:
                chunk_section = chunk.get("metadata", {}).get("section", "General")
                chunk_idx = chunk.get("metadata", {}).get("chunk_index", 0)
                text = chunk.get("text", "").lower()
                
                if candidate_name_lower in text:
                    strictly_filtered_chunks.append(chunk)
                    continue
                    
                distance = abs(chunk_idx - candidate_idx) if chunk_idx is not None and candidate_idx is not None else 999
                if chunk_section == candidate.get("section", "General") and distance <= 3:
                    strictly_filtered_chunks.append(chunk)
                    continue
                    
            def rank_score(chunk):
                text = chunk.get("text", "").lower()
                chunk_idx = chunk.get("metadata", {}).get("chunk_index", 0)
                score = 0
                if candidate_name_lower in text:
                    score += 1000
                distance = abs(chunk_idx - candidate_idx) if chunk_idx is not None and candidate_idx is not None else 999
                score -= distance
                return score
                
            strictly_filtered_chunks.sort(key=rank_score, reverse=True)
            
            evidence_texts = []
            seen_texts = []
            for chunk in strictly_filtered_chunks:
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
            
            # 2. Deterministic Classification
            deterministic_result = ScopeDeterministicClassifier.classify(candidate, combined_evidence)
            if deterministic_result.get("confidence", 0.0) >= 0.95:
                print(f"[Deterministic] '{candidate['name']}' -> {deterministic_result['scope_type']} (Reason: {deterministic_result['evidence_text']})")
                candidate["scope_type"] = deterministic_result["scope_type"]
                candidate["confidence"] = deterministic_result["confidence"]
                candidate["evidence_text"] = deterministic_result["evidence_text"]
                continue
                
            llm_batch.append({
                "candidate": candidate,
                "evidence": combined_evidence,
                "deterministic_result": deterministic_result
            })

        # Step 2: Process non-deterministic items in batches of 10
        BATCH_SIZE = 10
        for i in range(0, len(llm_batch), BATCH_SIZE):
            batch_slice = llm_batch[i:i + BATCH_SIZE]
            print(f"[LLM] Classifying batch of {len(batch_slice)} candidates (Items {i+1} to {i+len(batch_slice)} of {len(llm_batch)})...")
            
            items_for_prompt = []
            for idx, item in enumerate(batch_slice):
                items_for_prompt.append({
                    "id": str(idx),
                    "name": item["candidate"]["name"],
                    "description": item["candidate"].get("description", ""),
                    "section": item["candidate"].get("section", ""),
                    "evidence": item["evidence"]
                })
            
            from core.prompts import get_batch_scope_classifier_prompt
            prompt = get_batch_scope_classifier_prompt(items_for_prompt)
            try:
                batch_results = LLMService.generate_json(prompt)
                if not isinstance(batch_results, list):
                    batch_results = [batch_results]
                    
                result_map = {{str(res.get("id", "")) : res for res in batch_results}}
                for idx, item in enumerate(batch_slice):
                    candidate_ref = item["candidate"]
                    res = result_map.get(str(idx), {})
                    candidate_ref["scope_type"] = res.get("scope_type", "UNCERTAIN")
                    candidate_ref["confidence"] = res.get("confidence", 0.5)
                    candidate_ref["evidence_text"] = res.get("evidence_text", "No reasoning provided.")
            except Exception as e:
                print(f"Failed to classify batch {i}: {e}")
                for item in batch_slice:
                    candidate_ref = item["candidate"]
                    det_res = item["deterministic_result"]
                    if det_res.get("scope_type") != "UNCERTAIN":
                        print(f"[Failsafe] Falling back to deterministic low-confidence result for '{candidate_ref['name']}'.")
                        candidate_ref["scope_type"] = det_res["scope_type"]
                        candidate_ref["confidence"] = det_res["confidence"]
                        candidate_ref["evidence_text"] = det_res["evidence_text"]
                    else:
                        candidate_ref["scope_type"] = "UNCERTAIN"
                        candidate_ref["confidence"] = 0.0
                        candidate_ref["evidence_text"] = "LLM classification failed due to batch error."
        
        return candidates
        
    @classmethod
    def classify_candidate(cls, project_id: int, candidate: dict) -> dict:
        # Retrieve supporting evidence for the candidate using hybrid search
        search_query = candidate["name"] + " " + candidate["description"]
        # Only search EL and IFA to determine scope explicitly
        retrieved_chunks = HybridRetrievalService.retrieve(project_id, search_query, document_types=["EL", "IFA"])
        
        # Issue 4 & 5: Filter and Rank chunks
        filtered_chunks = []
        bad_sections = {"Out of Scope", "Assumptions", "Client Responsibilities", "Customer Responsibilities"}
        for chunk in retrieved_chunks:
            meta = chunk.get("metadata", {}) or {}
            chunk_section = meta.get("section", "General")
            
            cand_section = candidate.get("section", "General")
            if chunk_section in bad_sections and cand_section not in bad_sections:
                continue
            if cand_section in bad_sections and chunk_section not in bad_sections:
                continue
            filtered_chunks.append(chunk)
            
        candidate_name_lower = candidate["name"].lower()
        candidate_idx = candidate.get("chunk_index", 0)
        
        # Issue 3: Filter chunks to ONLY same sentence/paragraph (idx distance <= 1), 
        # same section, or nearest neighbor.
        strictly_filtered_chunks = []
        for chunk in filtered_chunks:
            chunk_section = chunk.get("metadata", {}).get("section", "General")
            chunk_idx = chunk.get("metadata", {}).get("chunk_index", 0)
            text = chunk.get("text", "").lower()
            
            # Keep if scope item is in the text (same sentence/paragraph)
            if candidate_name_lower in text:
                strictly_filtered_chunks.append(chunk)
                continue
                
            # Keep if same section and it's near (nearest neighbor / distance <= 3)
            distance = abs(chunk_idx - candidate_idx) if chunk_idx is not None and candidate_idx is not None else 999
            if chunk_section == candidate.get("section", "General") and distance <= 3:
                strictly_filtered_chunks.append(chunk)
                continue
                
        def rank_score(chunk):
            text = chunk.get("text", "").lower()
            chunk_idx = chunk.get("metadata", {}).get("chunk_index", 0)
            
            score = 0
            if candidate_name_lower in text:
                score += 1000
            
            distance = abs(chunk_idx - candidate_idx) if chunk_idx is not None and candidate_idx is not None else 999
            score -= distance
            return score
            
        strictly_filtered_chunks.sort(key=rank_score, reverse=True)
        
        # Take the top 3 most relevant chunks to keep the context window small, deduplicating them first
        evidence_texts = []
        seen_texts = []
        for chunk in strictly_filtered_chunks:
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
            
        from core.prompts import get_single_scope_classifier_prompt
        prompt = get_single_scope_classifier_prompt(candidate, combined_evidence)
        
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
