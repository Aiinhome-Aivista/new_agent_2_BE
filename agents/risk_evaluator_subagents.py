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
        PHASE 1: Batch risk diagnosis.
        Evaluates ALL ambiguous activities in a SINGLE LLM call.

        The LLM's job here is DIAGNOSIS ONLY — it identifies:
          - What category of risk exists (SCOPE_CREEP / DELAY / DEPENDENCY / BLOCKED / NONE)
          - Which diagnostic signals are present (deadline_missed, customer_dependency, etc.)
          - What the business impact level is (LOW / MEDIUM / HIGH)
          - How confident it is (0.0–1.0)

        The LLM does NOT produce a numeric score.
        Scoring happens in Phase 2 (RiskScoringEngine) using deterministic weighted rules.

        KEY RULE — Tracker Title Priority:
        1. If a matched_baseline_item exists in the approved IN_SCOPE baseline → use that as title.
        2. If it matches an OUT_OF_SCOPE/excluded item → use that as title.
        3. Only if NO baseline match exists → use the normalized activity name as title.

        CRITICAL: An approved IN_SCOPE baseline item can NEVER be classified as SCOPE_CREEP.
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

        prompt = f"""You are the Activity Risk Diagnosis Agent (Phase 1).
The Risk Tracker is a contractual monitoring system. Every item must represent a contractual
deliverable or scope request — not an activity log entry.

Your job is DIAGNOSIS ONLY. You identify what kind of risk exists and which signals are present.
You do NOT calculate numeric scores — that happens deterministically after your output.

Evaluate each of the following activities using ONLY their provided baseline context.

{activities_block}

RULES:
1. TRACKER TITLE PRIORITY (in order):
   a. If baseline context shows a confirmed IN_SCOPE match → use that baseline item name as "matched_baseline_item". risk_category CANNOT be SCOPE_CREEP.
   b. If baseline context shows a confirmed OUT_OF_SCOPE/excluded match → use that baseline item name. risk_category is SCOPE_CREEP.
   c. If NO baseline match exists → use the normalized activity name. risk_category is SCOPE_CREEP.

2. RISK CATEGORIES (pick ONE per activity):
   - SCOPE_CREEP: Activity has NO approved IN_SCOPE baseline match (new request or excluded item).
   - DELAY: Deliverable is behind schedule or has missed its contractual deadline.
   - DEPENDENCY: Blocked waiting for client/third party obligation (VPN, API creds, infra).
   - BLOCKED: Explicitly blocked by a technical or organizational issue.
   - NONE: On-track and within approved scope with no blockers.

3. DIAGNOSTIC SIGNALS — for each activity, identify which signals are TRUE:
   - deadline_missed: The contractual or mentioned deadline has passed or is at immediate risk.
   - customer_dependency: A customer obligation (VPN, API credentials, infrastructure, access) is pending.
   - technical_dependency: An internal technical dependency is blocking progress.
   - progress_behind: Stated or implied progress is behind expected pace.
   - milestone_slipping: A named project milestone (UAT, Go Live, delivery) is slipping.
   - missing_deliverable: A contractual deliverable has no evidence of progress at all.

4. NEVER classify an approved IN_SCOPE baseline item as SCOPE_CREEP.

5. TRACKER IDENTITY: matched_baseline_item must be the canonical baseline name, not the MoM wording.
   The same baseline item must produce the same tracker title across different MoMs.

6. CANONICAL TITLES: matched_baseline_item must be the SHORT business entity name.
   - Use: "VPN Connectivity", NOT "Customer shall provide VPN connectivity"
   - Use: "Azure AD SSO", NOT "The Vendor shall configure Azure AD SSO by 25 April 2026"
   - Use: "API Credentials", NOT "Customer shall provide API credentials"
   The original contractual sentence belongs in "reasoning" only.

7. BUSINESS IMPACT:
   - HIGH: Blocks a core contractual deliverable, threatens project viability.
   - MEDIUM: Causes schedule risk or partial scope impact.
   - LOW: Minor or easily recoverable issue.

8. CONFIDENCE: 0.0–1.0 — how strongly does the baseline context support your diagnosis?

Output MUST be a valid JSON array with one entry per activity, in the SAME ORDER as provided:
[
  {{
    "activity": "Activity name as given",
    "matched_baseline_item": "Canonical short baseline entity name, or null if no match",
    "risk_category": "SCOPE_CREEP|DELAY|DEPENDENCY|BLOCKED|NONE",
    "signals": {{
      "deadline_missed": false,
      "customer_dependency": false,
      "technical_dependency": false,
      "progress_behind": false,
      "milestone_slipping": false,
      "missing_deliverable": false
    }},
    "business_impact": "LOW|MEDIUM|HIGH",
    "confidence": 0.85,
    "reasoning": "Short explanation citing the baseline context. Use the original MoM sentence as evidence here."
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


class DeliverableTimelineEvaluationAgent:
    @classmethod
    def evaluate_progress(cls, approved_baseline_items: list, document_text: str, risk_eval_output: list) -> list:
        """
        Extracts deliverable progress from the MoM/Status Report.
        Must strictly adhere to the rule of NEVER inventing percentages.
        Consolidates multiple references into a single progress record per baseline item.
        """
        if not approved_baseline_items:
            return []

        baseline_block = ""
        for item in approved_baseline_items:
            baseline_block += f"- ID: {item.get('id', 'Unknown')} | Deliverable: {item.get('name', 'Unknown')}\n"

        risk_block = ""
        import json
        try:
            risk_block = json.dumps(risk_eval_output, indent=2)
        except Exception:
            risk_block = str(risk_eval_output)

        prompt = f"""You are the Deliverable Timeline Evaluation Agent.
Your job is to extract project execution progress for specific approved baseline deliverables based on the provided document (MoM/Status Report).

=== APPROVED BASELINE DELIVERABLES ===
{baseline_block}

=== RISK EVALUATION OUTPUT ===
{risk_block}

=== DOCUMENT TEXT ===
{document_text}

CRITICAL RULES:
1. One progress record per approved baseline deliverable ONLY. Do not create duplicates for the same deliverable.
2. If multiple document statements refer to the same deliverable, consolidate them into a single "execution_summary" and single progress record.
3. NEVER invent or infer progress percentages. 
   - Allowed: Document says "60%" -> You output 60.
   - Allowed: Document says "Completed" -> You output null for percentage, but "COMPLETED" for status.
   - NOT Allowed: Document says "Completed" -> Output 100%. (Unless the text explicitly says 100%).
4. Map the progress_status strictly to one of: NOT_STARTED, IN_PROGRESS, BLOCKED, COMPLETED, DELAYED, RESCHEDULED, AT_RISK, PENDING.
5. Identify any "dependencies" (formerly blockers) if the item is blocked or delayed (e.g., VPN, API credentials, infrastructure).

Output MUST be a valid JSON object with the following schema:
{{
  "progress_records": [
    {{
      "scope_item_id": 123, // The ID of the baseline item from the input
      "progress_status": "COMPLETED",
      "progress_percentage": 60, // integer between 0-100 or null
      "execution_summary": "Development completed and handed over to QA.",
      "dependencies": ["VPN Connectivity", "API Credentials"], // List of strings or empty list
      "confidence": 0.95, // 0.0 - 1.0
      "evidence_text": "Exact quote from document supporting this status."
    }}
  ]
}}
"""
        result = LLMService.generate_json(prompt)
        return result.get("progress_records", [])

