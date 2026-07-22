from services.llm_service import LLMService

class ScopeExtractionAgent:
    @classmethod
    def extract_scope(cls, document_text: str) -> dict:
        prompt = f"""
You are an expert contract analyst. Extract the project scope details from the following Engagement Letter or Inter-Firm Approval document text.
Output MUST be a valid JSON object matching this schema exactly, and nothing else.

Schema Example (Your output MUST follow these keys and value types exactly):
{{
  "project_name": "NextGen Retail Platform",
  "client_name": "ABC Corporation",
  "engagement_type": "Technology Advisory",
  "scope_items": [
    {{
      "name": "Web Platform Design",
      "description": "Design and develop the web-based retail platform",
      "scope_type": "IN_SCOPE",
      "source_page": 1,
      "source_section": "Scope of Work",
      "evidence_text": "The document explicitly states that the firm is responsible for designing and developing the retail platform, which establishes this as a core commitment.",
      "confidence": 0.9
    }},
    {{
      "name": "Mobile App Development",
      "description": "Native iOS and Android application development",
      "scope_type": "OUT_OF_SCOPE",
      "source_page": 1,
      "source_section": "Out of Scope",
      "evidence_text": "The client specifically listed mobile app development as out of scope, meaning the firm has no responsibility to deliver native iOS or Android applications.",
      "confidence": 0.95
    }},
    {{
      "name": "Hardware Procurement",
      "description": "Purchasing of servers or hardware",
      "scope_type": "OUT_OF_SCOPE",
      "source_page": 1,
      "source_section": "Out of Scope",
      "evidence_text": "The contract excludes hardware procurement, indicating the client will handle purchasing their own servers rather than the firm.",
      "confidence": 0.95
    }}
  ],
  "deliverables": [
    {{
      "name": "Final Report",
      "description": "Comprehensive project deliverable report",
      "deadline": "2026-12-31",
      "owner": "Project Manager"
    }}
  ],
  "stakeholders": [
    {{
      "name": "John Smith",
      "role": "Engagement Partner",
      "responsibility": "Overall project oversight"
    }}
  ],
  "milestones": [
    {{
      "name": "Phase 1 Complete",
      "description": "Discovery and planning phase completion",
      "target_date": "2026-09-30"
    }}
  ],
  "assumptions": [
    "Client will provide access to existing systems within 2 weeks"
  ],
  "constraints": [
    "Budget capped at $500,000"
  ],
  "dependencies": [
    "Client IT team availability for integration testing"
  ]
}}

Data Types Rules:
- "scope_type" MUST be one of: "IN_SCOPE", "OUT_OF_SCOPE", "UNCERTAIN".
- "source_page" MUST be an integer or null.
- "confidence" MUST be a float between 0.0 and 1.0.
- "deadline" and "target_date" MUST be in YYYY-MM-DD format or null.
- Use null for any fields if the information is missing.
- If a category has no items, return an empty array [].

Implicit Scope Inference Rules:
1. If the text says "The vendor will deliver..." or "Our responsibilities include...", classify as IN_SCOPE.
2. If the text says "The client is responsible for..." or "Assuming the client provides...", classify as OUT_OF_SCOPE (Client Responsibility).
3. If the text lists assumptions like "Assuming no data migration is needed", extract "Data Migration" and classify as OUT_OF_SCOPE.

Document Text:
{document_text}
"""
        return LLMService.generate_json(prompt)
