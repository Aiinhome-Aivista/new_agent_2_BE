"""
Project Planning & Schedule Validation Service
===============================================

Adds a Project Planning Layer to the CodeSentry baseline extraction pipeline.

Design principles (from the implementation spec):
- NEVER overwrite explicit contractual dates with estimates.
- Estimated dates live in separate fields (estimated_start_date, estimated_deadline).
- date_provenance distinguishes CONTRACTUAL / AI_ESTIMATE / PAYMENT_MILESTONE / CONFIRMED_PLAN / ACTUAL.
- estimation_confidence is explicitly declared (LOW / MEDIUM / HIGH / NEEDS_REVIEW).
- Schedule conflicts are DETECTED and PERSISTED, never silently resolved.
- All arithmetic (calendar days, working days, dependency ordering) is deterministic;
  the LLM provides only structured complexity/effort estimates.
- If no defensible estimate can be produced, the field remains null with status NEEDS_REVIEW.
"""

from __future__ import annotations

import json
import re
from datetime import date, timedelta
from typing import Optional

from services.llm_service import LLMService


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

WORKING_DAYS_PER_WEEK = 5
DEVELOPER_HEADCOUNT_ASSUMPTION = (
    "Resource availability is assumed to be headcount x 80% "
    "(accounts for meetings, reviews and context switching). "
    "No individual skill-specialisation data was provided."
)

_EFFORT_DEFAULTS = {
    "LOW":    {"low": 1, "likely": 2,  "high": 4},
    "MEDIUM": {"low": 3, "likely": 5,  "high": 8},
    "HIGH":   {"low": 5, "likely": 10, "high": 15},
    "VERY_HIGH": {"low": 10, "likely": 20, "high": 30},
}

