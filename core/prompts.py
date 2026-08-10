"""
Centralized repository for all LLM prompts used across the system.
This file contains the string templates or functions returning prompts
for different agents and services.
"""

# ==========================================
# ALERTING AGENT PROMPTS
# ==========================================
# Used by the AlertingAgent to compose context-aware email notifications
# regarding high-risk items. It formats the output into a specific JSON schema.
def get_alerting_prompt(project_id: int, item_name: str, reasoning: str) -> str:
    return f"""You are the Alerting Agent for a project management system.
A high-risk project deviation has just been detected. 
Your job is to compose a structured email notification to be sent to the project stakeholders and team members.

Project ID: {project_id}
High-Risk Item: {item_name}
AI Reasoning / Evidence: {reasoning}

Output MUST be a valid JSON object matching this schema exactly:
{{
    "subject": "[URGENT] High Risk Detected - Project <project_id>",
    "summary": "1-2 sentence summary of the issue.",
    "root_cause": "Detailed explanation of why this occurred or what the blocker is, based on the AI reasoning.",
    "suggested_fix": "1-2 clear, actionable steps to resolve the issue."
}}
"""

# ==========================================
# RECONCILIATION AGENT PROMPTS
# ==========================================
def get_reconciliation_draft_prompt(procedural_rules: str, item_type: str, item_desc: str, item_evidence: str, baseline_evidence: str) -> str:
    return f"""You are an expert contract risk evaluator for professional services engagements.

Evaluate the following item against the project baseline and context.

Procedural Rules:
{procedural_rules}

Item Type: {item_type}
{item_desc}

Evidence Text from Document:
{item_evidence}

Baseline Evidence (from contract/EL/IFA):
{baseline_evidence}

Your task:
1. Determine if this item is within the original project scope or is a deviation. If baseline evidence contradicts itself (e.g. an original EL vs an Addendum), you MUST prioritize the information from the most recent/latest document source.
2. Classify the risk category
3. Calculate a risk score from 0 to 100
4. Provide a detailed description explaining WHY you assigned this risk score

Risk Score Guidelines:
- 0-20 (LOW): Item is clearly within scope, no concerns
- 21-40 (MEDIUM): Minor deviation or potential concern worth monitoring
- 41-70 (HIGH): Significant deviation from baseline, scope creep detected, or deliverable at risk
- 71-100 (CRITICAL): Major out-of-scope work, critical deadline miss, or requires immediate escalation

Risk Categories:
- SCOPE_CREEP: Work being done that was not in the original contract
- DELAY: Timeline slippage, missed deadlines, delayed milestones
- MISSING_DELIVERABLE: A contracted deliverable is not being tracked or delivered
- DEPENDENCY: External dependency creating risk
- STAKEHOLDER: Stakeholder-related concern (communication gaps, approval delays)
- GENERAL: Does not fit a specific category

Output MUST be a valid JSON object:
{{
  "is_out_of_scope": false,
  "risk_score": 25,
  "risk_level": "MEDIUM",
  "risk_category": "SCOPE_CREEP",
  "description": "This task was not mentioned in the original engagement letter. The client requested it during the project, which constitutes scope creep. The team has already spent 20 hours on this without a formal change request.",
  "reasoning": "Based on baseline evidence, the original scope only covered web platform design. The iOS app design was not part of the contract.",
  "confidence": 0.85,
  "requires_escalation": false
}}

Rules:
- "risk_score" MUST be an integer between 0 and 100.
- "risk_level" MUST be one of: "LOW", "MEDIUM", "HIGH", "CRITICAL".
- "risk_category" MUST be one of: "SCOPE_CREEP", "DELAY", "MISSING_DELIVERABLE", "DEPENDENCY", "STAKEHOLDER", "GENERAL".
- "description" MUST be 2-3 sentences explaining WHY this risk score was assigned. Include specific evidence.
- "confidence" MUST be a float between 0.0 and 1.0.
"""

