"""
DocumentQualityChecker
-----------------------
Pipeline Step 0 (runs BEFORE section detection / candidate extraction).

Purpose:
    For unstructured / arbitrary EL or IFA documents, quickly decide whether the
    document actually contains scope-of-work / deliverable / deadline language
    at all, BEFORE running the full (expensive) extraction pipeline.

    This lets the API return a clear, honest signal such as:
        "This document does not appear to contain scope-of-work details"
    instead of silently producing a near-empty or garbage baseline.

Integration point (api/routers/baseline.py -> extract_baseline):

    chunks = DocumentService.parse_document(doc["storage_key"], ext)

    # Pipeline Step 0: Document quality / relevance pre-check
    quality_check = DocumentQualityChecker.check(chunks)
    if not quality_check["has_scope_content"]:
        return {
            "success": True,
            "message": "Document does not appear to contain scope-of-work details",
            "data": {
                "baseline_id": None,
                "extraction_quality": {
                    "status": "NO_SCOPE_CONTENT",
                    "reason": quality_check["reasoning"],
                }
            }
        }

    # ... continue with existing Step 1 (ScopeSectionDetector) as before
"""

import json
import re
from typing import List, Dict, Any

from services.llm_service import LLMService


class DocumentQualityChecker:
    """Cheap, single-call pre-check on the whole document (not per-candidate)."""

    # Hard cap on how much raw text we send to the LLM for the pre-check.
    # We only need a representative sample, not the full document, to judge
    # whether scope/deliverable language is present.
    MAX_CHARS_FOR_PRECHECK = 6000

    # If the document has fewer than this many non-whitespace characters,
    # skip the LLM call entirely and short-circuit as NO_SCOPE_CONTENT.
    MIN_CHARS_REQUIRED = 40

    SYSTEM_PROMPT = (
        "You are a contract-analysis assistant. You will be shown raw text "
        "extracted from a business document (which may or may not be an "
        "Engagement Letter / IFA / SOW). Decide whether it contains any of:\n"
        "1) Vendor/contractor scope-of-work commitments (things a vendor will "
        "build, deliver, implement, or perform)\n"
        "2) Explicit out-of-scope / exclusion statements\n"
        "3) Deliverables, milestones, or deadlines (dates, durations, "
        "'by <date>', 'within N days', go-live dates, etc.)\n\n"
        "Respond with ONLY a JSON object, no markdown, no prose:\n"
        '{"has_scope_content": bool, "has_deliverable_content": bool, '
        '"reasoning": "<one short sentence>"}'
    )

    @classmethod
    def check(cls, chunks: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Args:
            chunks: output of DocumentService.parse_document(...) - list of
                    dicts, each expected to have a 'text' field (matches the
                    shape already consumed downstream by ScopeSectionDetector).

        Returns:
            {
                "has_scope_content": bool,
                "has_deliverable_content": bool,
                "reasoning": str
            }
        """
        raw_text = cls._flatten_chunks(chunks)
        stripped = raw_text.strip()

        if len(stripped) < cls.MIN_CHARS_REQUIRED:
            return {
                "has_scope_content": False,
                "has_deliverable_content": False,
                "reasoning": "Document contains little to no extractable text.",
            }

        sample = stripped[: cls.MAX_CHARS_FOR_PRECHECK]

        try:
            result = cls._llm_check(sample)
        except Exception as e:
            # Fail-open with a deterministic fallback so a flaky LLM call
            # doesn't block the whole pipeline. Falls back to a cheap
            # keyword heuristic instead of blocking baseline creation.
            print(f"Warning: DocumentQualityChecker LLM call failed, falling back to heuristic: {e}")
            result = cls._heuristic_check(sample)

        return result

    @staticmethod
    def _flatten_chunks(chunks: List[Dict[str, Any]]) -> str:
        return "\n".join(c.get("text", "") for c in chunks if c.get("text"))

    @classmethod
    def _llm_check(cls, sample_text: str) -> Dict[str, Any]:
        prompt = f"""{cls.SYSTEM_PROMPT}

Document Sample:
{sample_text}
"""
        parsed = LLMService.generate_json(prompt)

        return {
            "has_scope_content": bool(parsed.get("has_scope_content", False)),
            "has_deliverable_content": bool(parsed.get("has_deliverable_content", False)),
            "reasoning": parsed.get("reasoning", "").strip() or "No reasoning provided.",
        }

    @staticmethod
    def _heuristic_check(sample_text: str) -> Dict[str, Any]:
        """
        Deterministic fallback used only if the LLM call fails. Intentionally
        permissive (has_scope_content=True by default) so a transient LLM
        outage never blocks a legitimate extraction — it only guards against
        LLM downtime, not against genuinely empty documents (that case is
        already caught earlier by MIN_CHARS_REQUIRED).
        """
        lowered = sample_text.lower()
        scope_signals = ["shall", "scope of work", "deliverable", "vendor", "contractor", "responsib"]
        deadline_signals = ["by ", "within ", "deadline", "due date", "go-live", "go live", "milestone"]

        has_scope = any(s in lowered for s in scope_signals)
        has_deliverable = any(s in lowered for s in deadline_signals)

        return {
            "has_scope_content": has_scope,
            "has_deliverable_content": has_deliverable,
            "reasoning": (
                "Determined via keyword heuristic fallback (LLM pre-check unavailable)."
            ),
        }