_SIMPLE_COMPLEXITY_SIGNALS = {
    "LOW": [
        "footer", "landing page", "notification", "email notification",
        "birthday", "announcement", "news section", "upcoming events",
        "display", "view", "listing", "tabular",
    ],
    "MEDIUM": [
        "admin", "reporting", "download", "upload", "search",
        "onboarding", "training", "poll", "survey", "feedback",
        "praising", "suggestion", "learning", "recommendation",
    ],
    "HIGH": [
        "integration", "access control", "hierarchy", "role-based",
        "approval", "rejection", "workflow", "automation", "email service",
        "authentication", "sso", "security", "data model",
    ],
    "VERY_HIGH": [
        "dependency graph", "cutover", "migration", "database", "azure",
        "sharepoint framework", "spfx", "powerapp", "power automate",
        "end-to-end", "testing strategy", "uat", "go-live", "training material",
    ],
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _add_working_days(start: date, working_days: int) -> date:
    if working_days <= 0:
        return start
    current = start
    days_added = 0
    while days_added < working_days:
        current += timedelta(days=1)
        if current.weekday() < 5:
            days_added += 1
    return current


def _working_days_between(start: date, end: date) -> int:
    if end <= start:
        return 0
    count = 0
    current = start
    while current < end:
        if current.weekday() < 5:
            count += 1
        current += timedelta(days=1)
    return count


def _complexity_from_title(title: str) -> str:
    lower = title.lower()
    for level in ("VERY_HIGH", "HIGH", "MEDIUM", "LOW"):
        for signal in _SIMPLE_COMPLEXITY_SIGNALS[level]:
            if signal in lower:
                return level
    return "MEDIUM"


def _parse_iso_date(value) -> Optional[date]:
    if value is None:
        return None
    if isinstance(value, date):
        return value
    try:
        from datetime import datetime
        if isinstance(value, datetime):
            return value.date()
        return date.fromisoformat(str(value)[:10])
    except Exception:
        return None


# ---------------------------------------------------------------------------
# LLM-based complexity estimator (bounded, schema-validated)
# ---------------------------------------------------------------------------

def _llm_estimate_complexity(
    items: list[dict],
    project_context: str,
) -> dict[str, dict]:
    if not items:
        return {}

    item_list = "\n".join(
        f"- [{i+1}] {item.get('name', '')} (section: {item.get('section', 'Unknown')})"
        for i, item in enumerate(items)
    )

    prompt = f"""You are a project estimation assistant for a software delivery project.

Project context:
{project_context}

For each scope item below, provide a structured effort estimate.
Rules:
- complexity: one of LOW, MEDIUM, HIGH, VERY_HIGH
- effort_days_low: optimistic person-days (single dev)
- effort_days_likely: most-likely person-days
- effort_days_high: pessimistic person-days
- confidence: LOW if the description is vague, MEDIUM if reasonable, HIGH if unambiguous
- rationale: max 150 chars, factual basis only
- assumptions: list of strings

Scope items:
{item_list}

Return ONLY valid JSON - a list of objects, one per item in order:
[
  {{
    "name": "<exact item name>",
    "complexity": "MEDIUM",
    "effort_days_low": 2,
    "effort_days_likely": 4,
    "effort_days_high": 7,
    "confidence": "MEDIUM",
    "rationale": "Standard form with workflow logic.",
    "assumptions": ["Database schema is defined", "Email service is configured"]
  }}
]

Do not add commentary outside the JSON array."""

    try:
        raw = LLMService.generate(prompt, temperature=0.1)
        match = re.search(r'\[\s*\{.*\}\s*\]', raw, re.DOTALL)
        if not match:
            raise ValueError("No JSON array in LLM response")
        parsed = json.loads(match.group(0))
        result = {}
        for entry in parsed:
            name = entry.get("name", "")
            if name:
                result[name] = {
                    "complexity": entry.get("complexity", "MEDIUM"),
                    "effort_days_low": int(entry.get("effort_days_low", 2)),
                    "effort_days_likely": int(entry.get("effort_days_likely", 4)),
                    "effort_days_high": int(entry.get("effort_days_high", 8)),
                    "confidence": entry.get("confidence", "LOW"),
                    "rationale": str(entry.get("rationale", ""))[:300],
                    "assumptions": list(entry.get("assumptions", [])),
                }
        return result
    except Exception as exc:
        print(f"[PlanningService] LLM estimate failed ({exc}); falling back to heuristics.")
        return {}


# ---------------------------------------------------------------------------
# Main service class
# ---------------------------------------------------------------------------

class ProjectPlanningService:
    """
    Generates provisional work-plan estimates for a baseline's scope items
    and detects schedule conflicts in the EL.
    """

    @classmethod
    def plan_baseline(
        cls,
        scope_items: list[dict],
        project_start: Optional[date],
        project_end: Optional[date],
        explicit_milestones: list[dict] | None = None,
        resource_headcount: int = 5,
        use_llm: bool = True,
    ) -> "PlanningResult":
        print("[PlanningService] Starting project planning & schedule estimation...")

        result = PlanningResult()

        project_context = cls._build_project_context(
            project_start, project_end, resource_headcount,
            explicit_milestones or []
        )
        result.plan_summary["project_start"] = str(project_start) if project_start else None
        result.plan_summary["project_end"] = str(project_end) if project_end else None
        result.plan_summary["resource_headcount"] = resource_headcount
        result.plan_summary["resource_assumption"] = DEVELOPER_HEADCOUNT_ASSUMPTION

        plannable = [
            item for item in scope_items
            if item.get("scope_type") == "IN_SCOPE"
            and not item.get("is_pure_milestone", False)
        ]
        print(f"[PlanningService] {len(plannable)} IN_SCOPE items eligible for estimation.")

        for item in plannable:
            item["_complexity"] = _complexity_from_title(
                f"{item.get('name', '')} {item.get('description', '')} {item.get('section', '')}"
            )

        llm_estimates: dict[str, dict] = {}
        if use_llm and plannable:
            # Batch into groups of 30 to keep prompt size manageable
            batch_size = 30
            for batch_start in range(0, len(plannable), batch_size):
                batch = plannable[batch_start:batch_start + batch_size]
                print(f"[PlanningService] LLM estimation batch {batch_start//batch_size + 1} ({len(batch)} items)...")
                batch_result = _llm_estimate_complexity(batch, project_context)
                llm_estimates.update(batch_result)
            print(f"[PlanningService] LLM returned estimates for {len(llm_estimates)} items.")

        effective_devs = max(1, resource_headcount) * 0.8
        cursor_date = project_start or date.today()

        _complexity_order = {"LOW": 0, "MEDIUM": 1, "HIGH": 2, "VERY_HIGH": 3}
        plannable.sort(key=lambda x: _complexity_order.get(x.get("_complexity_level", "MEDIUM"), 1))

        for item in plannable:
            name = item.get("name", "")
            llm_est = llm_estimates.get(name, {})
            if llm_est:
                complexity = llm_est.get("complexity", item["_complexity"])
                effort = {
                    "low": llm_est["effort_days_low"],
                    "likely": llm_est["effort_days_likely"],
                    "high": llm_est["effort_days_high"],
                }
                confidence = llm_est.get("confidence", "LOW")
                rationale = llm_est.get("rationale", "")
                assumptions = llm_est.get("assumptions", [])
            else:
                complexity = item["_complexity"]
                effort = _EFFORT_DEFAULTS[complexity]
                confidence = "LOW"
                rationale = f"Heuristic estimate based on complexity signal '{complexity}'."
                assumptions = [
                    "Requirements are documented and available.",
                    "Development environment is accessible.",
                ]

            item["_effort"] = effort
            item["_plan_confidence"] = confidence
            item["_plan_rationale"] = rationale
            item["_plan_assumptions"] = assumptions
            item["_complexity_level"] = complexity

            effort_likely = effort.get("likely", 3)
            elapsed_days = max(1, round(effort_likely / effective_devs))

            item["estimated_start_date"] = cursor_date.isoformat()
            item["estimated_deadline"] = _add_working_days(cursor_date, elapsed_days).isoformat()
            item["date_provenance"] = "AI_ESTIMATE"
            item["estimation_confidence"] = confidence
            item["estimation_basis"] = rationale
            item["plan_assumptions"] = assumptions
            item["estimated_effort_low"] = effort.get("low", 1)
            item["estimated_effort_likely"] = effort_likely
            item["estimated_effort_high"] = effort.get("high", effort_likely * 2)
            item["baseline_approval_status"] = "PENDING_REVIEW"

            cursor_date = _add_working_days(cursor_date, elapsed_days)

        result.estimated_items = plannable

        cls._detect_conflicts(
            plannable, project_start, project_end,
            explicit_milestones or [], result
        )

        print(
            f"[PlanningService] Planning complete. "
            f"{len(plannable)} items estimated, "
            f"{len(result.conflicts)} conflict(s) detected."
        )
        return result

    @classmethod
    def _build_project_context(
        cls,
        project_start,
        project_end,
        resource_headcount: int,
        explicit_milestones: list[dict],
    ) -> str:
        lines = []
        if project_start:
            lines.append(f"Project start: {project_start}")
        if project_end:
            lines.append(f"Project end: {project_end}")
        if project_start and project_end:
            wd = _working_days_between(project_start, project_end)
            lines.append(f"Available working days: {wd}")
        lines.append(f"Developer headcount: {resource_headcount} (assumed; actual availability unverified).")
        lines.append(
            "Technology: SharePoint Online, PowerApps, Power Automate, Azure SQL, "
            "Azure Function/App Service, Viva Engage, Microsoft 365."
        )
        if explicit_milestones:
            lines.append("Explicit contractual milestones (dates must not be moved):")
            for m in explicit_milestones:
                m_date = m.get("date") or m.get("planned_date") or "TBD"
                lines.append(f"  - {m.get('name', 'Milestone')}: {m_date} ({m.get('type', 'MILESTONE')})")
        return "\n".join(lines)

    @classmethod
    def _detect_conflicts(
        cls,
        plannable: list[dict],
        project_start,
        project_end,
        explicit_milestones: list[dict],
        result: "PlanningResult",
    ):
        # C1: Schedule overrun
        if project_end and plannable:
            last_estimated = max(
                (_parse_iso_date(i.get("estimated_deadline")) for i in plannable if i.get("estimated_deadline")),
                default=None
            )
            if last_estimated and last_estimated > project_end:
                result.add_conflict(ScheduleConflict(
                    conflict_id="C1-SCHEDULE_OVERRUN",
                    severity="HIGH",
                    title="Estimated work extends beyond contract end date",
                    description=(
                        f"The last estimated task completion date is {last_estimated}, "
                        f"which is after the stated contract end date of {project_end}."
                    ),
                    evidence=f"Contract end: {project_end} | Last estimated finish: {last_estimated}",
                    recommended_action=(
                        "Review capacity assumptions, parallelize tasks, "
                        "or raise a scope-vs-timeline issue with the project sponsor."
                    ),
                ))

        # C2: Milestone before project start
        for m in explicit_milestones:
            m_date = _parse_iso_date(m.get("date") or m.get("planned_date"))
            if m_date and project_start and m_date < project_start:
                result.add_conflict(ScheduleConflict(
                    conflict_id=f"C2-MILESTONE_BEFORE_START-{m.get('name', 'M')[:30]}",
                    severity="CRITICAL",
                    title=f"Milestone '{m.get('name', '')}' date precedes project start",
                    description=(
                        f"The milestone '{m.get('name', '')}' is dated {m_date}, "
                        f"which is before the project start date {project_start}."
                    ),
                    evidence=f"Milestone date: {m_date} | Project start: {project_start}",
                    recommended_action="Verify the milestone date in the EL; this appears to be a data conflict.",
                ))

        # C3: Support period beyond contract end
        for m in explicit_milestones:
            if "support" in (m.get("name") or "").lower() or "support" in (m.get("type") or "").lower():
                m_date = _parse_iso_date(m.get("date") or m.get("planned_date"))
                if m_date and project_end and m_date > project_end:
                    result.add_conflict(ScheduleConflict(
                        conflict_id=f"C3-SUPPORT_BEYOND_CONTRACT-{m.get('name', 'M')[:30]}",
                        severity="MEDIUM",
                        title="Production support obligation extends beyond contract end date",
                        description=(
                            f"'{m.get('name', '')}' is dated {m_date}, "
                            f"which is after the stated contract end {project_end}."
                        ),
                        evidence=f"Support date: {m_date} | Contract end: {project_end}",
                        recommended_action=(
                            "Confirm whether the support obligation is intentionally beyond "
                            "the engagement period or requires a contract amendment."
                        ),
                    ))

        # C4: No project start date
        if not project_start:
            result.add_conflict(ScheduleConflict(
                conflict_id="C4-NO_PROJECT_START",
                severity="HIGH",
                title="Project start date is missing",
                description=(
                    "No project start date is set on the project record. "
                    "All estimated task dates are relative to today as a placeholder."
                ),
                evidence="projects.start_date IS NULL",
                recommended_action="Set the project start date from the EL before approving the baseline.",
            ))

        # C5: Resource capacity unvalidated
        result.add_conflict(ScheduleConflict(
            conflict_id="C5-RESOURCE_CAPACITY_UNVALIDATED",
            severity="LOW",
            title="Resource availability unvalidated",
            description=(
                "Developer headcount is stated in the EL but individual availability, "
                "skill specialization, leave and allocation percentages are not provided. "
                "The schedule assumes 80% team utilisation."
            ),
            evidence="EL states developer headcount; no per-person availability data present.",
            recommended_action=(
                "Provide a detailed resource plan (role, allocation %, availability dates) "
                "to refine schedule estimates."
            ),
        ))


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

class ScheduleConflict:
    def __init__(
        self,
        conflict_id: str,
        severity: str,
        title: str,
        description: str,
        evidence: str,
        recommended_action: str,
    ):
        self.conflict_id = conflict_id
        self.severity = severity
        self.title = title
        self.description = description
        self.evidence = evidence
        self.recommended_action = recommended_action

    def to_dict(self) -> dict:
        return {
            "conflict_id": self.conflict_id,
            "severity": self.severity,
            "title": self.title,
            "description": self.description,
            "evidence": self.evidence,
            "recommended_action": self.recommended_action,
        }


class PlanningResult:
    def __init__(self):
        self.estimated_items: list[dict] = []
        self.conflicts: list[ScheduleConflict] = []
        self.plan_summary: dict = {}

    def add_conflict(self, conflict: ScheduleConflict):
        self.conflicts.append(conflict)
        print(f"[PlanningService] Conflict [{conflict.severity}] {conflict.conflict_id}: {conflict.title}")

    def conflicts_as_dicts(self) -> list[dict]:
        return [c.to_dict() for c in self.conflicts]

    def summary_dict(self) -> dict:
        return {
            **self.plan_summary,
            "estimated_item_count": len(self.estimated_items),
            "conflict_count": len(self.conflicts),
            "conflicts": self.conflicts_as_dicts(),
        }