def get_reconciliation_verify_prompt(procedural_rules: str, draft: str, baseline_evidence: str) -> str:
    return f"""Review this draft risk assessment for accuracy and consistency.

Rules:
{procedural_rules}

Draft Assessment:
{draft}

Baseline Evidence:
{baseline_evidence}

Verification checklist:
1. If "is_out_of_scope" is true, does the description cite specific missing scope evidence?
2. Is the risk_score consistent with the risk_level? (0-20=LOW, 21-40=MEDIUM, 41-70=HIGH, 71-100=CRITICAL)
3. Is the description specific enough to explain WHY this score was given?
4. Does the risk_category match the actual type of risk?

Output the finalized assessment as a valid JSON object with the same schema. Fix any inconsistencies.
{{
  "is_out_of_scope": false,
  "risk_score": 25,
  "risk_level": "MEDIUM",
  "risk_category": "SCOPE_CREEP",
  "description": "Detailed 2-3 sentence reason",
  "reasoning": "Detailed reasoning with evidence citations",
  "confidence": 0.85,
  "requires_escalation": false
}}
"""

# ==========================================
# RISK EVALUATION AGENT PROMPTS
# ==========================================
def get_risk_aggregation_prompt(
    in_scope_count: int, 
    deterministic_count: int, 
    out_of_scope_activities: list, 
    timeline_deliverables: list
) -> str:
    import json
    return f"""You are the Risk Aggregation Agent.
Summarize the following risk evaluation results and compute an overall project risk score.

In-Scope Activities: {in_scope_count} (including {deterministic_count} confirmed by baseline matching)
Out-of-Scope / Scope Creep Items: {len(out_of_scope_activities)}
Delayed / Blocked Deliverables: {len(timeline_deliverables)}

Scope Creep Items:
{json.dumps(out_of_scope_activities, indent=2)}

Delayed / Blocked Items:
{json.dumps(timeline_deliverables, indent=2)}

Output MUST be a valid JSON object:
{{
   "overallRisk": "HIGH",
   "riskScore": 72,
   "summary": "2 sentence summary of overall project risk status.",
   "project_executive_summary": {{
      "status": "⚠ At Risk",
      "tracked_items": 15,
      "critical_risks": 3,
      "highest_priority": "Production CRM API Credentials",
      "progress_percent": 27,
      "new_blockers": 2,
      "resolved_items": 1,
      "ai_summary": "The project is progressing as planned, however three customer dependencies are preventing execution of the critical path. Immediate customer action is required to avoid delaying CRM Integration, SIT and Production Deployment."
   }},
   "highestActionPriority": {{
      "activity": "Name of the Delayed/Blocked item with the highest action_priority_score",
      "status": "In Progress (70%) or similar",
      "dueDate": "3 Sep (2 days overdue) or similar based on expected_date",
      "reason": "Bullet points explaining blockers and cascade impact",
      "recommendedAction": "Specific actionable recommendation to resolve this top priority item"
   }},
   "recommendations": [
      "One specific actionable recommendation per identified risk."
   ]
}}
"""

