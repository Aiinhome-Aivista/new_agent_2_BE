"""
Generic Baseline Configuration Module
Contains all vocabularies, regex patterns, pronoun mappings, corporate suffixes,
gating rules, thresholds, tolerances, and field semantics for contract baseline extraction.
Strictly ensures zero hardcoded party names, abbreviations, item names, or dates.
"""

from typing import Dict, List, Set, Any

# ── 1. Party Pronoun and Role Mappings ─────────────────────────────────────────

PRONOUN_MAP: Dict[str, str] = {
    "we": "VENDOR",
    "our": "VENDOR",
    "ours": "VENDOR",
    "us": "VENDOR",
    "ourselves": "VENDOR",
    "you": "CLIENT",
    "your": "CLIENT",
    "yours": "CLIENT",
    "yourself": "CLIENT",
    "yourselves": "CLIENT",
}

GENERIC_CLIENT_KEYWORDS: List[str] = [
    "client", "customer", "buyer", "purchaser", "licensee", "principal", "company"
]

GENERIC_VENDOR_KEYWORDS: List[str] = [
    "vendor", "service provider", "contractor", "consultant", "supplier", "licensor"
]

# Corporate suffixes used for strict stem disambiguation and preventing false merges
CORPORATE_SUFFIXES: Set[str] = {
    "limited", "ltd", "llp", "llc", "inc", "incorporated",
    "corp", "corporation", "plc", "pvt ltd", "private limited",
    "gmbh", "sa", "sarl", "bv", "co", "company"
}

# ── 2. Work Activity Promotion Verbs ──────────────────────────────────────────

# Role duties that produce actual delivery work and qualify as VENDOR_WORK (category ACTIVITY)
WORK_ACTIVITY_VERBS: Set[str] = {
    "design", "build", "plan", "train", "cutover", "knowledge transfer",
    "defect fixing", "defect resolution", "project management", "deploy",
    "implement", "develop", "configure", "integrate", "setup", "test",
    "testing", "handover", "migration", "install", "code", "author"
}

# ── 3. Customer Dependency Taxonomy & Gating ──────────────────────────────────

DEPENDENCY_TAXONOMY: Dict[str, List[str]] = {
    "ENVIRONMENT_ACCESS": [
        "cloud", "infrastructure", "server", "hosting", "credentials", "login",
        "network", "firewall", "git", "repo", "environment", "vpn", "access to",
        "dev infrastructure", "uat infrastructure", "prod infrastructure", "tenant"
    ],
    "DATA_ACCESS": [
        "database", "data source", "schema", "test data", "source system",
        "api key", "data access", "feed", "extract", "sample data"
    ],
    "DESIGN_INPUT": [
        "wireframe", "mockup", "design", "branding", "specification", "ui/ux",
        "brand guidelines", "style guide", "logo", "assets"
    ],
    "SIGN_OFF": [
        "sign-off", "signoff", "approval", "acceptance", "review and approve",
        "uat approval", "requirements sign-off", "functional sign-off"
    ],
    "STAFFING": [
        "sme", "stakeholder", "personnel", "spoc", "staff", "resource availability",
        "interview", "business users", "core team", "project champion"
    ],
    "CONTENT": [
        "content", "document", "copy", "guideline", "template", "business requirement",
        "learning section", "news", "articles", "faq content"
    ],
    "LICENSING": [
        "license", "subscription", "m365", "o365", "office 365", "azure subscription",
        "software license", "seat"
    ],
}

# Mapping of dependency type to work item keyword filters for gated items
GATING_RULES: Dict[str, List[str]] = {
    "ENVIRONMENT_ACCESS": [
        "implement", "develop", "build", "configure", "setup", "deploy", "infra", "portal"
    ],
    "DATA_ACCESS": [
        "data", "database", "api", "integration", "migration", "feed", "search", "backend"
    ],
    "DESIGN_INPUT": [
        "ui", "ux", "landing page", "view", "portal", "theme", "layout", "frontend", "branding"
    ],
    "CONTENT": [
        "news", "learning", "poll", "suggestion", "faq", "content", "display"
    ],
    "STAFFING": [
        "requirement", "discovery", "interview", "review", "training", "understanding"
    ],
    "LICENSING": [
        "portal", "azure", "cloud", "sharepoint", "hosted", "deploy"
    ],
    "SIGN_OFF": [
        "go-live", "deploy", "production", "cutover", "release"
    ],
}

# ── 4. Structural Sequence & Dependency Rules ─────────────────────────────────

DOCUMENT_DEPENDENCY_PHRASES: List[str] = [
    "after", "once", "subject to", "requires", "prerequisite to",
    "contingent on", "following completion of", "prior to", "before commencement",
    "upon receipt of", "conditional upon"
]

STRUCTURAL_FOUNDATION_KEYWORDS: List[str] = [
    "architecture", "data model", "authentication", "access control",
    "security model", "user authorization", "base template", "database schema"
]

