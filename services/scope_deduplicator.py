import difflib

class ScopeDeduplicator:
    """
    Deterministically merges overlapping or duplicate scope candidates.
    Uses fuzzy string matching to group similar items.
    """

    @classmethod
    def deduplicate(cls, candidates: list[dict], similarity_threshold: float = 0.75) -> list[dict]:
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
                # >>> CHANGED: Do NOT concatenate description/evidence_text with
                # " | " / "Furthermore: ...". That produced unbounded merge
                # chains, especially when a duplicate came from a
                # mis-tagged section (see ScopeSectionDetector patch). The
                # first-seen candidate's description/evidence already carries
                # the meaningful content (e.g. the original scope sentence
                # already contains its own inline deadline, which
                # MilestoneDeadlineExtractor extracts directly) — a later
                # near-duplicate is redundant, not additive, so it is folded
                # in silently instead of appended.

                # Only promote scope_type/confidence if the existing entry
                # was still UNCERTAIN and the new one is more specific.
                if matched_existing["scope_type"] == "UNCERTAIN" and candidate["scope_type"] != "UNCERTAIN":
                    matched_existing["scope_type"] = candidate["scope_type"]
                    matched_existing["evidence_text"] = candidate["evidence_text"]
                    matched_existing["confidence"] = candidate["confidence"]

                # If the existing candidate is missing a milestone/deadline
                # but this duplicate happens to carry one (e.g. it came from
                # a Milestones-section restatement), backfill just that
                # field — structured enrichment, not raw text concatenation.
                if not matched_existing.get("milestone") and candidate.get("milestone"):
                    matched_existing["milestone"] = candidate["milestone"]
                if not matched_existing.get("deadline_text") and candidate.get("deadline_text"):
                    matched_existing["deadline_text"] = candidate["deadline_text"]
                if not matched_existing.get("deadline") and candidate.get("deadline"):
                    matched_existing["deadline"] = candidate["deadline"]

                # Track how many raw candidates were folded into this one,
                # for debugging/audit, without polluting evidence_text.
                matched_existing["duplicate_count"] = matched_existing.get("duplicate_count", 1) + 1
            else:
                # No match found, add as a new distinct item
                candidate.setdefault("duplicate_count", 1)
                merged_candidates.append(candidate)

        return merged_candidates