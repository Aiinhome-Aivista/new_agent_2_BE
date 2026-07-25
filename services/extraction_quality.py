"""
extraction_quality.py
----------------------
Aggregate-level quality check that runs AFTER the pipeline has produced
enriched_candidates, to decide what confidence signal to surface to the
frontend (separate from the Step-0 DocumentQualityChecker pre-check, which
runs BEFORE the pipeline and catches "not an EL at all" cases).

Two distinct situations are being separated on purpose:
    - Step 0 (DocumentQualityChecker): "this document has no scope language
      at all" -> skip the pipeline entirely.
    - This module (post-pipeline): "the pipeline ran, but what it found is
      thin / ambiguous" -> still create the draft baseline, but flag it so
      reviewers know to check it manually rather than trusting it blindly.

Integration point (api/routers/baseline.py -> extract_baseline), after
enriched_candidates and extracted_deliverables both exist:

    quality = evaluate_extraction_quality(enriched_candidates, extracted_deliverables)
    ...
    return {
        "success": True,
        "message": "Draft baseline extracted",
        "data": {
            "baseline_id": baseline_id,
            "extraction_quality": quality,
        }
    }
"""

from typing import List, Dict, Any

# Tune these thresholds against real documents once you have a labeled sample set.
LOW_CONFIDENCE_RATIO_THRESHOLD = 0.7
UNCERTAIN_TYPE_RATIO_THRESHOLD = 0.5
LOW_CONFIDENCE_CUTOFF = 0.6


def evaluate_extraction_quality(
    enriched_candidates: List[Dict[str, Any]],
    extracted_deliverables: List[Dict[str, Any]],
) -> Dict[str, Any]:

    if not enriched_candidates:
        return {
            "status": "NO_CONTENT",
            "reason": "No scope-related content detected in document.",
            "deliverable_note": "No deadlines or deliverable milestones found in this document.",
        }

    total = len(enriched_candidates)
    low_conf = [c for c in enriched_candidates if (c.get("confidence") or 0) < LOW_CONFIDENCE_CUTOFF]
    uncertain_type = [c for c in enriched_candidates if c.get("scope_type") == "UNCERTAIN"]

    deliverable_note = None
    if not extracted_deliverables:
        deliverable_note = "No deadlines or deliverable milestones found in this document."

    if len(low_conf) / total > LOW_CONFIDENCE_RATIO_THRESHOLD:
        return {
            "status": "LOW_CONFIDENCE",
            "reason": (
                f"{len(low_conf)}/{total} items were extracted with low confidence — "
                f"document may lack clear scope/deliverable language."
            ),
            "deliverable_note": deliverable_note,
        }

    if len(uncertain_type) / total > UNCERTAIN_TYPE_RATIO_THRESHOLD:
        return {
            "status": "AMBIGUOUS",
            "reason": (
                "Document structure was unclear — could not reliably classify "
                "in-scope vs out-of-scope for a majority of items."
            ),
            "deliverable_note": deliverable_note,
        }

    return {
        "status": "OK",
        "reason": None,
        "deliverable_note": deliverable_note,
    }