# ==========================================
# RISK EVALUATOR SUBAGENTS PROMPTS
# ==========================================
# Phase 1a: Activity Extractor and Normalizer
def get_activity_extractor_prompt(document_text: str, active_tracker_block: str = "None") -> str:
    return f"""You are the Document Fact Extraction Agent.
Your job is to read the following project document (MOM / Status Report) and extract every
work activity, deliverable update, action item, or scope request mentioned.

=== CURRENT ACTIVE PROJECT RISKS ===
{active_tracker_block}

=== DOCUMENT ===
{document_text}

CRITICAL RULES:
1. DO NOT CLASSIFY the items (do not decide if it's a Risk, Action, Dependency, etc). Your job is PURE FACT EXTRACTION.
2. Extract the raw statement or entity name as `statement`.
3. Identify any primary action verb associated with the statement.
4. Identify the owner/responsible party: INTERNAL, CUSTOMER, VENDOR, or THIRD_PARTY.
5. Identify any due date or deadline mentioned.
6. Identify if this item is explicitly blocking or delaying any other deliverables/activities.
   CRITICAL FOR DEPENDENCIES:
   - `blocks` and `blocked_by` MUST contain project deliverables/activities ONLY (e.g. ["CRM Integration"], ["Production VPN Access"]).
   - NEVER put statuses ("Pending review", "Waiting", "Completed", "Blocked"), owners ("Customer", "Internal"), roles, or dates into `blocks` or `blocked_by`.
7. Preserve the exact original sentence as `source_sentence`.
8. Ignore greetings, attendance lists, signatures, and agenda headings.
9. Extract any items that the document explicitly states are now resolved, received, or completed into the `resolved_items` array. You MUST include a confidence score (0-1) and the exact evidence sentence.
   IMPORTANT: When extracting a resolved item, if it conceptually matches one of the CURRENT ACTIVE PROJECT RISKS listed above, you MUST use the EXACT title from the active risks list as the `name`.

Output MUST be valid JSON conforming to the following structure:
{{
  "extractions": [
    {{
      "statement": "Production VPN Access",
      "verb": "provide",
      "owner": "CUSTOMER",
      "due_date": "2026-09-09",
      "blocks": ["CRM Integration"],
      "confidence": 0.98,
      "source_sentence": "Customer must provide Production VPN Access by Sept 9, which is delaying the CRM Integration."
    }}
  ],
  "resolved_items": [
    {{
      "name": "API Credentials",
      "resolution_evidence": "Production API credentials were received.",
      "confidence": 0.96
    }}
  ]
}}
"""

# Phase 1b: Batch Activity Risk Agent
def get_batch_activity_risk_prompt(milestone_progress_block: str, activities_block: str) -> str:
    return f"""You are the Activity Fact Extraction Agent (Phase 1).
The Risk Tracker is a contractual monitoring system. Every item must represent a contractual deliverable or scope request.

Your job is FACT EXTRACTION ONLY. You do NOT calculate execution priority, root cause, cascade impact, or any numeric scores. Those are handled deterministically in the backend.

Evaluate each of the following activities using ONLY their provided baseline context.

{milestone_progress_block}

{activities_block}

RULES:
1. TRACKER TITLE PRIORITY (in order):
   a. If baseline context shows a confirmed IN_SCOPE match → use that baseline item name as "matched_baseline_item".
   b. If baseline context shows a confirmed OUT_OF_SCOPE/excluded match → use that baseline item name.
   c. If NO baseline match exists → use the normalized activity name.

2. STATUS EXTRACTION:
   - Identify the current execution status: IN_PROGRESS, BLOCKED, DELAYED, COMPLETED, NOT_STARTED, WAITING_ON_CUSTOMER, or UNKNOWN.

3. BLOCKED BY & BLOCKS (STRICT ENTITY ONLY):
   - `blocked_by` and `blocks` MUST contain valid project deliverables or activities ONLY (e.g. ["Production CRM API Credentials"]).
   - NEVER include statuses ("Pending review", "Waiting", "Completed"), owners ("Customer", "Vendor", "Internal"), roles ("QA Lead"), dates, or evidence phrases.
   - If not blocked, return an empty array [].

4. OWNER:
   - Extract the entity owner: INTERNAL, CUSTOMER, VENDOR, or THIRD_PARTY.

5. PROGRESS:
   - Extract progress percentage if explicitly mentioned (e.g., 70 for 70%). Otherwise, null. NEVER invent percentages.

6. ENTITY TYPE:
   - Identify what kind of entity this is: MILESTONE, DEPENDENCY, SCOPE_REQUEST, ACTION_ITEM, or RISK.

7. EVIDENCE TEXT:
   - You MUST provide `evidence_text` for every extracted fact. Every downstream calculation must be traceable back to the original MoM sentence.

8. NARRATIVES (PMO Executive View):
   - You must generate a set of PM-friendly narratives. DO NOT use technical jargon (like "cascade depth" or "immediate unlock").
   - `executive_summary`: 1-2 sentence PM executive summary of the item and its impact.
   - `gap_analysis`: Expected (from baseline) vs Actual (from MoM) gap analysis. (e.g. "Expected completion by X, but still pending.")
   - `why_important`: Non-technical explanation of importance and what will slip if not resolved.
   - `business_impact.immediate`: What is blocked right now?
   - `business_impact.future`: What will slip in the future?
   - `ai_interpretation`: Coherent story interpreting the evidence.

Output MUST be a valid JSON array with one entry per activity, in the SAME ORDER as provided:
[
  {{
    "activity": "Activity name as given",
    "entity_type": "MILESTONE|DEPENDENCY|SCOPE_REQUEST|ACTION_ITEM|RISK",
    "matched_baseline_item": "Canonical short baseline entity name, or null if no match",
    "owner": "INTERNAL|CUSTOMER|VENDOR|THIRD_PARTY",
    "status": "IN_PROGRESS|BLOCKED|DELAYED|COMPLETED|NOT_STARTED|WAITING_ON_CUSTOMER",
    "progress": 70,
    "blocked_by": ["Item 1"],
    "blocks": ["Downstream Item 1"],
    "evidence_text": "Exact quote from document proving this status/blocker.",
    "narratives": {{
        "executive_summary": "1-2 sentence summary",
        "gap_analysis": "Expected vs Actual",
        "why_important": "Non-technical explanation",
        "business_impact": {{
           "immediate": "Immediate impact",
           "future": "Future impact"
        }},
        "ai_interpretation": "AI interpretation of evidence"
    }},
    "recommended_action": "Specific actionable recommendation to resolve this item, if blocked or delayed, else null"
  }}
]
"""

