from services.llm_service import LLMService
from tools.mcp_tools import MCPTools


class InScopeEvaluationAgent:
    @classmethod
    def evaluate(cls, project_id: int, document_text: str, mysql_scope_items: list = None) -> dict:
        """
        Reads the uploaded MOM/Status Report, extracts activities, and matches them to baseline.

        Dual-source architecture:
          - MySQL: structured list of EXACTLY what was approved (the "what")
          - ChromaDB (EL/IFA): semantic context of HOW those items were defined,
            plus assumptions, boundary conditions, and fine-print that are not
            captured in the MySQL grid (the "how" and "under what conditions")
        """
        context = MCPTools.get_project_context(project_id)

        # --- ChromaDB Query 1: Deliverables & scope definition ---
        # Semantic: find EL/IFA paragraphs describing approved deliverables with their conditions
        scope_definition_evidence = MCPTools.search_baseline(
            project_id, "approved deliverables in-scope activities project scope definition"
        )
        scope_def_text = "\n".join([f"- {e['text']}" for e in scope_definition_evidence[:6]])

        # --- ChromaDB Query 2: Assumptions & boundary conditions (fine-print) ---
        # This captures things like "Client will provide APIs within 2 weeks",
        # "Scope assumes no data migration", "Third-party licensing is client responsibility"
        assumptions_evidence = MCPTools.search_baseline(
            project_id, "assumptions conditions dependencies client responsibilities limitations boundary"
        )
        assumptions_text = "\n".join([f"- {e['text']}" for e in assumptions_evidence[:6]])

        # --- MySQL: structured authoritative list of approved scope item names ---
        mysql_scope_list = ""
        if mysql_scope_items:
            mysql_scope_list = "\n".join([
                f"  [{i+1}] {si['name']}" for i, si in enumerate(mysql_scope_items)
            ])
        else:
            mysql_scope_list = "  (No structured scope baseline available)"

        prompt = f"""You are the In-Scope Evaluation Agent.
Your job is to read the following project status document and extract all work items, action items, and activities.
Then, compare them against the two-source baseline to determine if they are in scope.

=== DOCUMENT TEXT (MOM / Status Report) ===
{document_text}

=== SOURCE 1: STRUCTURED SCOPE BASELINE (MySQL — Authoritative Approved List) ===
These are the EXACT approved deliverable names from the project contract.
Use this as the primary check: does the activity match one of these names?
{mysql_scope_list}

=== SOURCE 2: SCOPE DEFINITIONS & BOUNDARY CONDITIONS (ChromaDB — EL/IFA Fine-Print) ===
This is how the approved deliverables were contractually defined, including assumptions and conditions.
Use this for deeper semantic understanding — even if the wording differs from the MySQL names.
Scope Definitions:
{scope_def_text}

Assumptions & Boundary Conditions:
{assumptions_text}

=== PROCEDURAL RULES ===
{context.get("procedural_rules")}

Instructions:
1. Extract every activity/work item mentioned in the document
2. Check against MySQL list first (exact approved items)
3. Use ChromaDB context to understand scope boundaries and conditions
4. Map each activity to its closest approved scope item name from the MySQL list
5. If an activity matches an approved item but violates an assumption (e.g. client providing APIs), flag it

Output MUST be a valid JSON object:
{{
  "agent": "InScopeEvaluation",
  "activities": [
      {{
          "activity": "Backend API Development",
          "classification": "IN_SCOPE",
          "deliverable": "Web-based Support Portal",
          "status": "In Progress",
          "progress_percentage": 50,
          "confidence": 96
      }}
  ]
}}

Valid classifications: "IN_SCOPE", "POSSIBLY_IN_SCOPE", "UNABLE_TO_DETERMINE".
"""
        return LLMService.generate_json(prompt)


