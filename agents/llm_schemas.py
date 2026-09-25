"""
Pydantic schemas for all LLM structured output calls in ACSE.

DESIGN RULES:
1. Every field with a fixed set of valid values uses Literal[...].
   This constrains LLM output at the token generation level —
   the model cannot produce values outside the Literal set.
2. Every field uses Annotated[type, Field(description="...")] so the
   LLM understands what each field means without needing schema
   examples in the prompt text.
3. Optional fields default to None. Lists default to [].
4. These schemas are passed to llm.with_structured_output(Schema).
   Do NOT use these schemas for validation of non-LLM data.
"""

from __future__ import annotations
from typing import Annotated, List, Literal, Optional
from pydantic import BaseModel, Field


# ══════════════════════════════════════════════════════════════
# DOCUMENT FACT EXTRACTION (ActivityExtractorAgent — Step 2A)
# ══════════════════════════════════════════════════════════════

class ExtractedActivity(BaseModel):
    """One work activity, deliverable update, or action item from a MoM/Status Report."""

    statement: Annotated[str, Field(
        description="The exact name of the deliverable, activity, or action item as mentioned in the document. Use the normalised baseline item name if it matches one."
    )]
    verb: Annotated[Optional[str], Field(
        default=None,
        description="The primary action verb associated with this item (e.g. 'provide', 'complete', 'review'). Null if not applicable."
    )]
    owner: Annotated[
        Literal["INTERNAL", "CUSTOMER", "VENDOR", "THIRD_PARTY"],
        Field(description=(
            "Who is responsible for EXECUTING or DELIVERING this item. "
            "INTERNAL = the vendor delivery team. CUSTOMER = the client must provide it. "
            "VENDOR = a specific named vendor. THIRD_PARTY = an external party. "
            "NEVER set CUSTOMER for a contracted deliverable just because it is blocked by a customer dependency — "
            "the blocker owner and the deliverable owner are different concepts."
        ))
    ]
    due_date: Annotated[Optional[str], Field(
        default=None,
        description="Due date in YYYY-MM-DD format if explicitly stated in the document. Null if not mentioned. NEVER invent a date."
    )]
    blocks: Annotated[List[str], Field(
        default_factory=list,
        description=(
            "Names of OTHER deliverables/activities that THIS item is currently blocking. "
            "Must contain only project deliverable names (e.g. ['CRM Integration']). "
            "NEVER include statuses, owner names, roles, or dates. "
            "If this item blocks B, then B.blocked_by must contain this item — do NOT also set this item's blocked_by to B."
        )
    )]
    blocked_by: Annotated[List[str], Field(
        default_factory=list,
        description=(
            "Names of OTHER deliverables/activities that are currently blocking THIS item. "
            "Must contain only project deliverable names. "
            "NEVER include fulfilled prerequisites — if a dependency was already received/resolved, return []."
        )
    )]
    confidence: Annotated[float, Field(
        ge=0.0, le=1.0,
        description="Confidence that this extraction is accurate. 1.0 = explicitly stated in document. 0.5 = inferred from context."
    )]
    source_sentence: Annotated[str, Field(
        description="The exact original sentence from the document that this extraction is based on."
    )]
    classification_type: Annotated[
        Literal["RISK", "PROGRESS_UPDATE"],
        Field(description=(
            "RISK = this item represents a problem, blocker, scope creep, or delay that needs tracking. "
            "PROGRESS_UPDATE = purely informational status update with no active risk (e.g. 'Development is on track'). "
            "When in doubt, use RISK."
        ))
    ] = "RISK"


class ResolvedItem(BaseModel):
    """An item, dependency, or prerequisite that the document confirms is now completed or fulfilled."""

    name: Annotated[str, Field(
        description="The normalised name of the completed/resolved item. Use the baseline item name if it matches."
    )]
    resolution_evidence: Annotated[str, Field(
        description="The exact sentence from the document that confirms this item is complete or fulfilled."
    )]
    confidence: Annotated[float, Field(
        ge=0.0, le=1.0,
        description="Confidence that this item is genuinely completed. 0.9+ = explicitly stated. 0.5–0.75 = inferred."
    )]