# Phase 2: Deliverable Timeline Evaluation Agent
def get_deliverable_timeline_evaluation_prompt(baseline_block: str, risk_block: str, document_text: str) -> str:
    return f"""You are the Deliverable Timeline Evaluation Agent.
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

# ==========================================
# SCOPE EXTRACTION AGENT PROMPTS
# ==========================================
def get_scope_extraction_prompt(document_text: str) -> str:
    return f"""
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
      "confidence": 0.9,
      "deadline": "2026-10-31"
    }},
    {{
      "name": "Mobile App Development",
      "description": "Native iOS and Android application development",
      "scope_type": "OUT_OF_SCOPE",
      "source_page": 1,
      "source_section": "Out of Scope",
      "evidence_text": "The client specifically listed mobile app development as out of scope, meaning the firm has no responsibility to deliver native iOS or Android applications.",
      "confidence": 0.95,
      "deadline": null
    }},
    {{
      "name": "Hardware Procurement",
      "description": "Purchasing of servers or hardware",
      "scope_type": "OUT_OF_SCOPE",
      "source_page": 1,
      "source_section": "Out of Scope",
      "evidence_text": "The contract excludes hardware procurement, indicating the client will handle purchasing their own servers rather than the firm.",
      "confidence": 0.95,
      "deadline": null
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

Evidence Rules:
1. Evidence MUST be taken ONLY from the section where the item was actually extracted.
2. NEVER cite unrelated document sections in the evidence text (e.g., do not cite 'Out of Scope' for an IN_SCOPE item).
3. The explanation must always be internally consistent with the final classification.
4. NEVER group multiple distinct testing or deployment phases (e.g. SIT, UAT, Production) into a single item. They MUST be extracted as separate, individual scope items and milestones.

Document Text:
{document_text}
"""

# ==========================================
# STATUS INGESTION AGENT PROMPTS
# ==========================================
def get_status_ingestion_prompt(document_text: str) -> str:
    return f"""
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
  ],
  "resolved_items": [
    {{
      "name": "VPN Access",
      "resolution_evidence": "Production VPN access received",
      "confidence": 96
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

# ==========================================
# LLM SERVICE PROMPTS
# ==========================================
# Error correction prompt when JSON parsing fails
def get_json_correction_prompt(raw_response: str, error: str) -> str:
    return f"""The following text was supposed to be valid JSON but failed to parse. Please output ONLY the corrected valid JSON and nothing else.

Invalid Text:
{raw_response}

Error:
{error}"""

# ==========================================
# MILESTONE DEADLINE EXTRACTOR PROMPTS
# ==========================================
# Extracts explicit delivery deadlines for scope items.
def get_batch_deadline_extraction_prompt(items_for_prompt: list) -> str:
    import json
    return f"""
You are an expert contract scheduling extractor.
Analyze the following array of scope items and their evidence text to extract Milestone and Deadline information.

Items to analyze:
{json.dumps(items_for_prompt, indent=2)}

Rules for EACH item:
1. Only extract if explicitly mentioned.
2. If no deadline/milestone is mentioned, set has_schedule to false.
3. If deadline is mentioned, return the EXACT original text (e.g., "15 Apr", "End of June").
4. If a milestone name is mentioned (e.g. "UAT", "Go Live"), extract it. If the item itself is the milestone, use the item name.

Output strictly as a JSON ARRAY of objects, matching the input "id".
Schema Example:
[
  {{
    "id": "0",
    "has_schedule": true,
    "milestone": "UAT",
    "deadline_text": "15 Apr"
  }}
]
"""

def get_single_deadline_extraction_prompt(item_name: str, evidence: str) -> str:
    return f"""
You are an expert contract scheduling extractor.
Analyze the following scope item and its evidence text to extract Milestone and Deadline information.

Scope Item: {item_name}
Evidence: {evidence}

Rules:
1. Only extract if explicitly mentioned.
2. If no deadline/milestone is mentioned, set has_schedule to false.
3. If deadline is mentioned, return the EXACT original text (e.g., "15 Apr", "End of June").
4. If a milestone name is mentioned (e.g. "UAT", "Go Live"), extract it. If the item itself is the milestone, use the item name.

Return JSON format:
{{
    "has_schedule": boolean,
    "milestone": string or null,
    "deadline_text": string or null
}}
"""

# Identifies actual milestones for tracking
def get_milestone_identification_prompt(milestones_text: str, document_text: str) -> str:
    return f"""
You are an expert project manager. Review the following proposed milestones and the original contract text.
Your job is to identify the ACTUAL project phases/milestones that represent distinct stages of execution, filtering out generic deliverables or features that are not milestones.

Proposed Milestones (Extracted from Scope):
{milestones_text}

Rules:
1. Select ONLY the items that act as true project phases, gating items, or delivery milestones (e.g., "Project Kickoff", "Requirement Gathering", "SIT", "UAT", "Go-Live").
2. Exclude generic features (e.g., "Audit Logs", "Analytics Dashboard") unless they are explicitly called out as major gating milestones in the text.
3. Keep the exact names of the milestones as they appear in the proposed list if possible.
4. Output MUST be a JSON list of strings (the milestone names). No markdown blocks.

Contract Text:
{document_text}
"""

# ==========================================
# MILESTONE DEPENDENCY EXTRACTOR PROMPTS
# ==========================================
# Extracts dependencies between milestones.
def get_milestone_dependency_prompt(milestone_names: list, document_text: str) -> str:
    import json
    return f"""
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

# ==========================================
# RELEVANCE SERVICE PROMPTS
# ==========================================
def get_relevance_expansion_prompt(type_name: str, short_description: str) -> str:
    return (
        f"You are a professional auditor assistant helping to define document classification profiles.\n\n"
        f"A user has created a new document type called '{type_name}' with this short description:\n"
        f"\"{short_description}\"\n\n"
        f"Your task: Write a detailed, keyword-rich reference profile (approximately 150-200 words) that describes "
        f"what a '{type_name}' document typically contains. Include specific terms, sections, and vocabulary "
        f"that would commonly appear in this type of document.\n\n"
        f"Write it as a single flowing paragraph. Do NOT use bullet points, numbering, or markdown formatting. "
        f"Do NOT include any preamble like 'Here is...' — just output the profile text directly."
    )

def get_relevance_scoring_prompt(document_type: str, type_label: str, embedding_score: int, small_sample: str) -> str:
    return (
        f"You are a professional auditor assistant.\n"
        f"We are analyzing a document to see if it is a valid '{document_type}'.\n"
        f"Our semantic pre-scanner gave it a preliminary confidence score of {embedding_score}/100.\n\n"
        f"Please read this excerpt and provide the final accurate relevance score:\n"
        f"\"\"\"\n{small_sample}\n\"\"\"\n\n"
        f"Scoring guidelines:\n"
        f"- 80-100: Clearly a valid {type_label} document\n"
        f"- 50-79: Partially matches {type_label} but missing key elements\n"
        f"- 20-49: Weak match, mostly unrelated content\n"
        f"- 0-19: Completely unrelated to {type_label}\n\n"
        f"Respond ONLY with a valid JSON object matching this schema:\n"
        f"{{\n"
        f"  \"score\": <integer between 0 and 100>,\n"
        f"  \"reasoning\": \"<brief 1-sentence reasoning explaining why it is or isn't a {document_type}>\"\n"
        f"}}"
    )

# ==========================================
# SCOPE CLASSIFIER PROMPTS
# ==========================================
def get_batch_scope_classifier_prompt(items_for_prompt: list) -> str:
    import json
    return f"""
You are an expert contract analyst. Your task is to classify an array of candidate scope items based ONLY on their provided supporting evidence from the contract.

Items to classify:
{json.dumps(items_for_prompt, indent=2)}

Task:
For EACH item, classify it as "IN_SCOPE", "OUT_OF_SCOPE", or "UNCERTAIN".
- If the evidence clearly states the vendor provides it, choose IN_SCOPE.
- If the evidence states it's excluded, or it's the client's responsibility, or it's an assumption, choose OUT_OF_SCOPE.
- If there is not enough evidence to be sure, choose UNCERTAIN.

Output your result strictly as a JSON ARRAY of objects, matching the input "id".
Schema Example:
[
  {{
    "id": "0",
    "scope_type": "IN_SCOPE", 
    "confidence": 0.9, 
    "evidence_text": "<Brief 1-sentence reasoning quoting the evidence>"
  }}
]
"""

def get_single_scope_classifier_prompt(candidate: dict, combined_evidence: str) -> str:
    return f"""
You are an expert contract analyst. Your task is to classify ONE specific candidate scope item based ONLY on the provided supporting evidence retrieved from the contract.

Candidate Item:
- Name: {candidate["name"]}
- Raw Description: {candidate["description"]}
- Found in Section: {candidate["section"]}

Supporting Evidence from Contract:
{combined_evidence}

Task:
Classify this candidate as "IN_SCOPE", "OUT_OF_SCOPE", or "UNCERTAIN".
- If the evidence clearly states the vendor provides it, choose IN_SCOPE.
- If the evidence states it's excluded, or it's the client's responsibility, or it's an assumption, choose OUT_OF_SCOPE.
- If there is not enough evidence to be sure, choose UNCERTAIN.

Output your result strictly as JSON:
{{
  "scope_type": "IN_SCOPE", 
  "confidence": 0.9, 
  "evidence_text": "<Brief 1-sentence reasoning quoting the evidence>"
}}
"""
# Classifies deliverables into risk categories and maps tracker activities.
def get_scope_classification_prompt(items_for_prompt: list, custom_guidelines: str = "") -> str:
    return f"""
You are an expert IT Project Manager and Risk Assessor.
Your task is to classify a list of project deliverables into specific technical risk categories.

Categories:
- CRITICAL_PATH: Core infrastructure, mandatory integrations, or gating deliverables.
- REGULATORY_COMPLIANCE: Security, audit logs, GDPR, data privacy features.
- HIGH_COMPLEXITY: Machine learning, custom algorithms, complex data migrations.
- STANDARD_FEATURE: Basic UI, CRUD operations, standard reports.

{custom_guidelines}

Rules:
1. Assign exactly one category to each item based on its name and description.
2. Output must be a valid JSON list containing objects with these keys:
   - "scope_item_id": The integer ID from the input.
   - "category": The assigned category name (string).
   - "reasoning": 1 sentence explaining why.
3. Output ONLY valid JSON. No markdown blocks.

Items to classify:
{items_for_prompt}
"""

def get_activity_mapping_prompt(scope_items: list, activities: list) -> str:
    return f"""
You are an expert IT Project Manager. 
You are comparing a list of baseline project scope items with a list of activities extracted from a recent status report.
Your job is to determine which activities map to existing scope items, and which activities are entirely new (potential scope creep).

Baseline Scope Items:
{scope_items}

Tracker Activities:
{activities}

Rules:
1. For each Tracker Activity, find the BEST matching Baseline Scope Item.
2. If the activity is clearly referring to a baseline item, return the "baseline_item_name" EXACTLY as it appears in the baseline list.
3. If the activity is a new request or does not match any baseline item, return "baseline_item_name": null.
4. Your output MUST be a valid JSON list containing objects with exactly these keys:
   - "activity": The name of the tracker activity.
   - "baseline_item_name": The exact name of the matched baseline item, or null.
5. Output ONLY valid JSON. No markdown blocks.
"""


# ==========================================
# RECURRING DELIVERABLE EXTRACTION PROMPTS
# ==========================================
def get_recurrence_extraction_prompt(items_for_prompt: list) -> str:
    import json
    return f"""You are an expert contract analyst specialising in recurring commitment detection.

Analyse the following array of IN_SCOPE project scope items from an Engagement Letter (EL).
Your task is to determine whether each item represents a RECURRING commitment (one that must be delivered repeatedly over the engagement period).

Items to analyse:
{json.dumps(items_for_prompt, indent=2)}

RULES:
1. An item is RECURRING only if the EL explicitly commits the vendor/team to deliver it repeatedly at a defined frequency.
   Examples of RECURRING:
   - "Developer shall provide a monthly improvement to the application."
   - "Vendor shall submit a monthly progress report."
   - "Weekly security monitoring shall be performed."
   - "Quarterly performance review shall be completed."
   - "Annual architecture review shall be delivered."

2. An item is NOT RECURRING if it is:
   - A one-time deliverable with a specific deadline.
   - A general description of project activities (e.g., "The team may discuss progress in monthly meetings").
   - A monitoring/meeting activity not tied to a contractual deliverable output.
   - Incidentally mentioned with a time period (e.g., "monthly steering committee discussed CRM progress").

3. FREQUENCY — If recurring, identify the frequency as exactly one of:
   "WEEKLY" | "MONTHLY" | "QUARTERLY" | "YEARLY"
   Normalize semantic variants:
   - "every month", "monthly", "each month", "per month" → MONTHLY
   - "every week", "weekly", "each week" → WEEKLY
   - "every quarter", "quarterly", "once per quarter" → QUARTERLY
   - "every year", "annually", "annual", "yearly" → YEARLY

4. DATE BOUNDS — Extract explicit start/end dates only if the EL text directly states them.
   DO NOT invent or infer dates. Return null if not explicitly stated.
   Dates must be in YYYY-MM-DD format.

5. CONFIDENCE — Your confidence that this IS a recurring deliverable commitment (0.0–1.0).
   Use 0.9+ only when the evidence is unambiguous (explicit "shall provide monthly X").
   Use 0.5–0.75 for likely recurring but with some ambiguity.
   Use <0.5 for doubtful cases.

6. DO NOT calculate individual occurrence dates. That is handled separately.

Output strictly as a JSON ARRAY of objects, one per input item, matching the input "id":
[
  {{
    "id": "0",
    "is_recurring": true,
    "frequency": "MONTHLY",
    "commitment_title": "Application Improvement",
    "start_date": null,
    "end_date": null,
    "confidence": 0.95,
    "reasoning": "EL explicitly states 'Developer shall provide a monthly improvement' — clear recurring vendor obligation."
  }},
  {{
    "id": "1",
    "is_recurring": false,
    "frequency": null,
    "commitment_title": null,
    "start_date": null,
    "end_date": null,
    "confidence": 0.0,
    "reasoning": "One-time CRM integration deliverable with a specific deadline — not recurring."
  }}
]

Rules for output:
- "is_recurring" MUST be a boolean.
- "frequency" MUST be one of "WEEKLY","MONTHLY","QUARTERLY","YEARLY" or null.
- "start_date" and "end_date" MUST be "YYYY-MM-DD" strings or null.
- "confidence" MUST be a float between 0.0 and 1.0.
- Output ONLY the JSON array. No markdown blocks, no explanations outside JSON.
"""

