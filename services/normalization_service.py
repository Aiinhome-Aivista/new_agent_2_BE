import re

class NormalizationService:
    """
    Normalizes candidate names and milestones to extract purely the business object
    by removing standard contractual phrasing (e.g. "The Vendor shall provide").
    """

    CONTRACTUAL_PREFIX_PATTERN = re.compile(
        r"^(?:the\s+)?(?:vendor|customer|client|we|they|you|company)\s+(?:shall|will|must|should|to|agrees\s+to|is\s+responsible\s+for)\s+(?:provide|implement|configure|perform|develop|deliver|ensure|maintain|support|conduct|create|set\s+up)\s+(?:an?\s+)?",
        re.IGNORECASE
    )

    OUT_OF_SCOPE_PATTERN = re.compile(
        r"\s+(?:is|are)?\s*(?:strictly\s+)?out\s+of\s+scope\.?",
        re.IGNORECASE
    )

    DEADLINE_SUFFIX_PATTERN = re.compile(
        r"(?:\s*\([^)]+\))?\s+(?:by|on|before|after|scheduled\s+for|due\s+on|prior\s+to)\s+(?:(?:[0-9]{1,2}\s+[A-Za-z]+\s+[0-9]{4}|[0-9]{1,2}\s+[A-Za-z]+|[A-Za-z]+\s+[0-9]{1,2}(?:,\s+[0-9]{4})?|[0-9]{4}-[0-9]{2}-[0-9]{2}|[0-9]{1,2}/[0-9]{1,2}/[0-9]{2,4}|Q[1-4]\s+[0-9]{4}|End of [A-Za-z]+)(?:\s+.*)?|implementation(?:[^.]*)?)\.?",
        re.IGNORECASE
    )

    DANGLING_SUFFIX_PATTERN = re.compile(
        r"\s+(?:by|on|before|after|within|until|during|scheduled\s+for|due\s+on|prior\s+to)\s*$",
        re.IGNORECASE
    )

    @classmethod
    def normalize_scope_item(cls, text: str) -> str:
        """
        Strips contractual phrasing from a full sentence to isolate the deliverable or responsibility.
        E.g. "Customer shall provide API credentials." -> "API credentials"
        """
        if not text:
            return ""

        # Remove trailing deadline info (already captured elsewhere)
        clean_text = cls.DEADLINE_SUFFIX_PATTERN.sub('', text)

        # Remove "out of scope" suffix
        clean_text = cls.OUT_OF_SCOPE_PATTERN.sub('', clean_text)

        # Remove common contractual verbs/subjects prefix
        clean_text = cls.CONTRACTUAL_PREFIX_PATTERN.sub('', clean_text)

        # Clean trailing punctuation
        clean_text = clean_text.strip().rstrip('.;,')

        # Remove dangling grammar words at the end
        clean_text = cls.DANGLING_SUFFIX_PATTERN.sub('', clean_text)

        # Clean trailing punctuation again in case dangling words removal left any
        clean_text = clean_text.strip().rstrip('.;,')

        # Capitalize first letter while maintaining rest of casing
        if clean_text:
            clean_text = clean_text[0].upper() + clean_text[1:]

        return clean_text

    @classmethod
    def normalize_milestone(cls, text: str, normalized_scope_item: str) -> str:
        """
        If the milestone extracted is an entire sentence (over 5 words), just default to the normalized scope item.
        Otherwise, keep the explicit milestone name (e.g. 'Go Live').
        """
        if not text:
            return None
            
        words = text.split()
        if len(words) > 5:
            # It's an entire sentence, replace with normalized scope item
            return normalized_scope_item
            
        return text.strip().rstrip('.;,')

    @classmethod
    def resolve_canonical_entity(cls, activity_name: str, matched_baseline_item: str,
                               in_scope_items: list, all_baseline_items: list = None) -> tuple:
        """
        Implements the Tracker Title Priority rule:
          1. Matched IN_SCOPE baseline item name  -> (scope_item_normalized, True)
          2. Matched OUT_OF_SCOPE baseline item   -> (scope_item_normalized, False)
          3. Normalized activity name             -> (normalize_scope_item(activity_name), False)

        Returns (canonical_title, is_confirmed_in_scope)
        """
        all_items = (all_baseline_items or []) + (in_scope_items or [])

        if matched_baseline_item:
            norm_match = cls.normalize_scope_item(matched_baseline_item).lower()

            # Priority 1: check IN_SCOPE items first
            for si in in_scope_items:
                si_norm = cls.normalize_scope_item(si["name"]).lower()
                if norm_match == si_norm or norm_match in si_norm or si_norm in norm_match:
                    # Final normalization pass on the DB normalized name in case of legacy dirty data
                    final_title = cls.normalize_scope_item(si.get("scope_item_normalized", si["name"]))
                    return final_title, True

            # Priority 2: check ALL baseline items (including OUT_OF_SCOPE exclusions)
            for si in all_items:
                si_norm = cls.normalize_scope_item(si["name"]).lower()
                if norm_match == si_norm or norm_match in si_norm or si_norm in norm_match:
                    final_title = cls.normalize_scope_item(si.get("scope_item_normalized", si["name"]))
                    return final_title, False

            # Priority 2 fallback: use whatever the LLM said (already normalized by the prompt, but we enforce it)
            return cls.normalize_scope_item(matched_baseline_item), False

        # Priority 3: no baseline match — use normalized activity name
        return cls.normalize_scope_item(activity_name), False