CRUD_ACTION_ORDER: List[str] = [
    "create", "post", "submit", "apply", "view", "display", "edit",
    "update", "delete", "download", "export", "approve"
]

# Standard phase ordering for DAG validation and sequence
CANONICAL_PHASE_ORDER: List[str] = [
    "assessment", "discovery", "initiation",
    "design", "architecture",
    "construct", "build", "implementation", "development",
    "testing", "uat", "validation",
    "go-live", "deployment", "cutover", "release",
    "hypercare", "support", "operate", "handover"
]

# ── 5. Scheduling, Buffers & Tolerances ────────────────────────────────────────

TIMELINE_TOLERANCE_DAYS: int = 7
WORKING_DAY_BUFFER: int = 3
DUPLICATE_SIMILARITY_THRESHOLD: float = 0.85
CAPACITY_UTILISATION: float = 0.80
WORKING_DAYS_PER_WEEK: int = 5

# ── 6. Trigger-Based Commercial Terms ─────────────────────────────────────────

TRIGGER_PATTERNS: List[Dict[str, Any]] = [
    {
        "term_type": "PAYMENT_DUE",
        "pattern": r"payment\s+(?:is\s+)?due\s+within\s+(\d+)\s*(?:business|working|calendar)?\s*days\s+(?:of|from)\s+(?:date\s+of\s+)?invoice",
        "default_owner": "CLIENT",
        "stated_consequence": "Invoice becomes overdue; commercial interest or service suspension applies."
    },
    {
        "term_type": "CLIENT_DELAY_ESCALATION",
        "pattern": r"(?:client|customer)[-\s]caused\s+delay\s+(?:which\s+)?exceeds\s+(\d+)\s*(?:business|working|calendar)?\s*(?:weeks|days)",
        "default_owner": "CLIENT",
        "stated_consequence": "Vendor reserves right to adjust timeline, renegotiate fees, or reallocate personnel."
    },
    {
        "term_type": "OVERDUE_INVOICE_ESCALATION",
        "pattern": r"invoices?\s+(?:remains?\s+)?pending\s+(?:for\s+)?over\s+(\d+)\s*(?:business|working|calendar)?\s*days",
        "default_owner": "CLIENT",
        "stated_consequence": "Work stoppage or formal escalation to steering committee / legal."
    },
    {
        "term_type": "DELIVERABLE_REVIEW_SLA",
        "pattern": r"(?:review|feedback|sign-off)\s+(?:period|within|sla)\s*(?:is|of)?\s*(\d+)\s*(?:business|working|calendar)?\s*days",
        "default_owner": "CLIENT",
        "stated_consequence": "Deliverable deemed accepted if no written objections submitted within review window."
    }
]

# ── 7. Field Semantics for API & Final Artifact (v2.1.0) ───────────────────────

ARTIFACT_SCHEMA_VERSION = "2.1.0"

FIELD_SEMANTICS: Dict[str, str] = {
    "artifact_schema_version": "Semantic version of the baseline artifact format (v2.1.0).",
    "project_id": "Unique identifier of the project in the workspace.",
    "document_id": "Unique identifier of the source contract document.",
    "document_name": "Filename of the source contract document.",
    "document_type": "Classification of the document (EL, IFA, etc.).",
    "artifact_status": "Lifecycle state of the artifact (READY, PROVISIONAL, BLOCKED).",
    "readiness_status": "Readiness indicator for PM review and downstream tracking.",
    "work_items": "Actionable vendor or joint delivery activities sized for capacity execution (leaf tasks).",
    "scope_items": "Alias of work_items for backward compatibility across endpoints.",
    "all_records": "Complete canonical ledger of all extracted contractual records across all categories.",
    "roles_register": "Register of contractual roles, their owning parties, and associated duties.",
    "milestones": "Major delivery events and contractual gates extracted from document text.",
    "payment_milestones": "Fee-triggering contractual payment milestones with percentages and dates.",
    "customer_dependencies": "Client-owned prerequisites that gate vendor delivery activities.",
    "client_responsibilities": "Ongoing contractual governance, participation, and compliance duties owned by the client.",
    "commitments": "Cadenced obligations (reporting, meetings) and trigger-based SLAs/terms.",
    "project_phases": "Chronological delivery phases named and defined explicitly in the contract.",
    "contract_dates": "Verified start, end, duration, and date provenance information.",
    "contractual_risks": "Contractual ambiguities, liability terms, and operational risks identified.",
    "schedule_warnings": "Rollup schedule warnings aggregated per milestone anchor.",
    "traceability": "Audit matrix linking every record to source chunk locators and quotations.",
    "validation": "Comprehensive boolean integrity checks, gate status, and error logs.",
    "summary": "Reconciled counts across all candidate categories and delivery dimensions.",
    "dashboard_payload": "Optimized payload for the frontend UI dashboard and timeline views."
}
