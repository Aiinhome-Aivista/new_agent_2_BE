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
