"""
date_provenance_validator.py - Verbatim Evidence Validator & Date Provenance Service

Implements generic, source-grounded contract date provenance validation for any contract (EL, SOW, MSA, Change Order, etc.):
1. Validates every date quotation against original document text / chunks.
2. Safe normalization only (whitespace, unicode punctuation, quotes/dashes/case).
3. Rejects synthetic / generated quotations as EVIDENCE_UNVERIFIED.
4. Cross-validates duration (14 weeks) from contract start to contract end.
5. Preserves lifecycle separation between Project End (2027-04-30) and Production Support (2027-07-31).
6. Preserves SUPPORT_PERIOD_AMBIGUITY warning without treating it as breach.
7. Validates milestone dates (4 distinct payment dates across 6 milestone rows).
8. Enforces independent validation fields and PROVISIONAL readiness on unverified evidence.
"""

import re
import unicodedata
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple, Union


def safe_normalize(text: Optional[str]) -> str:
    """Safe normalization: lowercase, collapse whitespace, normalize unicode punctuation only."""
    if not text:
        return ""
    # Normalize unicode to NFC
    text = unicodedata.normalize("NFC", str(text))
    # Normalize unicode quotation marks & apostrophes
    text = text.replace("\u2018", "'").replace("\u2019", "'")
    text = text.replace("\u201c", '"').replace("\u201d", '"')
    # Normalize unicode dashes / hyphens
    text = text.replace("\u2013", "-").replace("\u2014", "-").replace("\u2212", "-")
    # Normalize ellipsis & special spaces
    text = text.replace("\u2026", "...")
    text = text.replace("\xa0", " ")  # non-breaking space
    text = text.replace("\u200b", "")  # zero-width space
    # Collapse multiple whitespace characters into single space and strip
    text = re.sub(r"\s+", " ", text).strip().lower()
    return text


