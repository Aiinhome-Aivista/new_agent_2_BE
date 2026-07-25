import json
from services.llm_service import LLMService


class ActivityExtractorAgent:
    @classmethod
    def extract_activities(cls, document_text: str) -> list:
        """
        STEP 1: Single-pass extraction.
        Parses the document ONCE and returns a clean list of project activities.
        The LLM does NOT evaluate risk here — it only extracts.
        """
        prompt = f"""You are the Activity Extractor Agent.
Read the following project document (MOM / Status Report) and extract every work activity, 
action item, deliverable update, or progress mention.

=== DOCUMENT ===
{document_text}

Rules:
- Ignore greetings, attendance lists, signatures, agenda headings, and approvals.
- Normalize activity names (remove filler words, clean up).
- Remove duplicates.

Output MUST be valid JSON:
{{
  "activities": [
    {{
      "activity": "CRM Integration",
      "source_sentence": "The team mentioned CRM Integration is delayed due to missing APIs.",
      "confidence": 90
    }}
  ]
}}
"""
        result = LLMService.generate_json(prompt)
        return result.get("activities", [])


class BatchActivityRiskAgent:
    @classmethod
    def evaluate_batch(cls, activities_with_contexts: list) -> list:
        """
        STEP 4: Batch risk evaluation.
        Evaluates ALL ambiguous activities in a SINGLE LLM call.
        Each activity gets its own compact context built from ChromaDB + MySQL.
        This is the key token optimization — N activities = 1 LLM call, not N.
        
        Input format:
          [{"activity": "...", "context": "Scope Item: ...\nDeadline: ...\n..."}, ...]
        Output format:
          [{"activity": "...", "risk_category": "...", "risk_level": "...", "reasoning": "...", "matched_baseline_item": "..."}, ...]
        """
        if not activities_with_contexts:
            return []

        activities_block = ""
        for i, item in enumerate(activities_with_contexts, 1):
            activities_block += f"""
--- Activity {i} ---
Activity Name: {item['activity']}
Baseline Context:
{item['context']}
"""

        prompt = f"""You are the Activity Risk Evaluation Agent.
Evaluate each of the following project activities for risk using ONLY their provided baseline context.

{activities_block}

For EACH activity, determine:
- SCOPE_CREEP: Activity is NOT in the baseline or is an excluded item.
- DELAY: Activity is behind schedule or past its deadline.
- DEPENDENCY: Activity is blocked waiting for a third party or client.
- BLOCKED: Activity is explicitly blocked.
- NONE: Activity is on-track and within scope.

Output MUST be a valid JSON array with one entry per activity, in the SAME ORDER as provided:
[
  {{
    "activity": "Activity name exactly as given",
    "risk_category": "SCOPE_CREEP|DELAY|DEPENDENCY|BLOCKED|NONE",
    "risk_level": "LOW|MEDIUM|HIGH|CRITICAL",
    "reasoning": "Short explanation based ONLY on the context provided.",
    "matched_baseline_item": "Name of matched baseline item, or null"
  }}
]
"""
        result = LLMService.generate_json(prompt)
        # Result could be a list or dict with a key
        if isinstance(result, list):
            return result
        # Fallback: try common wrapper keys
        for key in ["activities", "results", "evaluations"]:
            if key in result:
                return result[key]
        return []
