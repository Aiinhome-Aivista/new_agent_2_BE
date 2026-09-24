"""
IMPROVEMENT 3: Pydantic Schemas for LLM Structured Output
FILE: agents/llm_schemas.py

Defines strongly-typed schemas for the 3 JSON-producing LLM calls:
  1. ActivityExtractorAgent  → ExtractionOutput
  2. BatchActivityRiskAgent  → RiskEvaluationOutput
  3. ScopeClassifier         → ScopeClassificationOutput

These schemas are used with langchain_google_genai's .with_structured_output()
to enforce output structure at the token level, eliminating all json.loads()
parse failures.

IMPORTANT:
- Field names EXACTLY match what the rest of the pipeline reads.
  Never rename fields — the downstream pipeline depends on them.
- temperature=0 is enforced on all extraction calls (set in the agent, not here).
- Do NOT apply structured output to alerting or narrative LLM calls — those
  produce prose, not JSON.
"""

from pydantic import BaseModel, Field
from typing import List, Optional


# ── Schema 1: ActivityExtractorAgent ─────────────────────────────────────────

class ActivityItem(BaseModel):
    """Represents a single extracted activity or deliverable from a MoM/Status Report."""
    activity: str = Field(description="Normalized business entity name (e.g. 'SAP Integration')")
    statement: str = Field(description="Original sentence from the document as evidence")
    status: str = Field(description="One of: BLOCKED, IN_PROGRESS, COMPLETED, NOT_STARTED")
    classification_type: str = Field(
        description="RISK if it represents a risk/concern, PROGRESS_UPDATE if it's informational"
    )
    blocked_by: List[str] = Field(
        default_factory=list,
        description="List of activity names that block this item"
    )
    blocks: List[str] = Field(
        default_factory=list,
        description="List of activity names that this item blocks"
    )
    confidence: float = Field(
        default=1.0,
        ge=0.0, le=1.0,
        description="Extraction confidence score between 0.0 and 1.0"
    )
    source_sentence: str = Field(
        default="",
        description="The verbatim sentence from the document this was extracted from"
    )
    due_date: Optional[str] = Field(
        default=None,
        description="Deadline if mentioned (ISO format preferred), null if not stated"
    )


class ResolvedItem(BaseModel):
    """Represents a tracker item that was resolved/completed in this document."""
    name: str = Field(description="Name of the resolved item")
    resolution_type: str = Field(
        default="COMPLETED",
        description="How it was resolved: COMPLETED, CANCELLED, MERGED"
    )
    evidence: str = Field(
        default="",
        description="Evidence sentence from document supporting the resolution"
    )


class ExtractionOutput(BaseModel):
    """Complete output of the ActivityExtractorAgent LLM call."""
    raw_activities: List[ActivityItem] = Field(
        default_factory=list,
        description="All activities/deliverables extracted from the document"
    )
    resolved_items: List[ResolvedItem] = Field(
        default_factory=list,
        description="Items that appear to be completed/resolved in this document"
    )


# ── Schema 2: BatchActivityRiskAgent ─────────────────────────────────────────

class RiskItem(BaseModel):
    """Risk evaluation result for a single activity."""
    activity: str = Field(description="The activity name being evaluated")
    matched_baseline_item: Optional[str] = Field(
        default=None,
        description="Matched baseline/scope item title, or null if no match"
    )
    risk_category: str = Field(
        description="Risk category: SCOPE_CREEP, DELAY, DEPENDENCY, BLOCKED, NONE"
    )
    risk_level: str = Field(
        description="Risk severity: LOW, MEDIUM, HIGH, CRITICAL"
    )
    execution_status: str = Field(
        description="Current execution state: NOT_STARTED, IN_PROGRESS, BLOCKED, COMPLETED, OVERDUE"
    )
    is_out_of_scope: bool = Field(
        description="True if this activity is outside the approved contract scope"
    )
    owner: str = Field(
        description="Responsible party: Internal (our team) or Customer"
    )
    confidence: float = Field(
        ge=0.0, le=1.0,
        description="LLM confidence in this diagnosis"
    )
    reasoning: str = Field(
        description="Brief explanation of why this risk level was assigned"
    )
    blocked_by: List[str] = Field(
        default_factory=list,
        description="Activities that are blocking this item"
    )
    blocks: List[str] = Field(
        default_factory=list,
        description="Activities that this item is blocking"
    )


class RiskEvaluationOutput(BaseModel):
    """Complete output of the BatchActivityRiskAgent LLM call."""
    items: List[RiskItem] = Field(
        default_factory=list,
        description="Risk evaluation results for all submitted activities"
    )


# ── Schema 3: ScopeClassifier ─────────────────────────────────────────────────

class ScopeItem(BaseModel):
    """Scope classification result for a single candidate item."""
    id: str = Field(description="The candidate ID as provided in the input batch")
    scope_type: str = Field(
        description="Classification: IN_SCOPE, OUT_OF_SCOPE, or UNCERTAIN"
    )
    confidence: float = Field(
        ge=0.0, le=1.0,
        description="Confidence in this classification"
    )
    evidence_text: str = Field(
        description="The evidence from the contract that supports this classification"
    )


class ScopeClassificationOutput(BaseModel):
    """Complete output of the ScopeClassifier batch LLM call."""
    items: List[ScopeItem] = Field(
        default_factory=list,
        description="Classification result for each candidate in the batch"
    )
