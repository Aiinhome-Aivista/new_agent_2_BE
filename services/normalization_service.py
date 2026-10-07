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
        from agents.risk_evaluation_agent import _deterministic_match
        from services.entity_resolver import _clean_baseline_name
        all_items = (all_baseline_items or []) + (in_scope_items or [])

        def _format_matched_title(si: dict) -> str:
            cleaned = _clean_baseline_name(si.get("name", ""))
            if ":" in cleaned and len(cleaned) > 60:
                short_part = cleaned.split(":")[0].strip()
                return cls.normalize_scope_item(short_part)
            return cls.normalize_scope_item(cleaned or si.get("scope_item_normalized", si.get("name", "")))

        # Priority 1: Check explicitly matched baseline item against in-scope items
        if matched_baseline_item:
            si_match, conf, _ = _deterministic_match(matched_baseline_item, in_scope_items)
            if si_match:
                return _format_matched_title(si_match), True

            norm_match = cls.normalize_scope_item(matched_baseline_item).lower()
            for si in (in_scope_items or []):
                si_norm = cls.normalize_scope_item(si["name"]).lower()
                if norm_match == si_norm or (len(norm_match) >= 6 and (norm_match in si_norm or si_norm in norm_match)):
                    return _format_matched_title(si), True

            # Priority 1b: If matched_baseline_item was explicitly given, check all items (e.g. out-of-scope exclusions)
            si_match_all, conf_all, _ = _deterministic_match(matched_baseline_item, all_items)
            if si_match_all:
                is_in_scope = (str(si_match_all.get("scope_type", "IN_SCOPE")).upper() == "IN_SCOPE" and
                               str(si_match_all.get("category", "")).upper() != "OUT_OF_SCOPE" and
                               str(si_match_all.get("type", "")).upper() != "OUT_OF_SCOPE")
                if is_in_scope:
                    return _format_matched_title(si_match_all), True
                # Never rewrite an activity's title into an out-of-scope assumption sentence
                return cls.normalize_scope_item(activity_name or matched_baseline_item), False

            for si in all_items:
                si_norm = cls.normalize_scope_item(si["name"]).lower()
                if norm_match == si_norm or (len(norm_match) >= 6 and (norm_match in si_norm or si_norm in norm_match)):
                    is_in_scope = (str(si.get("scope_type", "IN_SCOPE")).upper() == "IN_SCOPE" and
                                   str(si.get("category", "")).upper() != "OUT_OF_SCOPE" and
                                   str(si.get("type", "")).upper() != "OUT_OF_SCOPE")
                    if is_in_scope:
                        return _format_matched_title(si), True
                    return cls.normalize_scope_item(activity_name or matched_baseline_item), False

        # Priority 2: Check activity_name against IN_SCOPE items ONLY (never match against out-of-scope assumptions)
        if activity_name:
            si_match, conf, _ = _deterministic_match(activity_name, in_scope_items)
            if si_match:
                return _format_matched_title(si_match), True

            norm_match = cls.normalize_scope_item(activity_name).lower()
            for si in (in_scope_items or []):
                si_norm = cls.normalize_scope_item(si["name"]).lower()
                if norm_match == si_norm or (len(norm_match) >= 6 and (norm_match in si_norm or si_norm in norm_match)):
                    return _format_matched_title(si), True

        # Priority 3: No baseline match — use normalized activity name (never rewrite to assumption sentence)
        return cls.normalize_scope_item(activity_name or matched_baseline_item), False