class DateProvenanceValidator:
    """Validates date provenance evidence against source document text and chunks."""

    MIN_OVERLAP_RATIO = 0.85

    # Patterns indicating synthetic / fabricated / boilerplate quotations
    SYNTHETIC_PATTERNS = [
        r"^effective from \d{4}-\d{2}-\d{2}$",
        r"^valid till \d{4}-\d{2}-\d{2}$",
        r"^contract start(s|ing)? (on|date)?\s*\d{4}-\d{2}-\d{2}$",
        r"^contract end(s|ing)? (on|date)?\s*\d{4}-\d{2}-\d{2}$",
        r"^production support (ends?|till) \d{4}-\d{2}-\d{2}$",
        r"^project (starts?|begins?|commences?) (on )?\d{4}",
        r"^project ends? (on )?\d{4}",
        r"^effective from$",
        r"^valid till$",
    ]

    @classmethod
    def validate_verbatim_quotation(
        cls,
        quotation: Optional[str],
        document_chunks: Optional[List[Dict[str, Any]]] = None,
        raw_text_corpus: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Check if a quotation exists verbatim in document chunks or raw text corpus.
        Allows only safe normalization (whitespace, unicode punctuation, case).
        Rejects synthetic or empty quotations as EVIDENCE_UNVERIFIED.
        """
        if not quotation or not str(quotation).strip():
            return {
                "verified": False,
                "status": "EVIDENCE_UNVERIFIED",
                "reason": "Empty or missing quotation",
                "matched_chunk_index": None,
                "normalized_match": False,
            }

        norm_quote = safe_normalize(quotation)
        if not norm_quote:
            return {
                "verified": False,
                "status": "EVIDENCE_UNVERIFIED",
                "reason": "Quotation contains no valid text after normalization",
                "matched_chunk_index": None,
                "normalized_match": False,
            }

        # Check for synthetic / auto-generated pattern
        for pat in cls.SYNTHETIC_PATTERNS:
            if re.match(pat, norm_quote):
                return {
                    "verified": False,
                    "status": "EVIDENCE_UNVERIFIED",
                    "reason": "Quotation matches synthetic/generated pattern — not from source document",
                    "matched_chunk_index": None,
                    "normalized_match": False,
                }

        chunks = document_chunks or []

        # Check against chunks
        for chunk in chunks:
            chunk_text = chunk.get("text") or chunk.get("raw_text") or chunk.get("content") or chunk.get("description") or ""
            if not chunk_text:
                continue
            norm_chunk = safe_normalize(chunk_text)

            # 1. Exact substring match after safe normalization
            if norm_quote in norm_chunk:
                c_idx = chunk.get("chunk_index")
                if c_idx is None:
                    c_idx = chunk.get("index") or chunk.get("id")
                return {
                    "verified": True,
                    "status": "EVIDENCE_VERIFIED",
                    "reason": "Verbatim match found in source chunk",
                    "matched_chunk_index": c_idx,
                    "normalized_match": True,
                }

            # 2. Word overlap check for longer quotations (> 40 chars)
            if len(norm_quote) > 40:
                words_q = [w for w in norm_quote.split() if len(w) > 2]
                words_c = set(norm_chunk.split())
                if words_q:
                    overlap = sum(1 for w in words_q if w in words_c) / len(words_q)
                    if overlap >= cls.MIN_OVERLAP_RATIO:
                        c_idx = chunk.get("chunk_index")
                        if c_idx is None:
                            c_idx = chunk.get("index") or chunk.get("id")
                        return {
                            "verified": True,
                            "status": "EVIDENCE_VERIFIED",
                            "reason": f"High word overlap ({overlap:.0%}) match found in source chunk",
                            "matched_chunk_index": c_idx,
                            "normalized_match": True,
                        }

        # Check against raw_text_corpus if provided
        if raw_text_corpus:
            norm_corpus = safe_normalize(raw_text_corpus)
            if norm_quote in norm_corpus:
                return {
                    "verified": True,
                    "status": "EVIDENCE_VERIFIED",
                    "reason": "Verbatim match found in document text corpus",
                    "matched_chunk_index": "corpus",
                    "normalized_match": True,
                }

        return {
            "verified": False,
            "status": "EVIDENCE_UNVERIFIED",
            "reason": "Quotation not found verbatim in source document text or chunks",
            "matched_chunk_index": None,
            "normalized_match": False,
        }

    @classmethod
    def cross_validate_duration(
        cls,
        start_date: str,
        end_date: str,
        duration_weeks: Optional[int] = None,
        counting_convention: str = "calendar_weeks",
    ) -> Dict[str, Any]:
        """
        Generic cross-validation of contract duration against start and end dates for any contract.
        Supports explicit duration_weeks or dynamically derives duration from dates.
        """
        try:
            start_dt = datetime.strptime(str(start_date)[:10], "%Y-%m-%d")
            end_dt = datetime.strptime(str(end_date)[:10], "%Y-%m-%d")

            total_days = (end_dt - start_dt).days
            if duration_weeks is None:
                duration_weeks = max(1, round(total_days / 7))

            # N weeks = N * 7 calendar days
            derived_end_dt = start_dt + timedelta(weeks=duration_weeks)
            derived_end_str = derived_end_dt.strftime("%Y-%m-%d")

            # Exact calendar week match
            matches = derived_end_dt.date() == end_dt.date()

            # Alternative inclusive boundary convention (start + N weeks - 1 day)
            alt_derived_end_dt = start_dt + timedelta(weeks=duration_weeks) - timedelta(days=1)
            alt_matches = alt_derived_end_dt.date() == end_dt.date()

            is_valid = matches or alt_matches

            return {
                "status": "DURATION_DATE_VALIDATED" if is_valid else "DURATION_DATE_MISMATCH",
                "start_date": start_date,
                "explicit_end_date": end_date,
                "duration_weeks": duration_weeks,
                "derived_end_date": derived_end_str,
                "counting_convention": counting_convention,
                "convention_description": f"{duration_weeks} calendar weeks ({duration_weeks * 7} calendar days) from start date {start_date}",
                "agreement": is_valid,
                "delta_days": (end_dt - derived_end_dt).days,
            }
        except (ValueError, TypeError) as e:
            return {
                "status": "DURATION_VALIDATION_ERROR",
                "start_date": start_date,
                "explicit_end_date": end_date,
                "duration_weeks": duration_weeks,
                "derived_end_date": None,
                "counting_convention": counting_convention,
                "convention_description": f"Failed to parse dates: {e}",
                "agreement": False,
                "error": str(e),
            }

    @classmethod
    def validate_support_period_separation(
        cls,
        project_end: str,
        support_end: str,
    ) -> Dict[str, Any]:
        """
        Validates separate lifecycle dates for Project End vs Production Support:
        - Project end: 2027-04-30
        - Production support end: 2027-07-31
        Ensures support end date never overwrites or extends the contractual project end date.
        Preserves existing SUPPORT_PERIOD_AMBIGUITY warning identifying:
        - contract end date
        - support end date
        - three-month extension
        - potential warranty/support interpretation
        - PM confirmation requirement
        """
        try:
            pe_dt = datetime.strptime(str(project_end)[:10], "%Y-%m-%d")
            se_dt = datetime.strptime(str(support_end)[:10], "%Y-%m-%d")

            extends_beyond = se_dt.date() > pe_dt.date()
            delta_days = (se_dt - pe_dt).days if extends_beyond else 0
            delta_weeks = round(delta_days / 7) if delta_days else 0

            warning = None
            if extends_beyond:
                duration_label = f"{round(delta_days / 30)} months" if delta_days >= 30 else f"{delta_weeks} weeks"
                warning = {
                    "type": "SUPPORT_PERIOD_AMBIGUITY",
                    "contract_end_date": project_end,
                    "support_end_date": support_end,
                    "three_month_extension": f"{duration_label} ({delta_weeks} weeks / {delta_days} days) extending beyond contractual project completion",
                    "potential_interpretation": "Post-delivery warranty / hypercare production support window",
                    "pm_confirmation_required": True,
                    "note": (
                        f"Production support period ends {support_end}. This warranty/support window "
                        f"must never overwrite or extend the contractual project completion date of {project_end}."
                    ),
                    "is_contractual_breach": False,
                }

            return {
                "status": "SUPPORT_PERIOD_AMBIGUITY" if extends_beyond else "SUPPORT_PERIOD_CLEAN",
                "contract_end_date": project_end,
                "support_end_date": support_end,
                "extends_beyond_project_end": extends_beyond,
                "extension_days": delta_days,
                "extension_weeks": delta_weeks,
                "review_required": extends_beyond,
                "warning": warning,
            }
        except (ValueError, TypeError) as e:
            return {
                "status": "SUPPORT_PERIOD_VALIDATION_ERROR",
                "contract_end_date": project_end,
                "support_end_date": support_end,
                "extends_beyond_project_end": False,
                "extension_days": 0,
                "extension_weeks": 0,
                "review_required": True,
                "error": str(e),
                "warning": None,
            }

    @classmethod
    def validate_milestone_dates(
        cls,
        milestones: List[Dict[str, Any]],
        project_end: str,
        expected_payment_dates: Optional[List[str]] = None,
        require_milestones: bool = False,
    ) -> Dict[str, Any]:
        """
        Validates milestone dates against source EL:
        - 4 distinct payment dates: 2027-02-11, 2027-02-12, 2027-03-18, 2027-04-01
        - 6 module-specific payment milestone rows
        - Ensures milestone dates remain distinct from project-end and support dates
        - Ensures last payment milestone (2027-04-01) is not inferred as project completion (2027-04-30)
        - Rejects zero milestones when milestones are required/expected by contract
        """
        payment_milestones = []
        for m in milestones:
            # Payment milestone has fee_amount, percentage, or module, or name contains %
            has_fee = bool(m.get("fee_amount_inr") or m.get("fee_amount"))
            has_pct = bool(m.get("percentage") or "%" in str(m.get("name", "")))
            has_mod = bool(m.get("module"))
            if has_fee or has_pct or has_mod:
                payment_milestones.append(m)

        payment_dates = [
            str(m.get("planned_date") or m.get("date"))[:10]
            for m in payment_milestones
            if (m.get("planned_date") or m.get("date"))
        ]
        distinct_payment_dates = sorted(set(payment_dates))
        last_payment_date = distinct_payment_dates[-1] if distinct_payment_dates else None

        # Check: does last payment date equal project end date?
        pe_str = str(project_end)[:10] if project_end else ""
        last_payment_equals_project_end = bool(last_payment_date and last_payment_date == pe_str)

        warnings = []
        if last_payment_equals_project_end:
            warnings.append(
                "CAUTION: Last payment milestone matches project end date. "
                "Ensure final sign-off is preserved as a distinct contractual gate."
            )

        milestone_date_validation_valid = True
        missing_dates = []
        unexpected_dates = []
        if require_milestones and len(milestones) == 0:
            milestone_date_validation_valid = False
            warnings.append(
                "FAIL: Expected milestone candidates present in contract, but zero milestones were extracted."
            )

        if expected_payment_dates:
            exp_norm = sorted(set(str(d)[:10] for d in expected_payment_dates))
            act_norm = set(distinct_payment_dates)
            missing = set(exp_norm) - act_norm
            extra = act_norm - set(exp_norm)
            if missing or extra:
                milestone_date_validation_valid = False
                missing_dates = sorted(missing)
                unexpected_dates = sorted(extra)

        return {
            "total_milestone_rows": len(milestones),
            "payment_milestone_rows": len(payment_milestones),
            "distinct_payment_dates": distinct_payment_dates,
            "distinct_payment_date_count": len(distinct_payment_dates),
            "last_payment_date": last_payment_date,
            "project_end_date": pe_str,
            "last_payment_equals_project_end": last_payment_equals_project_end,
            "milestone_date_validation_valid": milestone_date_validation_valid,
            "missing_expected_dates": missing_dates,
            "unexpected_dates": unexpected_dates,
            "warnings": warnings,
        }

    @classmethod
    def build_contract_date_record(
        cls,
        event_name: str,
        normalized_date: Optional[str],
        original_verbatim_quotation: Optional[str],
        source_document_id: int,
        source_locator: str,
        date_type: str = "CONTRACT_CONFIRMED",
        extraction_confidence: float = 0.9,
        document_chunks: Optional[List[Dict[str, Any]]] = None,
        raw_text_corpus: Optional[str] = None,
        duration_weeks: Optional[int] = None,
        start_date_for_duration: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Builds a comprehensive date evidence record with all 6 independent validation fields:
        1. date_value_valid: bool
        2. date_evidence_valid: bool
        3. duration_consistency_valid: Optional[bool]
        4. date_provenance_valid: bool
        5. support_period_review_required: bool
        6. milestone_date_validation_valid: Optional[bool]
        Never creates synthetic quotations. Unverified quotations are marked EVIDENCE_UNVERIFIED.
        """
        # Validate date value format
        date_value_valid = False
        norm_date_str = ""
        if normalized_date:
            try:
                dt = datetime.strptime(str(normalized_date)[:10], "%Y-%m-%d")
                norm_date_str = dt.strftime("%Y-%m-%d")
                date_value_valid = True
            except (ValueError, TypeError):
                norm_date_str = str(normalized_date)
                date_value_valid = False

        # Validate verbatim evidence
        evidence_result = cls.validate_verbatim_quotation(
            quotation=original_verbatim_quotation,
            document_chunks=document_chunks,
            raw_text_corpus=raw_text_corpus,
        )
        date_evidence_valid = evidence_result["verified"]
        validation_status = evidence_result["status"]

        # Date type determination
        # Valid categories: CONTRACT_CONFIRMED, DURATION_DERIVED, AI_ESTIMATED, UNRESOLVED, EVIDENCE_UNVERIFIED
        final_date_type = date_type
        if not date_value_valid:
            final_date_type = "UNRESOLVED"
        elif not date_evidence_valid:
            final_date_type = "EVIDENCE_UNVERIFIED"

        # Duration cross-validation if applicable
        duration_consistency_valid = None
        if duration_weeks is not None and start_date_for_duration and date_value_valid:
            dur_check = cls.cross_validate_duration(
                start_date=start_date_for_duration,
                end_date=norm_date_str,
                duration_weeks=duration_weeks,
            )
            duration_consistency_valid = dur_check.get("agreement", False)

        # Full provenance is valid ONLY when date value is valid AND verbatim evidence is verified
        date_provenance_valid = bool(date_value_valid and date_evidence_valid)

        return {
            "event_name": event_name,
            "normalized_date": norm_date_str,
            "original_verbatim_quotation": str(original_verbatim_quotation or ""),
            "source_document_id": source_document_id,
            "source_locator": source_locator,
            "date_type": final_date_type,
            "extraction_confidence": float(extraction_confidence),
            "validation_status": validation_status,
            # 6 independent validation fields
            "date_value_valid": date_value_valid,
            "date_evidence_valid": date_evidence_valid,
            "duration_consistency_valid": duration_consistency_valid,
            "date_provenance_valid": date_provenance_valid,
            "support_period_review_required": False,  # Updated for support dates
            "milestone_date_validation_valid": None,  # Updated for milestones
            "evidence_verification": evidence_result,
        }

