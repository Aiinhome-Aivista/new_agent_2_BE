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

        from core.prompts import get_milestone_dependency_prompt
        prompt = get_milestone_dependency_prompt(milestone_names, document_text)
        result = LLMService.generate_json(prompt)
        if isinstance(result, dict) and "dependencies" in result:
            return result["dependencies"]
        return []