class DocumentExtractionSchema(BaseModel):
    """Output schema for ActivityExtractorAgent (Step 2A fact extraction)."""

    extractions: Annotated[List[ExtractedActivity], Field(
        description="All work activities, deliverable updates, and action items extracted from the document."
    )]
    resolved_items: Annotated[List[ResolvedItem], Field(
        default_factory=list,
        description=(
            "Every item, deliverable, prerequisite, or dependency the document explicitly confirms is "
            "now completed, resolved, received, provided, signed off, or fulfilled. "
            "When a sentence says 'X completed after receiving Y and Z', extract X, Y, AND Z as separate resolved items."
        )
    )]


# Backward-compatibility alias
ExtractionOutput = DocumentExtractionSchema
ActivityItem = ExtractedActivity


# ══════════════════════════════════════════════════════════════
# BATCH RISK SCORING (BatchActivityRiskAgent — Step 2E)
# ══════════════════════════════════════════════════════════════

class BusinessImpact(BaseModel):
    immediate: Annotated[str, Field(
        description="What is blocked or stopped RIGHT NOW because of this item. If an overdue milestone depends on it, name the milestone and how many days overdue."
    )]
    future: Annotated[str, Field(
        description="What will slip or fail in the future if this item is not resolved."
    )]


class Narratives(BaseModel):
    executive_summary: Annotated[str, Field(
        description="1–2 sentence PM executive summary of this item and its impact. No technical jargon."
    )]
    gap_analysis: Annotated[str, Field(
        description="Expected (from baseline) vs Actual (from MoM) gap. E.g. 'Expected completion by X, but still pending.'"
    )]
    why_important: Annotated[str, Field(
        description="Non-technical explanation of why this matters and what will slip if not resolved."
    )]
    business_impact: BusinessImpact
    ai_interpretation: Annotated[str, Field(
        description="A coherent story interpreting the evidence for this item."
    )]


