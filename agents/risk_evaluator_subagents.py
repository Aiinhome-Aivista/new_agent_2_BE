import json
from services.llm_service import LLMService


class ActivityExtractorAgent:
    @classmethod
    def extract_activities(cls, document_text: str, active_tracker_block: str = "None") -> list:
        """
        STEP 1: Single-pass extraction.
        
        The Risk Tracker is a contractual monitoring system, not an activity log.
        Every tracker item must represent a contractual deliverable or scope request —
        not the sentence from the meeting minutes.

        Therefore this extractor returns:
          - A normalized business entity name (e.g. "SAP Integration", not "Evaluate SAP Integration Request")
          - The original source sentence preserved as evidence
          - Confidence score

        Normalization rules applied by the LLM:
          - Strip action verbs: "Evaluate", "Review", "Prepare", "Discuss", "Assess", "Propose"
          - Strip request/proposal/proposal noise words: "Request", "Proposal", "Assessment", "Development", "Implementation"
          - Strip customer responsibility preambles: "Customer shall provide", "Client must supply"
          - Strip date suffixes from deliverable names (dates are metadata, not titles)
          - Merge semantically equivalent activities into a single business entity
        """
        from core.prompts import get_activity_extractor_prompt
        prompt = get_activity_extractor_prompt(document_text, active_tracker_block)
        result = LLMService.generate_json(prompt)
        extractions = result.get("extractions", [])
        # The extraction prompt uses `statement` as the primary key;
        # normalise to `activity` so the downstream pipeline always finds one key.
        for item in extractions:
            if not item.get("activity") and item.get("statement"):
                item["activity"] = item["statement"]
        return {
            "activities": extractions,        # primary key read by risk_evaluation_agent
            "extractions": extractions,       # legacy fallback key
            "resolved_items": result.get("resolved_items", [])
        }


class BatchActivityRiskAgent:
    @classmethod
    def evaluate_batch(cls, activities_with_contexts: list, milestone_progress_block: str = "") -> list:
        """
        PHASE 1: Batch risk diagnosis.
        Evaluates ALL ambiguous activities in a SINGLE LLM call.

        The LLM's job here is DIAGNOSIS ONLY — it identifies:
          - What category of risk exists (SCOPE_CREEP / DELAY / DEPENDENCY / BLOCKED / NONE)
          - Which diagnostic signals are present (deadline_missed, customer_dependency, etc.)
          - What the business impact level is (LOW / MEDIUM / HIGH)
          - How confident it is (0.0–1.0)

        The LLM does NOT produce a numeric score.
        Scoring happens in Phase 2 (RiskScoringEngine) using deterministic weighted rules.

        KEY RULE — Tracker Title Priority:
        1. If a matched_baseline_item exists in the approved IN_SCOPE baseline → use that as title.
        2. If it matches an OUT_OF_SCOPE/excluded item → use that as title.
        3. Only if NO baseline match exists → use the normalized activity name as title.

        CRITICAL: An approved IN_SCOPE baseline item can NEVER be classified as SCOPE_CREEP.
        """
        if not activities_with_contexts:
            return []

        activities_block = ""
        for i, item in enumerate(activities_with_contexts, 1):
            activities_block += f"""
--- Activity {i} ---
Activity Name: {item['activity']}
Original MoM Evidence: {item.get('source_sentence', item['activity'])}
Baseline Context:
{item['context']}
"""

        from core.prompts import get_batch_activity_risk_prompt
        prompt = get_batch_activity_risk_prompt(milestone_progress_block, activities_block)
        result = LLMService.generate_json(prompt)
        if isinstance(result, list):
            return result
        # Robust fallback: if the LLM wrapped the array in an object (e.g. {"evaluated_activities": [...]})
        if isinstance(result, dict):
            # First check common keys
            for key in ["activities", "results", "evaluations", "evaluated_activities"]:
                if key in result and isinstance(result[key], list):
                    return result[key]
            # If not found, just return the first list we find in the dict values
            for val in result.values():
                if isinstance(val, list):
                    return val
        return []


class DeliverableTimelineEvaluationAgent:
    @classmethod
    def evaluate_progress(cls, approved_baseline_items: list, document_text: str, risk_eval_output: list) -> list:
        """
        Extracts deliverable progress from the MoM/Status Report.
        Must strictly adhere to the rule of NEVER inventing percentages.
        Consolidates multiple references into a single progress record per baseline item.
        """
        if not approved_baseline_items:
            return []

        baseline_block = ""
        for item in approved_baseline_items:
            baseline_block += f"- ID: {item.get('id', 'Unknown')} | Deliverable: {item.get('name', 'Unknown')}\n"

        risk_block = ""
        import json
        try:
            risk_block = json.dumps(risk_eval_output, indent=2)
        except Exception:
            risk_block = str(risk_eval_output)

        from core.prompts import get_deliverable_timeline_evaluation_prompt
        prompt = get_deliverable_timeline_evaluation_prompt(baseline_block, risk_block, document_text)
        result = LLMService.generate_json(prompt)
        return result.get("progress_records", [])

