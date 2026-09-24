"""
IMPROVEMENT 3 (applied): LangChain Structured Output for extraction agents.

ActivityExtractorAgent and BatchActivityRiskAgent now call
LLMService.generate_structured() with their respective Pydantic schemas.
This enforces JSON structure at the token level and eliminates all
manual json.loads() / regex parsing failures.

COMPATIBILITY GUARANTEE:
- All output dict keys are IDENTICAL to what the pipeline already reads.
- The structured path produces the same data shape as the old generate_json() path.
- Falls back to generate_json() automatically if structured output is unavailable.
"""

import json
from services.llm_service import LLMService


class ActivityExtractorAgent:
    @classmethod
    def extract_activities(cls, document_text: str, active_tracker_block: str = "None") -> dict:
        """
        STEP 1: Single-pass activity extraction with structured output.

        The Risk Tracker is a contractual monitoring system, not an activity log.
        Every tracker item must represent a contractual deliverable or scope request —
        not the sentence from the meeting minutes.

        Returns a dict with keys:
          - raw_activities: list of extracted activity dicts
          - activities:     same list (pipeline compatibility alias)
          - extractions:    same list (legacy fallback key)
          - resolved_items: list of resolved/completed items
        """
        from agents.llm_schemas import ExtractionOutput
        from core.prompts import get_activity_extractor_prompt

        prompt = get_activity_extractor_prompt(document_text, active_tracker_block)

        try:
            result = LLMService.generate_structured(
                prompt, ExtractionOutput, fallback_key='raw_activities'
            )

            # Normalize to the dict shape the rest of the pipeline expects
            if isinstance(result, ExtractionOutput):
                raw_activities = [item.model_dump() for item in result.raw_activities]
                resolved_items = [item.model_dump() for item in result.resolved_items]
            elif isinstance(result, dict):
                # Fallback path returned a dict
                raw_activities = result.get('raw_activities') or result.get('activities') or result.get('extractions') or []
                resolved_items = result.get('resolved_items', [])
            else:
                raw_activities = []
                resolved_items = []

        except Exception as e:
            print(f"[ActivityExtractorAgent] generate_structured error: {e}. Falling back to generate_json.")
            fallback = LLMService.generate_json(prompt)
            raw_activities = (
                fallback.get('extractions') or
                fallback.get('activities') or
                fallback.get('raw_activities') or []
            )
            resolved_items = fallback.get('resolved_items', [])

        # Normalise: ensure every item has both 'activity' and 'statement' keys
        for item in raw_activities:
            if isinstance(item, dict):
                if not item.get('activity') and item.get('statement'):
                    item['activity'] = item['statement']
                if not item.get('statement') and item.get('activity'):
                    item['statement'] = item['activity']

        return {
            'raw_activities': raw_activities,  # new canonical key
            'activities':     raw_activities,  # pipeline compatibility
            'extractions':    raw_activities,  # legacy fallback key
            'resolved_items': resolved_items,
        }

    @classmethod
    def extract(cls, document_text: str, scope_items: list = None,
                active_tracker_items: list = None) -> dict:
        """
        Alias used by evaluation_graph.py node_extract_activities.
        Builds the active_tracker_block string from active_tracker_items.
        """
        active_tracker_block = "None"
        if active_tracker_items:
            active_tracker_block = "\n".join(
                [f"- {it.get('title', '')}" for it in active_tracker_items]
            ) or "None"
        return cls.extract_activities(document_text, active_tracker_block)


class BatchActivityRiskAgent:
    @classmethod
    def evaluate_batch(cls, activities_with_contexts: list, milestone_progress_block: str = "") -> list:
        """
        PHASE 1: Batch risk diagnosis with structured output.

        Evaluates ALL ambiguous activities in a SINGLE LLM call.
        The LLM diagnoses risk category, level, execution status, and confidence.
        Scoring happens in Phase 2 (RiskScoringEngine) — NOT here.

        Returns a plain list of dicts for downstream RiskScoringEngine compatibility.
        """
        if not activities_with_contexts:
            return []

        activities_block = ""
        for i, item in enumerate(activities_with_contexts, 1):
            activities_block += f"""
--- Activity {i} ---
Activity Name: {item.get('activity', '')}
Original MoM Evidence: {item.get('source_sentence', item.get('activity', ''))}
Baseline Context:
{item.get('context', '')}
"""

        from agents.llm_schemas import RiskEvaluationOutput
        from core.prompts import get_batch_activity_risk_prompt

        prompt = get_batch_activity_risk_prompt(milestone_progress_block, activities_block)

        try:
            result = LLMService.generate_structured(
                prompt, RiskEvaluationOutput, fallback_key='items'
            )

            if isinstance(result, RiskEvaluationOutput):
                return [item.model_dump() for item in result.items]
            elif isinstance(result, dict):
                # Fallback path
                for key in ['items', 'activities', 'results', 'evaluations', 'evaluated_activities']:
                    if key in result and isinstance(result[key], list):
                        return result[key]
                for val in result.values():
                    if isinstance(val, list):
                        return val
            elif isinstance(result, list):
                return result

        except Exception as e:
            print(f"[BatchActivityRiskAgent] generate_structured error: {e}. Falling back to generate_json.")

        # Ultimate fallback: original generate_json() path
        raw = LLMService.generate_json(prompt)
        if isinstance(raw, list):
            return raw
        if isinstance(raw, dict):
            for key in ['activities', 'results', 'evaluations', 'evaluated_activities', 'items']:
                if key in raw and isinstance(raw[key], list):
                    return raw[key]
            for val in raw.values():
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

        NOTE: This call produces prose + structured progress — not pure JSON schema.
        It is intentionally left on generate_json() (not structured output).
        """
        if not approved_baseline_items:
            return []

        baseline_block = ""
        for item in approved_baseline_items:
            baseline_block += f"- ID: {item.get('id', 'Unknown')} | Deliverable: {item.get('name', 'Unknown')}\n"

        risk_block = ""
        try:
            risk_block = json.dumps(risk_eval_output, indent=2)
        except Exception:
            risk_block = str(risk_eval_output)

        from core.prompts import get_deliverable_timeline_evaluation_prompt
        prompt = get_deliverable_timeline_evaluation_prompt(baseline_block, risk_block, document_text)
        result = LLMService.generate_json(prompt)
        return result.get("progress_records", [])
