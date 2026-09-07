import difflib

class ScopeDeduplicator:
    """
    Deterministically merges overlapping or duplicate scope candidates.
    Uses fuzzy string matching to group similar items.
    """
    
    @classmethod
    def deduplicate(cls, candidates: list[dict], similarity_threshold: float = 0.80) -> list[dict]:
        if not candidates:
            return []
            
        merged_candidates = []
        
        for candidate in candidates:
            # Try to find a highly similar existing candidate
            matched_existing = None
            cand_name_lower = candidate.get("name", "").lower().strip()
            
            for existing in merged_candidates:
                exist_name_lower = existing.get("name", "").lower().strip()
                # Compare names
                name_ratio = difflib.SequenceMatcher(None, cand_name_lower, exist_name_lower).ratio()
                
                # If names are very similar, or one name is completely contained within the other
                if name_ratio > similarity_threshold or \
                   (len(cand_name_lower) > 5 and cand_name_lower in exist_name_lower) or \
                   (len(exist_name_lower) > 5 and exist_name_lower in cand_name_lower):
                    matched_existing = existing
                    break
                    
            if matched_existing:
                # Merge logic: Pick the more descriptive / longer name as the primary title
                if len(candidate.get("name", "")) > len(matched_existing.get("name", "")):
                    matched_existing["name"] = candidate["name"]

                # Append the new description to the existing one to preserve detail
                if candidate.get("description") and candidate["description"] not in matched_existing.get("description", ""):
                    matched_existing["description"] = f"{matched_existing.get('description', '')} | {candidate['description']}".strip(" |")
                    
                # If there's conflicting scope types, default to the more explicit one (IN/OUT > UNCERTAIN)
                if matched_existing.get("scope_type") == "UNCERTAIN" and candidate.get("scope_type") != "UNCERTAIN":
                    matched_existing["scope_type"] = candidate["scope_type"]
                    matched_existing["evidence_text"] = candidate["evidence_text"]
                    matched_existing["confidence"] = candidate["confidence"]
                    
                # Combine evidence if different
                if candidate.get("evidence_text") and candidate["evidence_text"] not in matched_existing.get("evidence_text", ""):
                    matched_existing["evidence_text"] = f"{matched_existing.get('evidence_text', '')} Furthermore: {candidate['evidence_text']}".strip(" Furthermore:")
                    
                # Propagate timeline / milestone metadata
                if candidate.get("deadline_text") and not matched_existing.get("deadline_text"):
                    matched_existing["deadline_text"] = candidate["deadline_text"]
                if candidate.get("deadline") and not matched_existing.get("deadline"):
                    matched_existing["deadline"] = candidate["deadline"]
                if candidate.get("milestone") and not matched_existing.get("milestone"):
                    matched_existing["milestone"] = candidate["milestone"]
                if candidate.get("milestone_status") and not matched_existing.get("milestone_status"):
                    matched_existing["milestone_status"] = candidate["milestone_status"]
                if candidate.get("category") and not matched_existing.get("category"):
                    matched_existing["category"] = candidate["category"]

                # If a pure milestone merges with a regular scope item, the merged item is NOT a pure milestone
                if not candidate.get("is_pure_milestone", False) or not matched_existing.get("is_pure_milestone", False):
                    matched_existing["is_pure_milestone"] = False
            else:
                # No match found, add as a new distinct item
                merged_candidates.append(candidate)
                
        return merged_candidates

