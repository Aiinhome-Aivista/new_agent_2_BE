from services.llm_service import LLMService

class StatusIngestionAgent:
    @classmethod
    def extract_status(cls, document_text: str) -> dict:
        prompt = f"""
You are an expert project manager. Extract the project activities and updates from the following Status Report or Minutes of Meeting.
Output MUST be a valid JSON object matching this schema exactly, and nothing else.

Schema:
{{
  "activities": [
    {{
      "activity_name": "string",
      "description": "string",
      "activity_status": "NOT_STARTED" | "PLANNED" | "IN_PROGRESS" | "COMPLETED" | "BLOCKED" | "DELAYED" | "UNKNOWN",
      "progress_percentage": "number (0-100) or null",
      "requested_by": "string or null",
      "owner": "string or null",
      "mentioned_deadline": "YYYY-MM-DD or null",
      "source_page": "number or null",
      "source_section": "string or null",
      "evidence_text": "Exact quote from document",
      "confidence": 0.0 to 1.0
    }}
  ],
  "new_requests": [
    {{
      "request_name": "string",
      "requested_by": "string or null",
      "source_page": "number or null",
      "evidence_text": "Exact quote from document"
    }}
  ]
}}

Document Text:
{document_text[:8000]} # Limit to ~8k chars for POC to avoid massive context
"""
        return LLMService.generate_json(prompt)
