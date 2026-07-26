import json
from services.llm_service import LLMService


class ActivityExtractorAgent:
    @classmethod
    def extract_activities(cls, document_text: str) -> list:
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
        prompt = f"""You are the Activity Extractor and Normalizer Agent.
Your job is to read the following project document (MOM / Status Report) and extract every
work activity, deliverable update, action item, or scope request mentioned.

=== DOCUMENT ===
{document_text}

CRITICAL RULES:
1. The Risk Tracker is a contractual monitoring system. Tracker items represent contractual 
   deliverables, not activity log entries.
2. For each activity, produce a NORMALIZED BUSINESS ENTITY name — what the contractual item is,
   not how it was described in this meeting.
3. Normalization means:
   - Strip action verbs: "Evaluate", "Review", "Prepare", "Discuss", "Assess", "Consider"
   - Strip noise words: "Request", "Proposal", "Assessment", "Activity"
   - Strip customer preambles: "Customer shall provide", "Client must"
   - Strip date suffixes: "UAT - 15 May 2026" → "UAT"
   - Merge semantically identical activities: "Evaluate SAP Integration Request" AND
     "SAP Integration Request Assessment" both normalize to "SAP Integration"
4. Preserve the original sentence as source_sentence (this becomes the evidence).
5. Ignore greetings, attendance lists, signatures, and agenda headings.

Examples of correct normalization:
- "Evaluate SAP Integration Request" → "SAP Integration"
- "Review Mobile Applications Request" → "Mobile Applications"
- "Evaluate Voice Bot Proposal" → "Voice Bot"
- "CRM Integration Development" → "CRM Integration"
- "Analytics Dashboard Development" → "Analytics Dashboard"
- "Customer shall provide VPN connectivity" → "VPN Connectivity"
- "Customer shall provide API credentials" → "API Credentials"
- "UAT - 15 May 2026" → "UAT"
- "Go Live - 30 June 2026" → "Production Deployment (Go Live)"

Output MUST be valid JSON — return unique normalized activities only (deduplicate by entity):
{{
  "activities": [
    {{
      "activity": "SAP Integration",
      "source_sentence": "The team discussed evaluating the SAP Integration request.",
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
        
        KEY RULE — Tracker Title Priority:
        1. If a matched_baseline_item exists in the approved IN_SCOPE baseline → use that as title.
        2. If it matches an OUT_OF_SCOPE/excluded item → use that as title.
        3. Only if NO baseline match exists → use the normalized activity name as title.
        
        CRITICAL: An approved IN_SCOPE baseline item can NEVER be classified as SCOPE_CREEP.
        If the baseline context shows the item is IN the approved scope, classify as NONE or DELAY/DEPENDENCY.
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

        prompt = f"""You are the Activity Risk Evaluation Agent.
The Risk Tracker is a contractual monitoring system. Every item must represent a contractual
deliverable or scope request — not an activity log entry.

Evaluate each of the following activities using ONLY their provided baseline context.

{activities_block}

RULES:
1. TRACKER TITLE PRIORITY (in order):
   a. If baseline context shows a confirmed IN_SCOPE match → use that baseline item name as "matched_baseline_item". Category CANNOT be SCOPE_CREEP.
   b. If baseline context shows a confirmed OUT_OF_SCOPE/excluded match → use that baseline item name. Category is SCOPE_CREEP.
   c. If NO baseline match exists → use the normalized activity name. Category is SCOPE_CREEP.
   
2. RISK CATEGORIES:
   - SCOPE_CREEP: Activity has NO approved IN_SCOPE baseline match (new request or excluded item).
   - DELAY: Deliverable is behind schedule or past its deadline.
   - DEPENDENCY: Blocked waiting for client/third party (customer responsibility items).
   - BLOCKED: Explicitly blocked.
   - NONE: On-track and within approved scope.

3. NEVER classify an approved IN_SCOPE baseline item as SCOPE_CREEP.

4. TRACKER IDENTITY: The matched_baseline_item must be the canonical baseline name, not the MoM wording.
   The same baseline item must produce the same tracker title across different MoMs.
   - "CRM Integration development completed" → matched_baseline_item: "CRM Integration"
   - "CRM module delivered" → matched_baseline_item: "CRM Integration" (same item!)

Output MUST be a valid JSON array with one entry per activity, in the SAME ORDER as provided:
[
  {{
    "activity": "Activity name as given",
    "matched_baseline_item": "Canonical baseline scope item name, or null if no match",
    "risk_category": "SCOPE_CREEP|DELAY|DEPENDENCY|BLOCKED|NONE",
    "risk_level": "LOW|MEDIUM|HIGH|CRITICAL",
    "reasoning": "Short explanation citing the baseline context."
  }}
]
"""
        result = LLMService.generate_json(prompt)
        if isinstance(result, list):
            return result
        for key in ["activities", "results", "evaluations"]:
            if key in result:
                return result[key]
        return []
