import json
from services.llm_service import LLMService

class MilestoneDependencyExtractor:
    @classmethod
    def extract_dependencies(cls, milestones: list[dict], document_text: str) -> list[dict]:
        """
        Attempts to extract explicit dependencies between the provided milestones from the document text.
        """
        if not milestones:
            return []
            
        milestone_names = [m.get("milestone_normalized", m.get("milestone")) for m in milestones if m.get("milestone_normalized") or m.get("milestone")]
        # Deduplicate and remove empty
        milestone_names = list(set([m for m in milestone_names if m]))
        
        if len(milestone_names) < 2:
            return []

        prompt = f"""
You are an expert project manager analyzing an engagement letter.
Your task is to identify explicit execution dependencies between the following project milestones based ONLY on the document text.

Milestones:
{json.dumps(milestone_names, indent=2)}

Rules:
1. ONLY identify a dependency if the document explicitly states that one milestone must finish before another can start.
2. DO NOT guess or assume dependencies based on common sense (e.g., do not assume Design must precede Development unless the text implies it).
3. Output MUST be a JSON object with a single key "dependencies" containing an array of objects.
4. Each object must have:
   - "parent_milestone": The exact name of the milestone that must finish first.
   - "child_milestone": The exact name of the milestone that is blocked waiting for the parent.
5. If no explicit dependencies exist in the text, return {{"dependencies": []}}.

Document Text:
{document_text[:10000]}  # Truncated for safety

Output ONLY the JSON object.
"""
        result = LLMService.generate_json(prompt)
        if isinstance(result, dict) and "dependencies" in result:
            return result["dependencies"]
        return []
