"""
DeliverableExtractor
----------------------
Pipeline Step 6 (runs AFTER MilestoneDeadlineExtractor.extract(...)).

Purpose:
    The router currently hardcodes:
        extracted_data = {
            "scope_items": enriched_candidates,
            "deliverables": [],     # <-- never populated
            "stakeholders": []
        }

    This service populates that list properly, WITHOUT relying on any
    specific section heading ("Milestones", "Deliverables", "Timeline", ...)
    being present in the source document — it works purely off the
    already-enriched candidate fields (milestone / deadline / deadline_text
    / extraction_confidence) that MilestoneDeadlineExtractor attaches to
    each scope item.

Integration point (api/routers/baseline.py -> extract_baseline):

    enriched_candidates = MilestoneDeadlineExtractor.extract(deduped_candidates)

    # Pipeline Step 6: Deliverable extraction
    extracted_deliverables = DeliverableExtractor.extract(enriched_candidates)

    extracted_data = {
        "scope_items": enriched_candidates,
        "deliverables": extracted_deliverables,   # was: []
        "stakeholders": []
    }

Design notes:
    - A deliverable is DERIVED from a scope item, not duplicated verbatim.
      We do NOT concatenate scope text and milestone text with " | " (that
      was the bug in ScopeDeduplicator producing messy names). Instead we
      build a clean, separate deliverable record with its own name/owner/
      deadline fields, and keep a back-reference (source_scope_item) so the
      UI can link the two.
    - Only IN_SCOPE items are eligible to become deliverables. An
      OUT_OF_SCOPE item with a date mentioned in passing (e.g. "Customer
      shall provide infrastructure before implementation") is a customer
      obligation, not a vendor deliverable — see OWNER_INFERENCE below for
      how that distinction is made instead of just "any item with a date".
"""

import re
from typing import List, Dict, Any, Optional


class DeliverableExtractor:

    # Deliverables must clear this confidence bar (classification confidence,
    # not extraction_confidence) to avoid promoting noisy/low-quality candidates.
    MIN_CONFIDENCE = 0.6

    # Phrases that indicate the OTHER party (customer/client) owns the
    # obligation, even if it has a deadline. These should NOT become vendor
    # deliverables — they matter for tracking, but as customer obligations.
    CUSTOMER_OWNER_PATTERNS = [
        r"\bcustomer shall\b",
        r"\bclient shall\b",
        r"\bcustomer responsibility\b",
        r"\bcustomer will provide\b",
    ]

    VENDOR_OWNER_PATTERNS = [
        r"\bvendor shall\b",
        r"\bcontractor shall\b",
        r"\bvendor will\b",
        r"\bwe shall\b",
        r"\bwe will\b",
    ]

    @classmethod
    def extract(cls, enriched_candidates: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Args:
            enriched_candidates: output of MilestoneDeadlineExtractor.extract(...)
                Each item is expected to (optionally) carry:
                    name, description, scope_type, confidence,
                    milestone, deadline, deadline_text,
                    extraction_method, extraction_confidence,
                    source_page, source_section

        Returns:
            List of deliverable dicts shaped for BaselineRepository.insert_deliverable /
            update_deliverable, i.e.:
                {
                    "name": str,
                    "description": str,
                    "deadline": Optional[str]   (normalized/ISO if available),
                    "owner": Optional[str],      ("Vendor" | "Customer" | None)
                    "source_scope_item": str,    (back-reference, not persisted
                                                   unless the deliverables table
                                                   has a column for it)
                }
        """
        deliverables: List[Dict[str, Any]] = []

        for item in enriched_candidates:
            if not cls._is_deliverable_candidate(item):
                continue

            owner = cls._infer_owner(item)
            deliverables.append({
                "name": cls._build_clean_name(item),
                "description": cls._build_description(item),
                "deadline": item.get("deadline") or None,
                "deadline_text": item.get("deadline_text") or None,
                "owner": owner,
                "source_scope_item": item.get("name"),
                "extraction_method": item.get("extraction_method"),
                "extraction_confidence": item.get("extraction_confidence"),
            })

        return deliverables

    # ------------------------------------------------------------------ #

    @classmethod
    def _is_deliverable_candidate(cls, item: Dict[str, Any]) -> bool:
        # Must be an in-scope, sufficiently confident item.
        if item.get("scope_type") != "IN_SCOPE":
            return False
        if (item.get("confidence") or 0) < cls.MIN_CONFIDENCE:
            return False

        # Must actually carry a deadline/milestone signal - this is what
        # MilestoneDeadlineExtractor is responsible for populating, and it
        # works regardless of whether the source had a "Milestones" heading,
        # since that extractor scans deadline phrasing per-candidate already.
        has_deadline = bool(item.get("deadline") or item.get("deadline_text"))
        has_milestone = bool(item.get("milestone"))

        return has_deadline or has_milestone

    @classmethod
    def _infer_owner(cls, item: Dict[str, Any]) -> Optional[str]:
        text = f"{item.get('name', '')} {item.get('evidence_text', '')}".lower()

        if any(re.search(p, text) for p in cls.CUSTOMER_OWNER_PATTERNS):
            return "Customer"
        if any(re.search(p, text) for p in cls.VENDOR_OWNER_PATTERNS):
            return "Vendor"
        return None

    @staticmethod
    def _build_clean_name(item: Dict[str, Any]) -> str:
        """
        Prefer the milestone label if it's short/clean (e.g. "Go Live",
        "UAT"), otherwise fall back to a trimmed scope-item name. Avoids
        the previous bug of concatenating scope text + milestone text with
        " | " into one messy string.
        """
        milestone = (item.get("milestone") or "").strip()
        name = (item.get("name") or "").strip()

        # If milestone is a short, distinct label (not just a repeat of the
        # full sentence), prefer it as the deliverable name.
        if milestone and len(milestone.split()) <= 6 and milestone.lower() not in name.lower():
            return milestone

        # Otherwise derive a shortened deliverable name from the scope
        # sentence itself (strip trailing "by <date>" clause if present).
        cleaned = re.sub(
            r"\s*(by|on|before|no later than)\s+[\w\s,]+$",
            "",
            name,
            flags=re.IGNORECASE,
        ).strip()
        return cleaned or name

    @staticmethod
    def _build_description(item: Dict[str, Any]) -> str:
        return (item.get("description") or item.get("name") or "").split(" | ")[0].strip()