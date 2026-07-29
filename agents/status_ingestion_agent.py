from services.llm_service import LLMService

class StatusIngestionAgent:
    @classmethod
    def extract_status(cls, document_text: str) -> dict:
        prompt = f"""
You are an expert project manager. Extract ALL project activities, updates, and meeting items from the following Status Report or Minutes of Meeting.
Output MUST be a valid JSON object matching this schema exactly, and nothing else.

Schema Example (Your output MUST follow these keys and value types exactly):
{{
  "activities": [
    {{
      "activity_name": "Name of task",
      "description": "Brief description",
      "activity_status": "IN_PROGRESS",
      "progress_percentage": 50,
      "requested_by": "John Doe",
      "owner": "Jane Doe",
      "mentioned_deadline": "2026-10-15",
      "source_page": 1,
      "source_section": "Updates",
      "evidence_text": "Exact quote from document",
      "confidence": 0.9
    }}
  ],
  "new_requests": [
    {{
      "request_name": "New feature request",
      "description": "Brief description of the request",
      "requested_by": "Client",
      "source_page": 1,
      "evidence_text": "Exact quote from document"
    }}
  ],
  "blockers": [
    {{
      "blocker_name": "Name of the blocker",
      "description": "What is blocked and why",
      "affected_activity": "Which activity is blocked",
      "severity": "HIGH",
      "source_page": 1,
      "evidence_text": "Exact quote from document"
    }}
  ],
  "action_items": [
    {{
      "action_name": "What needs to be done",
      "assigned_to": "Person responsible",
      "due_date": "2026-10-15",
      "status": "PENDING",
      "source_page": 1,
      "evidence_text": "Exact quote from document"
    }}
  ],
  "decisions": [
    {{
      "decision": "What was decided",
      "decided_by": "Person or group",
      "impact": "How this affects the project",
      "source_page": 1,
      "evidence_text": "Exact quote from document"
    }}
  ],
  "risks_mentioned": [
    {{
      "risk_name": "Name of the risk",
      "description": "Description of the risk",
      "likelihood": "HIGH",
      "impact": "HIGH",
      "source_page": 1,
      "evidence_text": "Exact quote from document"
    }}
  ],
  "stakeholder_comments": [
    {{
      "commenter": "Person who commented",
      "comment": "What they said",
      "sentiment": "POSITIVE",
      "source_page": 1,
      "evidence_text": "Exact quote from document"
    }}
  ]
}}

Data Types Rules:
- "activity_status" MUST be one of: "NOT_STARTED", "PLANNED", "IN_PROGRESS", "COMPLETED", "BLOCKED", "DELAYED", "UNKNOWN".
- "progress_percentage" MUST be an integer between 0 and 100, or null.
- "source_page" MUST be an integer or null.
- "confidence" MUST be a float between 0.0 and 1.0.
- "severity" and "likelihood" and "impact" MUST be one of: "LOW", "MEDIUM", "HIGH", "CRITICAL".
- "sentiment" MUST be one of: "POSITIVE", "NEGATIVE", "NEUTRAL", "CONCERN".
- "status" for action_items MUST be one of: "PENDING", "IN_PROGRESS", "COMPLETED", "OVERDUE".
- Use null for any string fields if the information is missing.
- If a category has no items found in the document, return an empty array [].

Document Text:
{document_text[:8000]}
"""
        return LLMService.generate_json(prompt)