class RiskItem(BaseModel):
    """One evaluated risk item from BatchActivityRiskAgent."""

    activity: Annotated[str, Field(
        description="The activity name exactly as provided in the input."
    )]
    entity_type: Annotated[
        Literal["MILESTONE", "DEPENDENCY", "SCOPE_REQUEST", "ACTION_ITEM", "RISK"],
        Field(description=(
            "MILESTONE = a contracted deliverable or project phase. "
            "DEPENDENCY = an external prerequisite (credentials, access, approval). "
            "SCOPE_REQUEST = work NOT in the contract baseline (no matched_baseline_item). "
            "ACTION_ITEM = a specific assigned task. "
            "RISK = a general project risk. "
            "CRITICAL: If matched_baseline_item is set, entity_type MUST be MILESTONE, DEPENDENCY, or ACTION_ITEM — NEVER SCOPE_REQUEST."
        ))
    ]
    risk_level: Annotated[
        Optional[Literal["LOW", "MEDIUM", "HIGH", "CRITICAL"]],
        Field(
            default=None,
            description="Optional qualitative risk level assessing project risk (LOW, MEDIUM, HIGH, CRITICAL)."
        )
    ]
    matched_baseline_item: Annotated[Optional[str], Field(
        default=None,
        description=(
            "The exact canonical baseline scope item name this activity maps to, or null if no match. "
            "MUST prefer the baseline item with the highest LEXICAL overlap with the activity name. "
            "If set, it MUST appear verbatim (or near-verbatim) in the baseline scope items list."
        )
    )]
    owner: Annotated[
        Literal["INTERNAL", "CUSTOMER", "VENDOR", "THIRD_PARTY"],
        Field(description=(
            "Who EXECUTES or DELIVERS this item. "
            "INTERNAL = vendor team (even if blocked by customer). "
            "CUSTOMER = client must provide this (credentials, access, approvals, sign-offs). "
            "NEVER set CUSTOMER for a contracted deliverable just because it is blocked by a customer dependency."
        ))
    ]
    status: Annotated[
        Literal["IN_PROGRESS", "BLOCKED", "DELAYED", "COMPLETED", "NOT_STARTED", "WAITING_ON_CUSTOMER", "UNKNOWN"],
        Field(description=(
            "Current execution status. "
            "WAITING_ON_CUSTOMER = specifically waiting for client to provide something. "
            "BLOCKED = waiting for an internal dependency. "
            "DELAYED = past its deadline but still in progress."
        ))
    ]
    progress: Annotated[Optional[int], Field(
        default=None,
        ge=0, le=100,
        description="Progress percentage as an integer 0–100 if EXPLICITLY stated in the document. NEVER invent a percentage. Null if not mentioned."
    )]
    blocked_by: Annotated[List[str], Field(
        default_factory=list,
        description="Names of active prerequisite items blocking this activity. Empty list if not blocked or if prerequisites are already fulfilled."
    )]
    blocks: Annotated[List[str], Field(
        default_factory=list,
        description="Names of downstream items that THIS activity is blocking."
    )]
    evidence_text: Annotated[str, Field(
        description="The exact sentence or clause from the document that is the primary evidence for this assessment. REQUIRED for every item."
    )]
    narratives: Narratives
    recommended_action: Annotated[Optional[str], Field(
        default=None,
        description="A specific, actionable recommendation to resolve this item if it is blocked or delayed. Null if no action is needed."
    )]
    is_out_of_scope: Annotated[bool, Field(
        default=False,
        description="True ONLY if this work is explicitly NOT in the approved contract baseline AND the customer is requesting it outside the CR process."
    )]


class BatchRiskScoringSchema(BaseModel):
    """Output schema for BatchActivityRiskAgent (Step 2E). Contains one RiskItem per input activity."""
    items: List[RiskItem]


# Backward-compatibility alias
RiskEvaluationOutput = BatchRiskScoringSchema


# ══════════════════════════════════════════════════════════════
# SCOPE CLASSIFICATION (ScopeClassifier — baseline extraction)
# ══════════════════════════════════════════════════════════════

class ScopeClassificationItem(BaseModel):
    """Classification result for one scope candidate."""

    id: Annotated[str, Field(
        description="The id from the input item — must match exactly."
    )]
    scope_type: Annotated[
        Literal["IN_SCOPE", "OUT_OF_SCOPE", "UNCERTAIN"],
        Field(description=(
            "IN_SCOPE = vendor/team is contracted to deliver this. "
            "OUT_OF_SCOPE = explicitly excluded, or is a client responsibility, or is an assumption. "
            "UNCERTAIN = not enough evidence to be sure."
        ))
    ]
    confidence: Annotated[float, Field(
        ge=0.0, le=1.0,
        description="Confidence in this classification. 0.95+ = heading or text is unambiguous. 0.5–0.75 = inferred."
    )]
    evidence_text: Annotated[str, Field(
        description="One sentence explaining the classification, quoting the specific evidence from the contract text."
    )]


class ScopeClassificationOutput(BaseModel):
    """Output schema for ScopeClassifier LLM call."""
    items: List[ScopeClassificationItem]


# Backward-compatibility alias
ScopeItem = ScopeClassificationItem


# ══════════════════════════════════════════════════════════════
# RECURRENCE DETECTION (RecurringDeliverableService)
# ══════════════════════════════════════════════════════════════