class OutOfScopeDetectionAgent:
    @classmethod
    def detect(cls, project_id: int, extracted_activities: list, document_text: str,
               mysql_scope_items: list = None) -> dict:
        """
        Detects out-of-scope work from the extracted activities.

        Dual-source architecture:
          - MySQL: exact approved list → if activity not in list, it's a candidate for out-of-scope
          - ChromaDB (EL/IFA): explicit exclusion clauses + fine-print that defines scope boundaries.
            This is where "customer-facing chatbots are excluded", "SAP integration is out of scope",
            "Third-party licensing is client responsibility" live — not in the MySQL rows.
        """
        # --- ChromaDB Query 1: Explicit exclusions from EL/IFA ---
        # Direct exclusion language: "excluded", "not in scope", "out of scope", "not covered"
        exclusion_evidence = MCPTools.search_baseline(
            project_id, "excluded out of scope not covered limitations restrictions explicitly excluded"
        )
        exclusion_text = "\n".join([f"- {e['text']}" for e in exclusion_evidence[:6]])

        # --- ChromaDB Query 2: Boundary assumptions and fine-print ---
        # Things like: "Any additional features require written change approval",
        # "Third-party tool costs are not included", "Client will provide test data"
        boundary_evidence = MCPTools.search_baseline(
            project_id, "change approval additional work boundary assumptions fine print commercial review"
        )
        boundary_text = "\n".join([f"- {e['text']}" for e in boundary_evidence[:5]])

        # --- MySQL: structured authoritative list of approved scope item names ---
        mysql_scope_list = ""
        if mysql_scope_items:
            mysql_scope_list = "\n".join([
                f"  [{i+1}] {si['name']}" for i, si in enumerate(mysql_scope_items)
            ])
        else:
            mysql_scope_list = "  (No structured scope baseline available)"

        prompt = f"""You are the Out-of-Scope Detection Agent.
Your job is to detect work that does NOT belong to the contractual scope.

=== SOURCE 1: APPROVED SCOPE BASELINE (MySQL — What Was Officially Approved) ===
These are the ONLY approved in-scope deliverables. Anything not clearly matching this list
is a candidate for scope creep:
{mysql_scope_list}

=== EXTRACTED ACTIVITIES (from MOM/Status Report) ===
{extracted_activities}

=== SOURCE 2A: EXPLICIT EXCLUSION CLAUSES (ChromaDB — EL/IFA Fine-Print) ===
Paragraphs from the Engagement Letter and IFA that explicitly exclude certain work.
These are the "fine print" rules not captured in the MySQL approved list:
{exclusion_text}

=== SOURCE 2B: BOUNDARY CONDITIONS & CHANGE CONTROL (ChromaDB — EL/IFA Fine-Print) ===
Rules about what requires additional approval, what is the client's responsibility,
and what triggers a commercial/change review:
{boundary_text}

=== DOCUMENT CONTEXT ===
{document_text}

Instructions:
1. For each extracted activity, check if it appears in the MySQL approved scope list
2. If NOT in the list, check ChromaDB exclusion clauses to confirm if it is explicitly excluded
3. If it's in the list but violates a boundary condition (e.g. requires change approval), flag it
4. Identify which approved scope item (from MySQL list) the flagged activity is MOST SIMILAR to
5. Only include OUT_OF_SCOPE, POSSIBLE_SCOPE_CREEP, or REVIEW_REQUIRED activities

Output MUST be a valid JSON object:
{{
  "agent": "OutOfScopeDetection",
  "activities": [
      {{
          "activity": "SAP Integration",
          "classification": "OUT_OF_SCOPE",
          "reason": "Not in approved scope list. EL Section 4.2 explicitly excludes SAP integrations.",
          "similar_deliverable": "CRM Integration (Salesforce)",
          "confidence": 99
      }}
  ]
}}

Valid classifications: "OUT_OF_SCOPE", "POSSIBLE_SCOPE_CREEP", "REVIEW_REQUIRED".
Only include flagged activities. Do NOT include perfectly in-scope activities.
"""
        return LLMService.generate_json(prompt)


class DeliverableTimelineEvaluationAgent:
    @classmethod
    def evaluate(cls, project_id: int, document_text: str, mysql_scope_items: list = None) -> dict:
        """
        Evaluates deliverables for delays, blockers, and dependencies.

        Dual-source architecture:
          - MySQL: approved deliverable names for accurate title mapping
          - ChromaDB (EL/IFA): deadline commitments, dependency assumptions, client obligations
            (e.g. "Client to provide test environment by Week 4") that drive schedule risk
        """
        # --- ChromaDB Query 1: Timeline commitments and milestones from EL/IFA ---
        timeline_evidence = MCPTools.search_baseline(
            project_id, "project timeline milestones deadlines delivery schedule phases weeks"
        )
        timeline_text = "\n".join([f"- {e['text']}" for e in timeline_evidence[:6]])

        # --- ChromaDB Query 2: Dependency and client obligation fine-print ---
        # "Client will provide X by Y", "Vendor delivery is contingent on...",
        # "Delay caused by client is not contractor's responsibility"
        dependency_evidence = MCPTools.search_baseline(
            project_id, "client responsibilities dependencies blockers prerequisite obligations contingent"
        )
        dependency_text = "\n".join([f"- {e['text']}" for e in dependency_evidence[:5]])

        # --- MySQL: structured approved deliverable names for accurate card titles ---
        mysql_scope_list = ""
        if mysql_scope_items:
            mysql_scope_list = "\n".join([
                f"  [{i+1}] {si['name']}" for i, si in enumerate(mysql_scope_items)
            ])
        else:
            mysql_scope_list = "  (No structured scope baseline available)"

        prompt = f"""You are the Deliverable & Timeline Evaluation Agent.
Your job is to evaluate project execution risk — delays, blockers, and dependency failures.

=== SOURCE 1: APPROVED SCOPE DELIVERABLES (MySQL — Ground Truth Names) ===
Use these exact names when identifying which deliverable is at risk:
{mysql_scope_list}

=== SOURCE 2A: CONTRACTUAL TIMELINE COMMITMENTS (ChromaDB — EL/IFA Fine-Print) ===
Deadlines, milestones, and delivery schedules committed in the contract:
{timeline_text}

=== SOURCE 2B: DEPENDENCY & CLIENT OBLIGATION CLAUSES (ChromaDB — EL/IFA Fine-Print) ===
Rules about what the client must provide, and what happens when dependencies are not met.
These are critical for identifying who is responsible for a delay:
{dependency_text}

=== DOCUMENT TEXT (MOM / Status Report) ===
{document_text}

Instructions:
1. Extract all deliverables/activities mentioned in the document with their status
2. Map each to its closest approved scope item name from the MySQL list (use that exact name)
3. Compare actual progress against contractual timeline commitments from ChromaDB
4. Check if any blockers are caused by client obligation failures (from ChromaDB dependency clauses)
5. Only include deliverables with MEDIUM/HIGH/CRITICAL risk or that are delayed/blocked

Output MUST be a valid JSON object:
{{
  "agent": "DeliverableTimelineEvaluation",
  "deliverables": [
      {{
          "deliverable": "Web-based Support Portal",
          "expected_date": "2026-08-10",
          "current_status": "Delayed",
          "delay_days": 8,
          "blockers": ["Client approval pending — per EL Section 3.1, client must approve UX by Week 6"],
          "dependency_status": "Blocked",
          "risk": "HIGH"
      }}
  ]
}}

Valid Risk levels: "LOW", "MEDIUM", "HIGH", "CRITICAL".
"""
        return LLMService.generate_json(prompt)
