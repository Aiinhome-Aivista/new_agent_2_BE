import difflib
import re
from typing import List, Dict, Any, Tuple, Optional


class ScopeDeduplicator:
    """
    Deterministically deduplicates scope candidates with strict contractual type-awareness,
    action/entity preservation, milestone protection, and complete merge traceability.
    """

    COMPATIBLE_TYPE_GROUPS = {
        "MILESTONE": {"MILESTONE"},
        "DELIVERABLE": {"DELIVERABLE"},
        "CLIENT RESPONSIBILITY": {"CLIENT RESPONSIBILITY"},
        "VENDOR RESPONSIBILITY": {"VENDOR RESPONSIBILITY"},
        "GOVERNANCE OBLIGATION": {"GOVERNANCE OBLIGATION"},
        "DEPENDENCY": {"DEPENDENCY"},
        "ASSUMPTION": {"ASSUMPTION"},
        "SCOPE ITEM": {"SCOPE ITEM", "FUNCTIONAL REQUIREMENT"},
        "CONTEXTUAL CONTRACT RECORD": {"CONTEXTUAL CONTRACT RECORD"},
    }

    ACTION_CONFLICT_PAIRS = {
        ("view", "post"), ("post", "view"),
        ("view", "download"), ("download", "view"),
        ("view", "delete"), ("delete", "view"),
        ("post", "download"), ("download", "post"),
        ("post", "delete"), ("delete", "post"),
        ("download", "delete"), ("delete", "download"),
        ("recommend", "reject"), ("reject", "recommend"),
        ("approve", "reject"), ("reject", "approve"),
        ("internal", "external"), ("external", "internal"),
    }

    ACTION_KEYWORDS = [
        "delete", "download", "reject", "recommend", "approve", "view", "post",
        "polls", "poll", "news", "footer", "internal", "external", "upload", "email"
    ]

    NAMED_PERSON_KEYWORDS = ["sam", "nick", "elon", "sundar", "satya", "demario"]

    ROLE_KEYWORDS = {
        "sponsor", "partner", "manager", "lead", "architect", "developer",
        "coordinator", "director", "chair", "owner", "officer", "executive"
    }

    @classmethod
    def are_compatible_record_types(cls, type_a: Optional[str], type_b: Optional[str]) -> bool:
        t_a = (type_a or "").strip().upper()
        t_b = (type_b or "").strip().upper()

        if not t_a or not t_b:
            return t_a == t_b

        if t_a == t_b:
            return True

        # Milestones, Deliverables, and Responsibilities MUST NOT cross-merge
        if "MILESTONE" in t_a or "MILESTONE" in t_b:
            return t_a == t_b
        if "DELIVERABLE" in t_a or "DELIVERABLE" in t_b:
            return t_a == t_b
        if "RESPONSIBILITY" in t_a or "RESPONSIBILITY" in t_b:
            return t_a == t_b
        if "GOVERNANCE" in t_a or "GOVERNANCE" in t_b:
            return t_a == t_b
        if "DEPENDENCY" in t_a or "DEPENDENCY" in t_b:
            return t_a == t_b
        if "ASSUMPTION" in t_a or "ASSUMPTION" in t_b:
            return t_a == t_b

        # Check compatible type groups
        for _, group in cls.COMPATIBLE_TYPE_GROUPS.items():
            if t_a in group and t_b in group:
                return True

        return False

    @classmethod
    def _extract_level_qualifiers(cls, text: str) -> set:
        """
        Extracts escalation levels or numeric/ordinal level qualifiers from text.
        E.g. 'Level 1 escalation matrix' -> {'level_1'}
             'Level 2 escalation matrix' -> {'level_2'}
             'lvl 3' -> {'level_3'}
        """
        if not text:
            return set()
        text_lower = text.lower()
        levels = set()

        # Regex for level / lvl / tier / stage / phase followed by number or roman numerals
        pattern = r"\b(?:level|lvl|tier|stage|phase)\s*([0-9]+|[ivx]+)\b"
        for m in re.finditer(pattern, text_lower):
            val = m.group(1)
            roman_map = {"i": "1", "ii": "2", "iii": "3", "iv": "4", "v": "5"}
            val_norm = roman_map.get(val, val)
            levels.add(f"level_{val_norm}")

        # Ordinals like 'first level', 'second level', '1st level', 'level one'
        word_to_num = {
            "1": "1", "one": "1", "first": "1", "1st": "1",
            "2": "2", "two": "2", "second": "2", "2nd": "2",
            "3": "3", "three": "3", "third": "3", "3rd": "3",
            "4": "4", "four": "4", "fourth": "4", "4th": "4",
            "5": "5", "five": "5", "fifth": "5", "5th": "5",
        }
        for word, num in word_to_num.items():
            if re.search(rf"\b(?:level|lvl|tier)\s+{word}\b", text_lower) or re.search(rf"\b{word}\s+(?:level|lvl|tier)\b", text_lower):
                levels.add(f"level_{num}")

        return levels

    @classmethod
    def can_merge(
        cls, candidate: Dict[str, Any], existing: Dict[str, Any], similarity_threshold: float = 0.88
    ) -> Tuple[bool, Optional[str], float]:
        """
        Determines whether candidate can legitimately merge into existing.
        Returns: (can_merge, merge_reason, similarity_score)
        """
        cand_name = (candidate.get("name") or "").strip()
        exist_name = (existing.get("name") or "").strip()
        cand_name_lower = cand_name.lower()
        exist_name_lower = exist_name.lower()

        cand_type = candidate.get("record_type")
        exist_type = existing.get("record_type")
        cand_scope = candidate.get("scope_status") or candidate.get("scope_type") or "IN_SCOPE"
        exist_scope = existing.get("scope_status") or existing.get("scope_type") or "IN_SCOPE"

        # 1. Scope type check (IN_SCOPE cannot merge with OUT_OF_SCOPE or UNCERTAIN)
        if cand_scope != exist_scope:
            return False, f"Conflicting scope types: '{cand_scope}' vs '{exist_scope}'", 0.0

        # 2. Record type compatibility check
        if not cls.are_compatible_record_types(cand_type, exist_type):
            return False, f"Incompatible record types: '{cand_type}' vs '{exist_type}'", 0.0

        # 3. Pure milestone check
        cand_is_milestone = bool(candidate.get("is_pure_milestone") or "MILESTONE" in (cand_type or "").upper())
        exist_is_milestone = bool(existing.get("is_pure_milestone") or "MILESTONE" in (exist_type or "").upper())
        if cand_is_milestone != exist_is_milestone:
            return False, f"Milestone mismatch: candidate milestone={cand_is_milestone} vs existing milestone={exist_is_milestone}", 0.0

        # If both are milestones, check dates, fee percentages, and payment conditions
        if cand_is_milestone and exist_is_milestone:
            cand_desc = (candidate.get("description") or "").lower()
            exist_desc = (existing.get("description") or "").lower()

            # Check percentage fee mentions (e.g. 60%, 10%)
            cand_pcts = re.findall(r"\b\d+%\b", cand_desc)
            exist_pcts = re.findall(r"\b\d+%\b", exist_desc)
            if cand_pcts and exist_pcts and set(cand_pcts) != set(exist_pcts):
                return False, f"Conflicting milestone fee percentages: {cand_pcts} vs {exist_pcts}", 0.0

            # Check dates
            cand_dates = set(re.findall(r"\b\d{1,2}(?:st|nd|rd|th)?\s+[A-Za-z]+\s+\d{4}\b", cand_desc))
            exist_dates = set(re.findall(r"\b\d{1,2}(?:st|nd|rd|th)?\s+[A-Za-z]+\s+\d{4}\b", exist_desc))
            if cand_dates and exist_dates and cand_dates != exist_dates:
                return False, f"Conflicting milestone dates: {cand_dates} vs {exist_dates}", 0.0

        # 4. Action conflict check
        cand_action = (candidate.get("action") or "").lower()
        exist_action = (existing.get("action") or "").lower()
        if (cand_action, exist_action) in cls.ACTION_CONFLICT_PAIRS or (exist_action, cand_action) in cls.ACTION_CONFLICT_PAIRS:
            return False, f"Conflicting actions: '{cand_action}' vs '{exist_action}'", 0.0

        # 5. Distinct feature keywords check
        c_kws = {kw for kw in cls.ACTION_KEYWORDS if kw in cand_name_lower}
        e_kws = {kw for kw in cls.ACTION_KEYWORDS if kw in exist_name_lower}
        if c_kws != e_kws:
            return False, f"Distinct operational keywords: {c_kws} vs {e_kws}", 0.0

        # 6. Distinct named individuals check (Steering Committee members, etc.)
        c_people = {p for p in cls.NAMED_PERSON_KEYWORDS if p in cand_name_lower}
        e_people = {p for p in cls.NAMED_PERSON_KEYWORDS if p in exist_name_lower}
        if c_people != e_people:
            return False, f"Different individuals identified: {c_people} vs {e_people}", 0.0

        # 6b. Escalation matrix levels / numeric / ordinal level qualifiers check
        # Blocks merging items whose names differ by level (e.g., Level 1 vs Level 2 vs Level 3 escalation matrix)
        cand_levels = cls._extract_level_qualifiers(cand_name_lower)
        exist_levels = cls._extract_level_qualifiers(exist_name_lower)
        if cand_levels and exist_levels and cand_levels != exist_levels:
            return False, f"Conflicting escalation/governance level qualifiers: {cand_levels} vs {exist_levels}", 0.0

        # 6c. Distinct project / governance roles check
        # Blocks merging distinct roles (e.g. Project Sponsor vs Project Partner) in governance or responsibility matrices
        c_roles = {r for r in cls.ROLE_KEYWORDS if re.search(rf"\b{r}\b", cand_name_lower)}
        e_roles = {r for r in cls.ROLE_KEYWORDS if re.search(rf"\b{r}\b", exist_name_lower)}
        if c_roles and e_roles and c_roles != e_roles:
            return False, f"Distinct governance/project roles: {c_roles} vs {e_roles}", 0.0

        # 7. Environment-qualifier guard: Dev/UAT/PROD/Production must all be distinct
        ENVIRONMENT_QUALIFIERS = {"dev", "uat", "prod", "production", "staging", "test", "qa"}
        cand_envs = {e for e in ENVIRONMENT_QUALIFIERS if re.search(rf"\b{e}\b", cand_name_lower)}
        exist_envs = {e for e in ENVIRONMENT_QUALIFIERS if re.search(rf"\b{e}\b", exist_name_lower)}
        if cand_envs and exist_envs and cand_envs != exist_envs:
            return False, f"Distinct environment qualifiers: {cand_envs} vs {exist_envs}", 0.0

        # 8. Cloud-provider guard: Azure/AWS/GCP/Google Cloud must not merge with generic cloud
        CLOUD_PROVIDERS = {"azure", "aws", "gcp", "google cloud", "amazon", "microsoft"}
        cand_cloud = {c for c in CLOUD_PROVIDERS if c in cand_name_lower}
        exist_cloud = {c for c in CLOUD_PROVIDERS if c in exist_name_lower}
        if cand_cloud != exist_cloud and (cand_cloud or exist_cloud):
            return False, f"Distinct cloud providers: {cand_cloud} vs {exist_cloud}", 0.0

        # 9. Product/feature discriminator guard: distinct product or feature names must not merge
        PRODUCT_DISCRIMINATORS = [
            "employee collaboration",
            "internal job posting",
            "vconnect sharepoint",
            "vconnect",
            "job posting",
        ]

        def _product_tokens(text: str) -> frozenset:
            return frozenset(d for d in PRODUCT_DISCRIMINATORS if d in text)

        cand_prods = _product_tokens(cand_name_lower)
        exist_prods = _product_tokens(exist_name_lower)
        if cand_prods != exist_prods:
            return False, f"Distinct product/feature discriminators: {cand_prods} vs {exist_prods}", 0.0

        # 10. Exact name match or high string similarity on core normalized name
        ratio = difflib.SequenceMatcher(None, cand_name_lower, exist_name_lower).ratio()
        if cand_name_lower == exist_name_lower:
            return True, "Identical normalized requirement name and identical contractual type", 1.0

        if ratio >= similarity_threshold:
            return True, f"High semantic and textual similarity ({ratio:.2f}) within identical record type and scope", ratio

        return False, f"Similarity below threshold ({ratio:.2f} < {similarity_threshold})", ratio

    @classmethod
    def deduplicate(
        cls, candidates: List[Dict[str, Any]], similarity_threshold: float = 0.88
    ) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        """
        Deduplicates candidates.
        Returns:
            (consolidated_candidates, merge_reconciliation_records)
        """
        if not candidates:
            return [], []

        merged_candidates: List[Dict[str, Any]] = []
        merge_records: List[Dict[str, Any]] = []

        for candidate in candidates:
            matched_existing = None
            merge_reason = None
            merge_ratio = 0.0

            for existing in merged_candidates:
                can_merge_flag, reason, score = cls.can_merge(candidate, existing, similarity_threshold=similarity_threshold)
                if can_merge_flag:
                    matched_existing = existing
                    merge_reason = reason
                    merge_ratio = score
                    break

            if matched_existing:
                orig_id = candidate.get("candidate_id")
                retained_id = matched_existing.get("candidate_id")

                # Track explicit merge reconciliation record
                reconciliation_entry = {
                    "original_candidate_id": orig_id,
                    "retained_candidate_id": retained_id,
                    "original_name": candidate.get("name"),
                    "retained_name": matched_existing.get("name"),
                    "original_record_type": candidate.get("record_type"),
                    "retained_record_type": matched_existing.get("record_type"),
                    "original_source_locator": candidate.get("source_locator"),
                    "retained_source_locator": matched_existing.get("source_locator"),
                    "original_quotation": candidate.get("evidence_snippet") or candidate.get("raw_text") or candidate.get("evidence_text"),
                    "retained_quotation": matched_existing.get("evidence_snippet") or matched_existing.get("raw_text") or matched_existing.get("evidence_text"),
                    "similarity_score": round(merge_ratio, 3),
                    "confidence": round(merge_ratio, 3),
                    "merge_reason": merge_reason,
                    "material_attributes_preserved": {
                        "scope_type": matched_existing.get("scope_status") or matched_existing.get("scope_type"),
                        "actions": list(set((candidate.get("actions") or []) + (matched_existing.get("actions") or []))),
                        "atomic_claims_retained": len(matched_existing.get("atomic_claims") or []) + len(candidate.get("atomic_claims") or []),
                    },
                    "is_true_duplicate": True,
                }
                merge_records.append(reconciliation_entry)

                # Merge logic: pick longer / more descriptive title
                if len(candidate.get("name", "")) > len(matched_existing.get("name", "")):
                    matched_existing["name"] = candidate["name"]

                # Append description without duplicating
                cand_desc = candidate.get("description", "")
                if cand_desc and cand_desc not in matched_existing.get("description", ""):
                    matched_existing["description"] = f"{matched_existing.get('description', '')} | {cand_desc}".strip(" |")

                # Combine atomic claims
                cand_claims = candidate.get("atomic_claims") or []
                exist_claims = matched_existing.get("atomic_claims") or []
                for cl in cand_claims:
                    if cl not in exist_claims:
                        exist_claims.append(cl)
                matched_existing["atomic_claims"] = exist_claims

                # Combine claims_evidence
                cand_ce = candidate.get("claims_evidence") or []
                exist_ce = matched_existing.get("claims_evidence") or []
                for ce in cand_ce:
                    if not any(e.get("claim") == ce.get("claim") for e in exist_ce):
                        exist_ce.append(ce)
                matched_existing["claims_evidence"] = exist_ce

                # Combine evidence_spans
                cand_spans = candidate.get("evidence_spans") or []
                exist_spans = matched_existing.get("evidence_spans") or []
                for sp in cand_spans:
                    if not any(s.get("source_locator") == sp.get("source_locator") and s.get("offset_start") == sp.get("offset_start") for s in exist_spans):
                        exist_spans.append(sp)
                matched_existing["evidence_spans"] = exist_spans

                # Add to merged_sources of retained item
                merged_sources = matched_existing.setdefault("merged_sources", [])
                merged_sources.append({
                    "candidate_id": orig_id,
                    "name": candidate.get("name"),
                    "chunk_index": candidate.get("chunk_index"),
                    "source_locator": candidate.get("source_locator"),
                    "evidence_snippet": candidate.get("evidence_snippet") or candidate.get("evidence_text"),
                    "rationale": f"Consolidated into {retained_id} during Step 5 deduplication ({merge_reason})",
                })

                # Propagate timeline / milestone metadata if existing lacks it
                for field in ["deadline_text", "deadline", "milestone", "milestone_status", "category"]:
                    if candidate.get(field) and not matched_existing.get(field):
                        matched_existing[field] = candidate[field]

            else:
                # Retained as a distinct candidate
                cand_copy = dict(candidate)
                # Ensure merged_sources list is initialized
                if "merged_sources" not in cand_copy:
                    cand_copy["merged_sources"] = list(candidate.get("merged_sources") or [])
                # Record possible duplicate or related relationships for transparency
                for existing in merged_candidates:
                    r = difflib.SequenceMatcher(None, (candidate.get("name") or "").lower(), (existing.get("name") or "").lower()).ratio()
                    if r >= 0.70:
                        cand_copy.setdefault("related_candidates", []).append({
                            "candidate_id": existing.get("candidate_id"),
                            "name": existing.get("name"),
                            "relationship": "POSSIBLE_DUPLICATE" if r >= similarity_threshold else "RELATED_TO",
                            "similarity_score": round(r, 3),
                        })
                merged_candidates.append(cand_copy)

        return merged_candidates, merge_records