class RecurrenceItem(BaseModel):
    """Recurrence analysis result for one scope item."""

    id: Annotated[str, Field(description="The id from the input item — must match exactly.")]
    is_recurring: Annotated[bool, Field(
        description=(
            "True ONLY if the contract explicitly commits the vendor to deliver this repeatedly at a defined frequency. "
            "False for one-time deliverables, general meeting mentions, or monitoring activities."
        )
    )]
    frequency: Annotated[
        Optional[Literal["WEEKLY", "BIWEEKLY", "MONTHLY", "QUARTERLY", "YEARLY"]],
        Field(default=None,
              description="The delivery frequency if is_recurring=True. Null if not recurring.")
    ]
    commitment_title: Annotated[Optional[str], Field(
        default=None,
        description="Short normalised title for this recurring commitment. Null if not recurring."
    )]
    start_date: Annotated[Optional[str], Field(
        default=None,
        description="Start date in YYYY-MM-DD format if EXPLICITLY stated in the contract. Null if not stated. NEVER invent a date."
    )]
    end_date: Annotated[Optional[str], Field(
        default=None,
        description="End date in YYYY-MM-DD format if EXPLICITLY stated. Null if not stated."
    )]
    confidence: Annotated[float, Field(
        ge=0.0, le=1.0,
        description="Confidence that this is a recurring vendor obligation. 0.9+ = explicit 'shall provide monthly X'. <0.5 = doubtful."
    )]
    reasoning: Annotated[str, Field(
        description="One sentence citing the specific contract language that supports this classification."
    )]


class RecurrenceExtractionOutput(BaseModel):
    """Output schema for RecurringDeliverableService LLM call."""
    items: List[RecurrenceItem]


# ══════════════════════════════════════════════════════════════
# RISK AGGREGATION (Step 2G executive summary)
# ══════════════════════════════════════════════════════════════

class HighestActionPriority(BaseModel):
    activity: Annotated[str, Field(description="Name of the highest priority blocker.")]
    status: Annotated[str, Field(description="Current status description e.g. 'In Progress (70%)'")]
    due_date: Annotated[Optional[str], Field(default=None, description="Due date or overdue description.")]
    reason: Annotated[str, Field(description="Bullet-point explanation of why this is top priority.")]
    recommended_action: Annotated[str, Field(description="Specific actionable step to resolve this item.")]


class ProjectExecutiveSummary(BaseModel):
    status: Annotated[str, Field(description="Project health label e.g. '✅ On Track', '⚠ At Risk', '🔴 Critical'")]
    tracked_items: Annotated[int, Field(description="Total number of tracked risk items.")]
    critical_risks: Annotated[int, Field(description="Count of CRITICAL or HIGH risk items.")]
    highest_priority: Annotated[str, Field(description="Name of the single most urgent item.")]
    progress_percent: Annotated[int, Field(
        ge=0, le=100,
        description="Milestone completion percentage. USE EXACTLY the value provided in CRITICAL METRIC RULES. Do NOT calculate or estimate."
    )]
    new_blockers: Annotated[int, Field(description="Number of new blockers detected in this document.")]
    resolved_items: Annotated[int, Field(
        description="Number of items resolved in this run. USE EXACTLY the value provided in CRITICAL METRIC RULES."
    )]
    ai_summary: Annotated[str, Field(description="Executive paragraph describing current critical path and progress.")]


class RiskAggregationOutput(BaseModel):
    """Output schema for Step 2G risk aggregation LLM call."""

    overall_risk: Annotated[
        Literal["LOW", "MEDIUM", "HIGH", "CRITICAL"],
        Field(alias="overallRisk",
              description="Overall project risk level based on the active blocker landscape.")
    ]
    risk_score: Annotated[int, Field(
        alias="riskScore", ge=0, le=100,
        description="Numeric overall risk score 0–100."
    )]
    summary: Annotated[str, Field(
        description="2-sentence summary of overall project risk status."
    )]
    project_executive_summary: ProjectExecutiveSummary
    highest_action_priority: Annotated[HighestActionPriority, Field(
        alias="highestActionPriority"
    )]
    recommendations: Annotated[List[str], Field(
        description="One specific actionable recommendation per identified risk. Plain English, no jargon."
    )]

    class Config:
        populate_by_name = True
