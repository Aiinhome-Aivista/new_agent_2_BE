import json
from services.llm_service import LLMService
import difflib

class ScopeClassifier:
    """
    Uses Hybrid Retrieval to find supporting evidence for a candidate item,
    and then uses a small LLM prompt to classify it.
    """
    
    BAD_SECTIONS = {"Out of Scope", "Assumptions", "Client Responsibilities", "Customer Responsibilities"}

    @classmethod
    def _get_section_fallback_evidence(cls, candidate: dict) -> str:
        cand_section = candidate.get("section", "General")
        raw_text = candidate.get("raw_text") or candidate.get("description") or candidate.get("name")
        return f"Evidence from Document Section [{cand_section}]:\nItem explicitly listed in contract under section '{cand_section}': \"{raw_text}\""

    @classmethod
    def _apply_section_failsafe(cls, candidate: dict, original_scope_type: str = "UNCERTAIN", original_conf: float = 0.0, original_evidence: str = ""):
        cand_section = candidate.get("section", "General")
        if cand_section in {"Out of Scope", "Client Responsibilities", "Customer Responsibilities"}:
            candidate["scope_type"] = "OUT_OF_SCOPE"
            candidate["confidence"] = 0.95
            candidate["evidence_text"] = f"Item explicitly specified under '{cand_section}' section of the contract."
        elif cand_section == "Assumptions":
            candidate["scope_type"] = "ASSUMPTION"
            candidate["confidence"] = 0.95
            candidate["evidence_text"] = "Item explicitly specified under 'Assumptions' section of the contract."
        elif cand_section in {"Scope of Work", "Deliverables", "Responsibilities", "Milestones"}:
            candidate["scope_type"] = "IN_SCOPE"
            candidate["confidence"] = 0.90
            candidate["evidence_text"] = f"Item specified under '{cand_section}' section of the contract."
        else:
            candidate["scope_type"] = original_scope_type
            candidate["confidence"] = original_conf
            candidate["evidence_text"] = original_evidence or "No specific supporting evidence found in the contract."

    @classmethod
    def classify_candidates_batch(cls, project_id: int, candidates: list[dict]) -> list[dict]:
        from services.hybrid_retrieval_service import HybridRetrievalService
        print(f"[LLM] Preparing {len(candidates)} candidates for classification...")
        
        llm_batch = []
        for candidate in candidates:
            # 1. Retrieve supporting evidence
            search_query = candidate["name"] + " " + candidate.get("description", "")
            retrieved_chunks = HybridRetrievalService.retrieve(project_id, search_query, document_types=["EL", "IFA"])
            
            # Filter and Rank chunks
            filtered_chunks = []
            for chunk in retrieved_chunks:
                meta = chunk.get("metadata", {}) or {}
                chunk_section = meta.get("section", "General")
                cand_section = candidate.get("section", "General")
                if chunk_section in cls.BAD_SECTIONS and cand_section not in cls.BAD_SECTIONS:
                    continue
                if cand_section in cls.BAD_SECTIONS and chunk_section not in cls.BAD_SECTIONS:
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
                cand_sec = candidate.get("section", "General")
                if cand_sec in cls.BAD_SECTIONS or cand_sec != "General":
                    combined_evidence = cls._get_section_fallback_evidence(candidate)
                else:
                    combined_evidence = "No specific supporting evidence found in the contract."
            
            deterministic_result = {}
                
            llm_batch.append({
                "candidate": candidate,
                "evidence": combined_evidence,
                "deterministic_result": deterministic_result
            })

        # Step 2: Process candidates in batches of 10
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
                    
                result_map = {str(res.get("id", "")) : res for res in batch_results}
                for idx, item in enumerate(batch_slice):
                    candidate_ref = item["candidate"]
                    res = result_map.get(str(idx), {})
                    scope_type = res.get("scope_type", "UNCERTAIN")
                    confidence = res.get("confidence", 0.5)
                    evidence_text = res.get("evidence_text", "No reasoning provided.")
                    
                    # If LLM gave UNCERTAIN on an item from Out of Scope or Assumptions, use robust section failsafe
                    cand_sec = candidate_ref.get("section", "General")
                    if scope_type == "UNCERTAIN" and (cand_sec in cls.BAD_SECTIONS or cand_sec != "General"):
                        cls._apply_section_failsafe(candidate_ref, scope_type, confidence, evidence_text)
                    else:
                        candidate_ref["scope_type"] = scope_type
                        candidate_ref["confidence"] = confidence
                        candidate_ref["evidence_text"] = evidence_text
            except Exception as e:
                print(f"Failed to classify batch {i}: {e}")
                for item in batch_slice:
                    candidate_ref = item["candidate"]
                    cls._apply_section_failsafe(candidate_ref, "UNCERTAIN", 0.0, "LLM classification failed due to batch error.")
        
        return candidates
        
    @classmethod
    def classify_candidate(cls, project_id: int, candidate: dict) -> dict:
        from services.hybrid_retrieval_service import HybridRetrievalService
        search_query = candidate["name"] + " " + candidate["description"]
        retrieved_chunks = HybridRetrievalService.retrieve(project_id, search_query, document_types=["EL", "IFA"])
        
        filtered_chunks = []
        for chunk in retrieved_chunks:
            meta = chunk.get("metadata", {}) or {}
            chunk_section = meta.get("section", "General")
            
            cand_section = candidate.get("section", "General")
            if chunk_section in cls.BAD_SECTIONS and cand_section not in cls.BAD_SECTIONS:
                continue
            if cand_section in cls.BAD_SECTIONS and chunk_section not in cls.BAD_SECTIONS:
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
            cand_sec = candidate.get("section", "General")
            if cand_sec in cls.BAD_SECTIONS or cand_sec != "General":
                combined_evidence = cls._get_section_fallback_evidence(candidate)
            else:
                combined_evidence = "No specific supporting evidence found in the contract."

        print(f"[LLM] Classifying '{candidate['name']}'...")
            
        from core.prompts import get_single_scope_classifier_prompt
        prompt = get_single_scope_classifier_prompt(candidate, combined_evidence)
        
        try:
            result = LLMService.generate_json(prompt)
            scope_type = result.get("scope_type", "UNCERTAIN")
            confidence = result.get("confidence", 0.5)
            evidence_text = result.get("evidence_text", "No reasoning provided.")
            
            cand_sec = candidate.get("section", "General")
            if scope_type == "UNCERTAIN" and (cand_sec in cls.BAD_SECTIONS or cand_sec != "General"):
                cls._apply_section_failsafe(candidate, scope_type, confidence, evidence_text)
            else:
                candidate["scope_type"] = scope_type
                candidate["confidence"] = confidence
                candidate["evidence_text"] = evidence_text
        except Exception as e:
            print(f"Failed to classify candidate {candidate['name']}: {e}")
            cls._apply_section_failsafe(candidate, "UNCERTAIN", 0.0, "LLM classification failed.")
            
        return candidate

