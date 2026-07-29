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
            for existing in merged_candidates:
                # Compare names
                name_ratio = difflib.SequenceMatcher(None, candidate["name"].lower(), existing["name"].lower()).ratio()
                
                # If names are very similar, or one name is completely contained within the other
                if name_ratio > similarity_threshold or \
                   candidate["name"].lower() in existing["name"].lower() or \
                   existing["name"].lower() in candidate["name"].lower():
                    matched_existing = existing
                    break
                    
            if matched_existing:
                # Merge logic: Append the new description to the existing one to preserve detail
                if candidate["description"] and candidate["description"] not in matched_existing["description"]:
                    matched_existing["description"] += f" | {candidate['description']}"
                    
                # If there's conflicting scope types, default to the more explicit one (IN/OUT > UNCERTAIN)
                if matched_existing["scope_type"] == "UNCERTAIN" and candidate["scope_type"] != "UNCERTAIN":
                    matched_existing["scope_type"] = candidate["scope_type"]
                    matched_existing["evidence_text"] = candidate["evidence_text"]
                    matched_existing["confidence"] = candidate["confidence"]
                    
                # Combine evidence if different
                if candidate["evidence_text"] and candidate["evidence_text"] not in matched_existing["evidence_text"]:
                    matched_existing["evidence_text"] += f" Furthermore: {candidate['evidence_text']}"
                    
                # If a pure milestone merges with a regular scope item, the merged item is NOT a pure milestone
                if not candidate.get("is_pure_milestone", False) or not matched_existing.get("is_pure_milestone", False):
                    matched_existing["is_pure_milestone"] = False
                    
                if candidate.get("milestone_status") and not matched_existing.get("milestone_status"):
                    matched_existing["milestone_status"] = candidate["milestone_status"]
            else:
                # No match found, add as a new distinct item
                merged_candidates.append(candidate)
                
        return merged_candidates
