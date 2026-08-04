from services.llm_service import LLMService

class StatusIngestionAgent:
    @classmethod
    def extract_status(cls, document_text: str) -> dict:
        from core.prompts import get_status_ingestion_prompt
        prompt = get_status_ingestion_prompt(document_text)
        return LLMService.generate_json(prompt)
