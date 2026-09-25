import json
from services.llm_service import LLMService
import difflib

class ScopeClassifier:
    """
    Uses Hybrid Retrieval to find supporting evidence for a candidate item,
    and then uses a small LLM prompt to classify it.
    """
    
    IN_SCOPE_SECTIONS = {"In Scope", "Scope of Work", "Deliverables", "Responsibilities", "Milestones", "Recurring Commitments"}
    OUT_OF_SCOPE_SECTIONS = {"Out of Scope", "Assumptions", "Client Responsibilities", "Customer Responsibilities"}
    BAD_SECTIONS = OUT_OF_SCOPE_SECTIONS

    @classmethod
    def _get_section_fallback_evidence(cls, candidate: dict) -> str:
        cand_section = candidate.get("section", "General")
        raw_text = candidate.get("raw_text") or candidate.get("description") or candidate.get("name")
        return f"Evidence from Document Section [{cand_section}]:\nItem explicitly listed in contract under section '{cand_section}': \"{raw_text}\""

    @classmethod
    def _apply_section_failsafe(cls, candidate: dict, original_scope_type: str = "UNCERTAIN", original_conf: float = 0.0, original_evidence: str = ""):
        cand_section = candidate.get("section", "General")
        if cand_section in cls.OUT_OF_SCOPE_SECTIONS:
            candidate["scope_type"] = "OUT_OF_SCOPE"
            candidate["confidence"] = 0.95
            candidate["evidence_text"] = f"Item explicitly specified under '{cand_section}' section of the contract."
        elif cand_section in cls.IN_SCOPE_SECTIONS:
            candidate["scope_type"] = "IN_SCOPE"
            candidate["confidence"] = 0.90
            candidate["evidence_text"] = f"Item specified under '{cand_section}' section of the contract."
        else:
            candidate["scope_type"] = original_scope_type
            candidate["confidence"] = original_conf
            candidate["evidence_text"] = original_evidence or "No specific supporting evidence found in the contract."

    @classmethod
    def prefilter_by_heading(cls, candidate: dict) -> dict | None:
        """
        Deterministic heading pre-filter function (Item 19).
        Classifies items from labelled sections with confidence=1.0 without LLM.
        Returns classified candidate dict if unambiguous, else None for ambiguous items.
        """
        cand_section = candidate.get("section", "General")
        
        # Unambiguous Out-of-Scope sections
        if cand_section in cls.OUT_OF_SCOPE_SECTIONS:
            c = dict(candidate)
            c["scope_type"] = "OUT_OF_SCOPE"
            c["confidence"] = 1.0
            c["evidence_text"] = f"Deterministic section classification from '{cand_section}' heading."
            return c

        # Unambiguous In-Scope sections
        if cand_section in cls.IN_SCOPE_SECTIONS:
            cand_text = f"{candidate.get('name', '')} {candidate.get('description', '')}".lower()
            # If explicit exclusion signals exist, do not classify deterministically
            has_exclusion = any(sig in cand_text for sig in [
                "not included", "excluded", "out of scope", "outside the scope",
                "customer responsibility", "client responsibility",
                "client will provide", "customer will provide"
            ])
            if not has_exclusion:
                c = dict(candidate)
                c["scope_type"] = "IN_SCOPE"
                c["confidence"] = 1.0
                c["evidence_text"] = f"Deterministic section classification from '{cand_section}' heading."
                return c

        # Ambiguous item needing LLM evaluation
        return None

    @classmethod
    def prefilter_candidates(cls, candidates: list[dict]) -> tuple[list[tuple[int, dict]], list[tuple[int, dict]]]:
        """
        Splits candidates into (deterministic_items, ambiguous_items) preserving original index (Item 20).
        """
        deterministic = []
        ambiguous = []
        for idx, cand in enumerate(candidates):
            classified = cls.prefilter_by_heading(cand)
            if classified is not None:
                deterministic.append((idx, classified))
            else:
                ambiguous.append((idx, cand))
        return deterministic, ambiguous

    @classmethod
    def classify_candidates_batch(cls, project_id: int, candidates: list[dict]) -> list[dict]:
        from services.hybrid_retrieval_service import HybridRetrievalService
        
        # Step 0: Deterministic heading pre-filter (Items 19 & 20)
        deterministic_items, ambiguous_items = cls.prefilter_candidates(candidates)

        if not ambiguous_items:
            print(f"[LLM] All {len(candidates)} candidates deterministically classified by section headings (0 tokens).")
            return [item for _, item in sorted(deterministic_items, key=lambda x: x[0])]

        print(f"[LLM] {len(deterministic_items)}/{len(candidates)} candidates pre-filtered by heading (confidence=1.0). "
              f"Sending {len(ambiguous_items)} ambiguous items to LLM batch...")

        ambiguous_candidates = [cand for _, cand in ambiguous_items]
        
        llm_batch = []
        for candidate in ambiguous_candidates:
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
            from agents.llm_schemas import ScopeClassificationOutput
            prompt = get_batch_scope_classifier_prompt(items_for_prompt)
            try:
                # IMPROVEMENT 3: structured output eliminates JSON parse failures
                structured_result = LLMService.generate_structured(
                    prompt, ScopeClassificationOutput, fallback_key='items'
                )

                # Normalize to a flat list of result dicts
                if isinstance(structured_result, ScopeClassificationOutput):
                    batch_results = [si.model_dump() for si in structured_result.items]
                elif isinstance(structured_result, dict):
                    batch_results = structured_result.get('items', [])
                    if not batch_results:
                        for val in structured_result.values():
                            if isinstance(val, list):
                                batch_results = val
                                break
                elif isinstance(structured_result, list):
                    batch_results = structured_result
                else:
                    batch_results = []

                result_map = {str(res.get("id", "")): res for res in batch_results}
                for idx, item in enumerate(batch_slice):
                    candidate_ref = item["candidate"]
                    res = result_map.get(str(idx), {})
                    scope_type = res.get("scope_type", "UNCERTAIN")
                    confidence = res.get("confidence", 0.5)
                    evidence_text = res.get("evidence_text", "No reasoning provided.")

                    # Enforce section ground truth over LLM boundary hallucinations
                    # Section failsafe: only override to IN_SCOPE if BOTH conditions are true:
                    # 1. The section heading is an in-scope section (not Out of Scope, Assumptions, etc.)
                    # 2. The section is NOT an out-of-scope section (double-check, catches
                    #    cases where section detector misclassified a heading)
                    # Generic: uses section name comparison only, no item-name matching.
                    cand_sec = candidate_ref.get("section", "General")
                    if (cand_sec in cls.IN_SCOPE_SECTIONS
                            and cand_sec not in cls.OUT_OF_SCOPE_SECTIONS
                            and scope_type in {"OUT_OF_SCOPE", "UNCERTAIN"}):
                        # Before overriding, check if item name contains explicit
                        # out-of-scope language — if so, trust the LLM, not the section
                        item_name_lower = (candidate_ref.get('name', '') or '').lower()
                        EXPLICIT_EXCLUSION_SIGNALS = [
                            'not included', 'excluded', 'out of scope', 'outside scope',
                            'not covered', 'not in scope', 'beyond scope',
                        ]
                        if not any(sig in item_name_lower for sig in EXPLICIT_EXCLUSION_SIGNALS):
                            cls._apply_section_failsafe(
                                candidate_ref, "IN_SCOPE", 0.95,
                                f"Item listed under '{cand_sec}' section of the contract."
                            )
                        else:
                            # Item name contains exclusion language — trust the LLM
                            print(f"  [SectionFailsafe] Skipped IN_SCOPE override for "
                                  f"'{candidate_ref.get('name', '')[:50]}' — "
                                  f"item name contains explicit exclusion language.")
                            candidate_ref["scope_type"] = scope_type
                            candidate_ref["confidence"] = confidence
                            candidate_ref["evidence_text"] = evidence_text

                    # NEVER apply IN_SCOPE failsafe to items under Out of Scope sections
                    elif cand_sec in cls.OUT_OF_SCOPE_SECTIONS and scope_type == "IN_SCOPE":
                        # Reverse failsafe: if LLM says IN_SCOPE but section is Out of Scope,
                        # trust the section — it is structurally unambiguous
                        cls._apply_section_failsafe(
                            candidate_ref, "OUT_OF_SCOPE", 0.90,
                            f"Item listed under '{cand_sec}' section — structurally out of scope."
                        )
                    elif cand_sec in cls.OUT_OF_SCOPE_SECTIONS:
                        cls._apply_section_failsafe(
                            candidate_ref, "OUT_OF_SCOPE", 0.95,
                            f"Item listed under '{cand_sec}' section of the contract."
                        )
                    elif scope_type == "UNCERTAIN" and cand_sec != "General":
                        cls._apply_section_failsafe(candidate_ref, scope_type, confidence, evidence_text)
                    else:
                        candidate_ref["scope_type"] = scope_type
                        candidate_ref["confidence"] = confidence
                        candidate_ref["evidence_text"] = evidence_text
            except Exception as e:
                print(f"Failed to classify batch {i}: {e}")
                for item in batch_slice:
                    candidate_ref = item["candidate"]
        results_by_index = {idx: item for idx, item in deterministic_items}
        for (idx, _), cand in zip(ambiguous_items, ambiguous_candidates):
            results_by_index[idx] = cand
        return [results_by_index[i] for i in range(len(candidates))]
        
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
            
            # Section failsafe: only override to IN_SCOPE if BOTH conditions are true:
            # 1. The section heading is an in-scope section (not Out of Scope, Assumptions, etc.)
            # 2. The section is NOT an out-of-scope section
            cand_sec = candidate.get("section", "General")
            if (cand_sec in cls.IN_SCOPE_SECTIONS
                    and cand_sec not in cls.OUT_OF_SCOPE_SECTIONS
                    and scope_type in {"OUT_OF_SCOPE", "UNCERTAIN"}):
                item_name_lower = (candidate.get('name', '') or '').lower()
                EXPLICIT_EXCLUSION_SIGNALS = [
                    'not included', 'excluded', 'out of scope', 'outside scope',
                    'not covered', 'not in scope', 'beyond scope',
                ]
                if not any(sig in item_name_lower for sig in EXPLICIT_EXCLUSION_SIGNALS):
                    cls._apply_section_failsafe(
                        candidate, "IN_SCOPE", 0.95,
                        f"Item listed under '{cand_sec}' section of the contract."
                    )
                else:
                    print(f"  [SectionFailsafe] Skipped IN_SCOPE override for "
                          f"'{candidate.get('name', '')[:50]}' — "
                          f"item name contains explicit exclusion language.")
                    candidate["scope_type"] = scope_type
                    candidate["confidence"] = confidence
                    candidate["evidence_text"] = evidence_text
            elif cand_sec in cls.OUT_OF_SCOPE_SECTIONS and scope_type == "IN_SCOPE":
                cls._apply_section_failsafe(
                    candidate, "OUT_OF_SCOPE", 0.90,
                    f"Item listed under '{cand_sec}' section — structurally out of scope."
                )
            elif cand_sec in cls.OUT_OF_SCOPE_SECTIONS:
                cls._apply_section_failsafe(
                    candidate, "OUT_OF_SCOPE", 0.95,
                    f"Item listed under '{cand_sec}' section of the contract."
                )
            elif scope_type == "UNCERTAIN" and cand_sec != "General":
                cls._apply_section_failsafe(candidate, scope_type, confidence, evidence_text)
            else:
                candidate["scope_type"] = scope_type
                candidate["confidence"] = confidence
                candidate["evidence_text"] = evidence_text
        except Exception as e:
            print(f"Failed to classify candidate {candidate['name']}: {e}")
            cls._apply_section_failsafe(candidate, "UNCERTAIN", 0.0, "LLM classification failed.")
            
        return candidate

