import json
from services.llm_service import LLMService

class MilestoneDependencyExtractor:
    @classmethod
    def extract_dependencies(cls, milestones: list[dict], document_text: str) -> list[dict]:
        """
        Attempts to extract explicit dependencies between the provided milestones from the document text.
        Uses structured output (MilestoneDependencyOutput) to guarantee the response always has a
        'dependencies' list with {parent_milestone, child_milestone} objects — no silent key mismatches.
        """
        if not milestones:
            return []

        milestone_names = [m.get("milestone_normalized", m.get("milestone")) for m in milestones if m.get("milestone_normalized") or m.get("milestone")]
        # Deduplicate and remove empty
        milestone_names = list(set([m for m in milestone_names if m]))

        if len(milestone_names) < 2:
            return []

        from core.prompts import get_milestone_dependency_prompt
        from agents.llm_schemas import MilestoneDependencyOutput

        prompt = get_milestone_dependency_prompt(milestone_names, document_text)

        try:
            structured = LLMService.generate_structured(prompt, MilestoneDependencyOutput)
            if isinstance(structured, MilestoneDependencyOutput):
                # Convert Pydantic objects to plain dicts matching existing caller expectations
                return [
                    {"parent_milestone": e.parent_milestone, "child_milestone": e.child_milestone}
                    for e in structured.dependencies
                ]
            elif isinstance(structured, dict) and "dependencies" in structured:
                return structured["dependencies"]
        except Exception as e:
            print(f"[MilestoneDependencyExtractor] generate_structured failed ({e}), falling back to generate_json")

        # Fallback: original generate_json path
        result = LLMService.generate_json(prompt)
        if isinstance(result, dict) and "dependencies" in result:
            return result["dependencies"]
        return []

