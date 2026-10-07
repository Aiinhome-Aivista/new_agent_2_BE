"""
RiskScoringEngine
=================
Phase 4 of Execution Priority Pipeline.

Execution Priority: Context-driven 9-tier system.
  Items are scored relative to what the PM needs to focus on TODAY
  to keep the project moving — not in isolation.

  TIER 0: SCOPE_CREEP              → 0–9    (out of contract)
  TIER 1: IMMEDIATE_UNLOCK         → 90–100 (directly unblocks primary target)
  TIER 2: TRANSITIVE_UNLOCK        → 80–89  (indirectly unblocks primary target)
  TIER 3: PRIMARY_TARGET           → 75–89  (the contracted milestone in focus)
  TIER 4: SECONDARY_WINDOW_UNLOCK  → 60–74  (unblocks next milestone)
  TIER 5: SECONDARY_WINDOW_READY   → 45–59  (next milestone, can start now)
  TIER 6: OVERDUE_ISOLATED         → 30–44  (past deadline, no dependency)
  TIER 7: DUE_SOON_ISOLATED        → 20–34  (upcoming deadline, no dependency)
  TIER 8: FUTURE_WORK              → 15–24  (future, no immediate connection)

  Fallback (no execution_context): DB-configured band system from
  execution_priority_band_config table (Improvement B).

Risk Severity: Independent metric — schedule + business + owner.
  DO NOT change the risk_severity formula.

All parameters that affect scoring are configurable via DB tables:
  - risk_parameter_config        — weights per scoring dimension
  - risk_threshold_config        — severity band cutoffs
  - execution_priority_band_config — band ranges (Improvement B)
"""

import re
from typing import Optional
from datetime import date as _date_type

# ─────────────────────────────────────────────────────────────────────────────
# MODULE-LEVEL REFERENCE DATE (Part A1)
# Set once per pipeline run by calling set_scoring_reference_date().
# This prevents score drift when the server processes documents at different times.
# Generic: works for any document type or project.
# ─────────────────────────────────────────────────────────────────────────────
_SCORING_REFERENCE_DATE: Optional[_date_type] = None


def set_scoring_reference_date(reference_date: _date_type) -> None:
    """
    Call this once per document processing run with the MoM's upload date.
    All scoring calculations in this run use this date as 'today'.
    This prevents score drift between server restarts or delayed processing.
    Generic: works for any document type or project.
    """
    global _SCORING_REFERENCE_DATE
    _SCORING_REFERENCE_DATE = reference_date
    print(f"  [ScoringEngine] Reference date set to: {reference_date}")


def _get_reference_date() -> _date_type:
    """Returns the current run's reference date, or today as fallback."""
    return _SCORING_REFERENCE_DATE or _date_type.today()


# ─────────────────────────────────────────────────────────────────────────────
# DATE PARSING HELPER (Part A1 fix: no more datetime.now() inside)
# ─────────────────────────────────────────────────────────────────────────────

def _parse_due_date(due_date_str: str, reference_date=None) -> Optional[int]:
    """
    Parse a due_date string into days_until_due from reference_date.

    Handles:
      - ISO dates: "2026-09-09"
      - Human dates: "09 Sep 2026", "September 9, 2026"
      - Relative: "Next week" → 7 days, "Next meeting" → 7 days, "This Friday" → ~3 days
      - Non-dates: "After CRM completion" → returns None (caller uses 9999)

    Returns:
      int days_until_due, or None if unparseable / not a date expression.
    """
    if not due_date_str or not isinstance(due_date_str, str):
        return None

    from datetime import datetime
    # PART A1 FIX: use module-level reference date, never datetime.now() directly
    today = reference_date or _get_reference_date()
    text = due_date_str.strip()

    # 1. Try ISO format: "2026-09-09"
    try:
        d = datetime.strptime(text.split(' ')[0], "%Y-%m-%d").date()
        return (d - today).days
    except (ValueError, IndexError):
        pass

    # 2. Try human date formats
    for fmt in ("%d %b %Y", "%d %B %Y", "%B %d, %Y", "%b %d, %Y",
                "%d-%b-%Y", "%d/%m/%Y", "%m/%d/%Y"):
        try:
            d = datetime.strptime(text, fmt).date()
            return (d - today).days
        except ValueError:
            continue

    # 3. Relative date expressions
    text_lower = text.lower().strip()

    # "After X completion" / "Once X is done" → not a date, skip
    if any(kw in text_lower for kw in ["after ", "once ", "upon ", "following ",
                                        "dependent on", "depends on", "when "]):
        return None

    # "Next week", "next weekly meeting" → 7 days
    if "next week" in text_lower or "next meeting" in text_lower:
        return 7

    # "This week", "this Friday" → ~3 days
    if "this week" in text_lower or "this friday" in text_lower:
        return 3

    # "Tomorrow" → 1 day
    if "tomorrow" in text_lower:
        return 1

    # "Today" → 0 days
    if text_lower in ("today", "immediately", "asap", "urgent"):
        return 0

    # "End of month" → approximate
    if "end of month" in text_lower:
        import calendar
        last_day = calendar.monthrange(today.year, today.month)[1]
        return max(1, last_day - today.day)

    # 4. Try to extract a date from within a longer string (e.g. "Deliver by 09 Sep 2026")
    date_patterns = [
        (r'(\d{4}-\d{2}-\d{2})', "%Y-%m-%d"),
        (r'(\d{1,2}\s+\w+\s+\d{4})', "%d %b %Y"),
        (r'(\d{1,2}\s+\w+\s+\d{4})', "%d %B %Y"),
    ]
    for pattern, fmt in date_patterns:
        match = re.search(pattern, text)
        if match:
            try:
                from datetime import datetime as _dt
                d = _dt.strptime(match.group(1), fmt).date()
                return (d - today).days
            except ValueError:
                continue

    # 5. Not a recognizable date expression
    return None


# ─────────────────────────────────────────────────────────────────────────────
# IMPROVEMENT A2: Schedule & Status Urgency Helpers
# Both default to weight=0.0 in DB → no effect until client enables them.
# ─────────────────────────────────────────────────────────────────────────────

def _calculate_schedule_urgency_factor(days_overdue: int, days_until_due: int) -> float:
    """
    Returns a normalised urgency factor 0.0–1.0 based on deadline proximity.
    0.0 = no urgency (due in 30+ days)
    1.0 = maximum urgency (overdue 30+ days)

    Tiers (generic — not project-specific):
      Overdue >= 30 days  → 1.0
      Overdue 14-29 days  → 0.8
      Overdue 7-13 days   → 0.6
      Overdue 1-6 days    → 0.4
      Due within 7 days   → 0.5
      Due within 14 days  → 0.35
      Due within 30 days  → 0.2
      Due in 30+ days     → 0.0
    """
    if days_overdue >= 30:   return 1.0
    if days_overdue >= 14:   return 0.8
    if days_overdue >= 7:    return 0.6
    if days_overdue >= 1:    return 0.4
    if days_until_due <= 7:  return 0.5
    if days_until_due <= 14: return 0.35
    if days_until_due <= 30: return 0.2
    return 0.0


def _calculate_status_urgency_factor(execution_status: str) -> float:
    """
    Returns urgency factor based on execution status.
    BLOCKED items are more urgent than IN_PROGRESS items
    because they have an active impediment.
    Generic: uses only status values from the Literal type set.
    """
    STATUS_URGENCY = {
        'BLOCKED':               1.0,
        'WAITING_ON_CUSTOMER':   0.9,
        'WAITING_ON_EXTERNAL':   0.8,
        'DELAYED':               0.7,
        'NOT_STARTED':           0.3,
        'IN_PROGRESS':           0.1,
        'COMPLETED':             0.0,
        'UNKNOWN':               0.0,
    }
    return STATUS_URGENCY.get((execution_status or 'UNKNOWN').upper(), 0.2)


# ─────────────────────────────────────────────────────────────────────────────
# IMPROVEMENT B: DB-driven band lookup helper
# Replaces hardcoded if/elif chain.
# ─────────────────────────────────────────────────────────────────────────────

# Hardcoded fallback — matches original bands exactly.
# Used when DB table is empty or unavailable.
_HARDCODED_BAND_FALLBACK = [
    {'band_name': 'ROOT_CAUSE_HIGH',      'graph_role': 'ROOT_CAUSE',          'cascade_min': 2,    'cascade_max': None, 'score_min': 90, 'score_max': 100, 'schedule_urgency_weight': 0.0, 'status_weight': 0.0},
    {'band_name': 'ROOT_CAUSE_MED',       'graph_role': 'ROOT_CAUSE',          'cascade_min': 1,    'cascade_max': 1,    'score_min': 80, 'score_max': 89,  'schedule_urgency_weight': 0.0, 'status_weight': 0.0},
    {'band_name': 'ROOT_CAUSE_LOW',       'graph_role': 'ROOT_CAUSE',          'cascade_min': 0,    'cascade_max': 0,    'score_min': 70, 'score_max': 79,  'schedule_urgency_weight': 0.0, 'status_weight': 0.0},
    {'band_name': 'INTERMEDIATE_BLOCKER', 'graph_role': 'INTERMEDIATE_BLOCKER', 'cascade_min': None, 'cascade_max': None, 'score_min': 60, 'score_max': 79,  'schedule_urgency_weight': 0.0, 'status_weight': 0.0},
    {'band_name': 'TERMINAL_ACTIVITY',    'graph_role': 'TERMINAL_ACTIVITY',   'cascade_min': None, 'cascade_max': None, 'score_min': 40, 'score_max': 59,  'schedule_urgency_weight': 0.0, 'status_weight': 0.0},
    {'band_name': 'ISOLATED',             'graph_role': 'ISOLATED',            'cascade_min': None, 'cascade_max': None, 'score_min': 20, 'score_max': 39,  'schedule_urgency_weight': 0.0, 'status_weight': 0.0},
    {'band_name': 'SCOPE_CREEP',          'graph_role': 'SCOPE_CREEP',         'cascade_min': None, 'cascade_max': None, 'score_min': 0,  'score_max': 9,   'schedule_urgency_weight': 0.0, 'status_weight': 0.0},
]


def _find_band(graph_role: str, cascade_count: int, band_config: list) -> dict:
    """
    Finds the matching band configuration row for a given graph_role
    and cascade_count. Checks cascade_min/cascade_max constraints.
    Returns first matching row, or a safe ISOLATED fallback.
    Generic: purely config-driven, no hardcoded role names.
    """
    cascade = cascade_count or 0
    for band in band_config:
        if band.get('graph_role') != graph_role:
            continue
        c_min = band.get('cascade_min')
        c_max = band.get('cascade_max')
        if c_min is not None and cascade < c_min:
            continue
        if c_max is not None and cascade > c_max:
            continue
        return band
    # Fallback: safe ISOLATED defaults
    return {'band_name': 'Unclassified', 'score_min': 20, 'score_max': 39,
            'schedule_urgency_weight': 0.0, 'status_weight': 0.0}


# ─────────────────────────────────────────────────────────────────────────────
# EXECUTION CONTEXT FINDER (prompt-2)
# Called ONCE per pipeline run before any item is scored.
# ─────────────────────────────────────────────────────────────────────────────

def _find_execution_context(all_tracker_items: list, reference_date) -> dict:
    """
    Finds the execution context for the current project state.

    The execution context answers:
      - What is the PRIMARY_TARGET? (next contracted milestone to complete)
      - What are the IMMEDIATE_UNLOCKs? (items directly blocking primary target)
      - What are the TRANSITIVE_UNLOCKs? (items indirectly blocking primary)
      - What are the SECONDARY_WINDOWs? (next milestones after primary)

    ALGORITHM:
      Step 1: Find all contracted milestones not yet complete, sorted by deadline.
      Step 2: Walk the dependency chain of each milestone (blocked_by recursively).
      Step 3: Nearest milestone with unresolved work = PRIMARY_TARGET.
      Step 4: Its direct blockers = IMMEDIATE_UNLOCK (depth=1).
              Its indirect blockers = TRANSITIVE_UNLOCK (depth>1).
      Step 5: Next 1–3 milestones after the primary = SECONDARY_WINDOWs.

    Generic: works for any project, any milestone chain, any dependency depth.
    No hardcoded deliverable names or project-specific logic.

    Args:
        all_tracker_items: list of tracker item dicts (OPEN items only)
        reference_date: the document upload date (NOT datetime.today())

    Returns:
        {
            'primary_target': str or None,
            'primary_deadline': date or None,
            'primary_days_until': int,
            'immediate_unlocks': set[str],       # depth=1 blockers of primary
            'transitive_unlocks': dict[str, int], # name→depth for depth>1
            'secondary_windows': list[dict],      # [{name, deadline, days_until, blockers}]
            'all_milestone_windows': list[tuple], # (deadline, name) sorted
        }
    """
    from datetime import date as date_type, datetime

    # Build lookup: name → item data
    item_map = {}
    for item in all_tracker_items:
        title = item.get('title') or item.get('name') or item.get('deliverable', '')
        if title:
            item_map[title] = item

    def get_blocked_by(name):
        """Returns list of item names that are blocking this item."""
        item = item_map.get(name, {})
        blocked_by = (
            item.get('blocked_by') or
            item.get('blocking_names') or
            item.get('direct_blocking_names') or
            item.get('blockers') or
            []
        )
        if isinstance(blocked_by, str):
            try:
                import json
                blocked_by = json.loads(blocked_by)
            except Exception:
                blocked_by = [blocked_by] if blocked_by else []
        return [b for b in (blocked_by or []) if b and b != name]

    def find_blockers_recursive(target_name, depth=0, visited=None):
        """
        Recursively finds all items in the blocking chain of target_name.
        Returns dict: {blocker_name: depth} where depth=1 is direct blocker.
        Generic: no limit on chain depth, cycle-safe via visited set.
        """
        if visited is None:
            visited = set()
        if target_name in visited or depth > 20:
            return {}
        visited.add(target_name)
        result = {}
        for blocker in get_blocked_by(target_name):
            if blocker not in result:
                result[blocker] = depth + 1
            sub = find_blockers_recursive(blocker, depth + 1, visited)
            for k, v in sub.items():
                if k not in result or v < result[k]:
                    result[k] = v
        return result

    def is_contracted_milestone(item):
        """
        True if this tracker item represents a contracted deliverable
        (in scope, has a deadline, not an action item, customer dependency, or scope creep).
        """
        if item.get('is_out_of_scope', False):
            return False
        if item.get('is_recurring') or item.get('parent_scope_item_id'):
            return False
        title = str(item.get('title') or item.get('name') or '')
        if re.search(r'[-–]\s*(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+\d{4}', title):
            return False
        entity_type = str(item.get('entity_type') or '').upper()
        if entity_type in ['ACTION_ITEM', 'TASK', 'ISSUE', 'SCOPE_REQUEST', 'COMMITMENT_RISK', 'RISK']:
            return False
        risk_cat = str(item.get('risk_category') or item.get('category') or '').upper()
        if any(cat in risk_cat for cat in ['ACTION_ITEM', 'EXECUTION_BLOCKER', 'SCOPE_CREEP', 'CHANGE_REQUEST', 'COMMITMENT_RISK']):
            return False
        graph_role = str(item.get('graph_role') or '').upper()
        if graph_role in ['SCOPE_CREEP', 'ROOT_CAUSE']:
            return False
        scope_type = str(item.get('scope_type') or '').upper()
        if scope_type == 'OUT_OF_SCOPE':
            return False
        # Must have a deadline to be an execution window
        deadline = item.get('expected_date') or item.get('planned_date') or item.get('due_date') or item.get('deadline')
        return bool(deadline)

    def parse_deadline(item):
        """Parses deadline from item dict. Returns date or None."""
        for field in ['expected_date', 'planned_date', 'due_date', 'deadline']:
            val = item.get(field)
            if val:
                if hasattr(val, 'date'):
                    return val.date()
                if isinstance(val, date_type):
                    return val
                if isinstance(val, str) and val not in ('Unknown', '', 'None'):
                    for fmt in ['%Y-%m-%d', '%d/%m/%Y', '%d %b %Y', '%d-%m-%Y']:
                        try:
                            return datetime.strptime(val.split(' ')[0], fmt).date()
                        except Exception:
                            continue
        return None

    def get_progress(item):
        """Returns progress percentage 0-100."""
        p = item.get('progress_percent') or item.get('progress') or 0
        try:
            return min(100, max(0, int(float(p))))
        except Exception:
            return 0

    ref = reference_date or _get_reference_date()

    # STEP 1: Find all contracted milestone windows sorted by deadline
    milestone_windows = []
    for item in all_tracker_items:
        if not is_contracted_milestone(item):
            continue
        deadline = parse_deadline(item)
        if not deadline:
            continue
        progress = get_progress(item)
        if progress >= 100:
            continue  # Already done
        title = item.get('title') or item.get('name') or item.get('deliverable', '')
        if title:
            days_until = (deadline - ref).days
            milestone_windows.append((deadline, days_until, title, progress))

    milestone_windows.sort(key=lambda x: x[0])  # sort by deadline (nearest first)

    # STEP 2: Find primary target = nearest milestone with active blockers
    primary_target = None
    primary_deadline = None
    primary_days_until = 9999
    primary_blockers_all = {}
    target_candidate = None

    for deadline, days_until, m_name, progress in milestone_windows:
        blockers = find_blockers_recursive(m_name)
        if blockers:
            primary_target = m_name
            primary_deadline = deadline
            primary_days_until = days_until
            primary_blockers_all = blockers
            break
        elif target_candidate is None:
            target_candidate = (deadline, days_until, m_name, blockers)

    if not primary_target and target_candidate:
        primary_deadline, primary_days_until, primary_target, primary_blockers_all = target_candidate

    if not primary_target:
        return {
            'primary_target': None,
            'primary_deadline': None,
            'primary_days_until': 9999,
            'immediate_unlocks': set(),
            'transitive_unlocks': {},
            'secondary_windows': [],
            'all_milestone_windows': milestone_windows,
        }

    # STEP 3: Separate immediate (depth=1) from transitive (depth>1) unlocks
    immediate_unlocks = {
        name for name, depth in primary_blockers_all.items() if depth == 1
    }
    transitive_unlocks = {
        name: depth for name, depth in primary_blockers_all.items() if depth > 1
    }

    # STEP 4: Find secondary windows (next 3 milestones after primary)
    secondary_windows = []
    for deadline, days_until, m_name, progress in milestone_windows:
        if m_name == primary_target:
            continue
        blockers = find_blockers_recursive(m_name)
        secondary_windows.append({
            'name': m_name,
            'deadline': deadline,
            'days_until': days_until,
            'blockers': blockers,
            'progress': progress,
        })
        if len(secondary_windows) >= 3:
            break

    print(f"  [ExecutionContext] Primary target: '{primary_target}' "
          f"(due in {primary_days_until}d, "
          f"{len(immediate_unlocks)} immediate unlock(s), "
          f"{len(transitive_unlocks)} transitive unlock(s))")

    return {
        'primary_target': primary_target,
        'primary_deadline': primary_deadline,
        'primary_days_until': primary_days_until,
        'immediate_unlocks': immediate_unlocks,
        'transitive_unlocks': transitive_unlocks,
        'secondary_windows': secondary_windows,
        'all_milestone_windows': milestone_windows,
    }


# ─────────────────────────────────────────────────────────────────────────────
# 9-TIER EXECUTION PRIORITY CALCULATOR (prompt-2)
# ─────────────────────────────────────────────────────────────────────────────

def _calculate_execution_priority(
    item_title: str,
    graph_role: str,
    cascade_count: int,
    is_scope_creep: bool,
    days_overdue: int,
    days_until_due: int,
    progress_percent: int,
    execution_unlock_count: int,
    cascade_depth: int,
    criticality_score: float,
    critical_path: bool,
    resolution_effort: str,
    execution_status: str,
    execution_context: dict,
    risk_params: dict,
    band_config: list,
) -> tuple:
    """
    Calculates execution_priority_score based on what the item unlocks
    relative to the current execution context.

    When execution_context is non-empty, uses the 9-tier context-driven system.
    When execution_context is empty ({}), falls back to the DB-configured
    band system (Improvement B) — backward compatible with existing tests.

    Returns: (score: int, reason: str)
    """
    ctx = execution_context or {}
    primary_target = ctx.get('primary_target')
    immediate_unlocks = ctx.get('immediate_unlocks') or set()
    transitive_unlocks = ctx.get('transitive_unlocks') or {}
    secondary_windows = ctx.get('secondary_windows') or []

    effort_mult = {'XS': 1.5, 'S': 1.2, 'M': 1.0, 'L': 0.8, 'XL': 0.5}.get(
        str(resolution_effort or 'M'), 1.0
    )
    days_over = max(0, days_overdue or 0)
    days_left = days_until_due if (days_until_due or 9999) < 9999 else 9999

    # ── TIER 0: SCOPE CREEP ──────────────────────────────────────────────────
    if is_scope_creep or graph_role == 'SCOPE_CREEP':
        return 2, 'SCOPE_CREEP: Outside contracted scope. Requires formal Change Request.'

    # ── CONTEXT-DRIVEN TIERS (only when execution_context is populated) ──────
    if primary_target:

        # TIER 1: IMMEDIATE_UNLOCK
        # Directly blocks the primary target (depth=1 in the blocker chain)
        if item_title in immediate_unlocks:
            base = 90
            band_range = 10
            primary_days = ctx.get('primary_days_until', 9999)
            deadline_urgency = 0.0
            if primary_days <= 0:    deadline_urgency = 1.0
            elif primary_days <= 3:  deadline_urgency = 0.8
            elif primary_days <= 7:  deadline_urgency = 0.6
            elif primary_days <= 14: deadline_urgency = 0.3
            own_overdue = min(days_over * 0.1, 0.3)
            bonus = (deadline_urgency + own_overdue) * band_range * effort_mult
            score = min(100, int(base + min(bonus, band_range)))
            return score, (
                f'IMMEDIATE_UNLOCK: Directly blocks \'{primary_target}\'. '
                f'Resolving this immediately unblocks the primary execution target.'
            )

        # TIER 2: TRANSITIVE_UNLOCK
        if item_title in transitive_unlocks:
            depth = transitive_unlocks[item_title]
            base = 80
            band_range = 9
            depth_bonus = max(0, (5 - depth) / 5.0) * band_range * 0.5
            bonus = depth_bonus * effort_mult
            score = min(89, int(base + min(bonus, band_range)))
            return score, (
                f'TRANSITIVE_UNLOCK: Indirectly blocks \'{primary_target}\' '
                f'(chain depth={depth}). Resolving this contributes to unblocking '
                f'the primary execution target.'
            )

        # TIER 3: PRIMARY_TARGET
        if item_title == primary_target:
            base = 75
            band_range = 14
            progress_bonus = (progress_percent or 0) / 100.0 * band_range * 0.5
            deadline_bonus = 0.0
            if days_left <= 0:    deadline_bonus = band_range * 0.4
            elif days_left <= 3:  deadline_bonus = band_range * 0.35
            elif days_left <= 7:  deadline_bonus = band_range * 0.25
            elif days_left <= 14: deadline_bonus = band_range * 0.15
            bonus = (progress_bonus + deadline_bonus) * effort_mult
            score = min(89, int(base + min(bonus, band_range)))
            return score, (
                f'PRIMARY_TARGET: This is the primary execution focus '
                f'({progress_percent or 0}% complete, '
                f'{abs(days_left)}d {"overdue" if days_left <= 0 else "remaining"}).'
            )

        # TIER 4: SECONDARY_WINDOW_UNLOCK
        for sw in secondary_windows:
            sw_blockers = sw.get('blockers', {})
            if item_title in sw_blockers:
                sw_days = sw.get('days_until', 9999)
                base = 60
                band_range = 14
                if sw_days <= 7:    urgency = 0.9
                elif sw_days <= 14: urgency = 0.7
                elif sw_days <= 30: urgency = 0.4
                else:               urgency = 0.2
                bonus = urgency * band_range * effort_mult
                score = min(74, int(base + min(bonus, band_range)))
                return score, (
                    f'SECONDARY_UNLOCK: Needed to unblock \'{sw["name"]}\' '
                    f'({sw_days}d away). Focus on this after primary target is resolved.'
                )

        # TIER 5: SECONDARY_WINDOW_READY
        for sw in secondary_windows:
            if item_title == sw['name'] and not sw.get('blockers'):
                sw_days = sw.get('days_until', 9999)
                base = 45
                band_range = 14
                if sw_days <= 0:    urgency = 1.0
                elif sw_days <= 7:  urgency = 0.8
                elif sw_days <= 14: urgency = 0.6
                elif sw_days <= 30: urgency = 0.3
                else:               urgency = 0.1
                progress_bonus = (progress_percent or 0) / 100.0 * band_range * 0.3
                bonus = (urgency * band_range * 0.7 + progress_bonus) * effort_mult
                score = min(59, int(base + min(bonus, band_range)))
                return score, (
                    f'READY_TO_START: \'{item_title}\' has no active blockers and '
                    f'is due in {sw_days}d. Can begin immediately.'
                )

    # ── TIER 6: OVERDUE_ISOLATED ─────────────────────────────────────────────
    if days_over > 0:
        base = 30
        band_range = 14
        overdue_urgency = min(days_over / 30.0, 1.0)
        progress_factor = (1.0 - (progress_percent or 0) / 100.0) * 0.3
        bonus = (overdue_urgency + progress_factor) * band_range * effort_mult
        score = min(44, int(base + min(bonus, band_range)))
        return score, (
            f'OVERDUE: {days_over} day(s) past deadline with no dependency '
            f'relationship to current execution chain. Review and reassign priority.'
        )

    # ── TIER 7: DUE_SOON_ISOLATED ────────────────────────────────────────────
    if 0 < days_left <= 30:
        base = 20
        band_range = 14
        if days_left <= 7:    urgency = 0.9
        elif days_left <= 14: urgency = 0.6
        elif days_left <= 30: urgency = 0.3
        else:                 urgency = 0.0
        bonus = urgency * band_range * effort_mult
        score = min(34, int(base + min(bonus, band_range)))
        return score, (
            f'DUE_SOON: Due in {days_left}d with no dependency on current '
            f'execution chain. Monitor; will promote when primary target resolves.'
        )

    # ── TIER 8 / FALLBACK: DB-configured band system (Improvement B) ─────────
    # Also handles all cases when execution_context is empty ({}).
    band = _find_band(graph_role, cascade_count, band_config or _HARDCODED_BAND_FALLBACK)
    min_prio = band.get('score_min', 15)
    max_prio = band.get('score_max', 24)
    b_range = max_prio - min_prio

    # Bonus points within the fallback band range
    bonus_points = 0.0

    if execution_unlock_count > 0:
        bonus_points += min(execution_unlock_count * 2.0, b_range * 0.4)
    if cascade_depth > 0:
        bonus_points += min(cascade_depth * 1.5, b_range * 0.3)
    if criticality_score > 0:
        bonus_points += min(criticality_score * 0.1, b_range * 0.2)
    if critical_path:
        bonus_points += min(3.0, b_range * 0.05)

    # Improvement A2: schedule urgency bonus (weight from DB config; default 0.0)
    schedule_weight = float((risk_params or {}).get('SCHEDULE_URGENCY', {}).get('weight', 0.0))
    if schedule_weight > 0:
        urgency_factor = _calculate_schedule_urgency_factor(days_over, days_left if days_left < 9999 else 999)
        schedule_bonus = urgency_factor * b_range * min(schedule_weight * 0.4, 0.4)
        bonus_points += min(schedule_bonus, b_range * 0.4)

    # Improvement A2: status urgency bonus (weight from DB config; default 0.0)
    status_weight = float((risk_params or {}).get('STATUS_URGENCY', {}).get('weight', 0.0))
    if status_weight > 0:
        status_factor = _calculate_status_urgency_factor(execution_status or 'UNKNOWN')
        status_bonus = status_factor * b_range * min(status_weight * 0.25, 0.25)
        bonus_points += min(status_bonus, b_range * 0.25)

    bonus_points *= effort_mult
    score = min_prio + min(bonus_points, b_range)
    score = max(min(round(score), 100), 0)

    band_name = band.get('band_name', graph_role)
    return score, f'FUTURE_WORK [{band_name}]: Not on current critical path. Will be promoted when closer milestones are resolved.'


# ─────────────────────────────────────────────────────────────────────────────
# MAIN SCORING ENGINE CLASS
# ─────────────────────────────────────────────────────────────────────────────

class RiskScoringEngine:
    @classmethod
    def calculate(
        cls,
        status: str,
        blocked_by: list,
        cascade_depth: int = 0,
        blocked_work_count: int = 0,
        execution_unlock_count: int = 0,
        critical_chain: bool = False,
        dependency_source: str = "ENGINEERING",
        days_overdue: int = 0,
        days_until_due: int = 9999,
        is_scope_creep: bool = False,
        confidence: float = 1.0,
        business_impact: str = "MEDIUM",
        params: dict = None,
        impact_matrix: dict = None,
        item_name: str = None,
        category: str = "GENERAL",
        immediate_unlocks: list = None,
        future_unlocks: list = None,
        next_milestone_name: str = None,
        next_milestone_date: str = None,
        days_to_next_milestone: int = None,
        critical_path: bool = False,
        distance_to_next_executable: int = 999,
        earliest_root_cause: bool = False,
        dependency_owner: str = "Internal",
        resolution_effort: str = "M",
        business_criticality: str = "Medium",
        business_phase: str = "Execution",
        criticality_score: float = 0.0,
        parallel_stream: str = "Stream 1",
        # Band system parameters
        graph_role: str = "ISOLATED",
        due_date: str = None,
        cascade_count: int = None,
        # NEW (prompt-2): execution context for context-driven scoring
        execution_context: dict = None,
        # NEW (Improvement A2): execution status for status-urgency bonus
        execution_status: str = None,
        # NEW (Improvement B): DB-loaded band config
        band_config: list = None,
        # NEW: progress for tier bonuses
        progress_percent: int = 0,
        # risk_params from DB (passed through for weight lookups)
        risk_params: dict = None,
    ) -> dict:
        """
        Dual Metric Architecture: Calculates Execution Priority AND Risk Severity separately.

        Execution Priority: Context-driven 9-tier system (prompt-2).
          Falls back to DB-configured band system (Improvement B) when no context.
        Risk Severity: Driven by schedule urgency + business criticality + owner.
          (Unchanged — DO NOT modify this formula.)

        All weighting parameters (schedule_urgency_weight, status_weight, band ranges)
        are configurable via DB tables — no code deploy needed.

        This function is STATELESS — each item is scored independently.
        """
        # Normalize cascade_count
        effective_cascade = cascade_count if cascade_count is not None else blocked_work_count

        # Use provided band_config or hardcoded fallback
        effective_band_config = band_config or _HARDCODED_BAND_FALLBACK

        execution_reasons = []
        score_breakdown = {}

        # ────────────────────────────────────────────────────────────────────
        # 1. EXECUTION PRIORITY — Context-driven 9-tier system
        # ────────────────────────────────────────────────────────────────────

        execution_priority, exec_priority_reason = _calculate_execution_priority(
            item_title=item_name or '',
            graph_role=graph_role,
            cascade_count=effective_cascade,
            is_scope_creep=is_scope_creep or category in ("SCOPE_CREEP", "CHANGE_REQUEST"),
            days_overdue=days_overdue or 0,
            days_until_due=days_until_due if (days_until_due or 9999) < 9999 else 9999,
            progress_percent=progress_percent or 0,
            execution_unlock_count=execution_unlock_count or 0,
            cascade_depth=cascade_depth or 0,
            criticality_score=criticality_score or 0.0,
            critical_path=critical_path or False,
            resolution_effort=resolution_effort or 'M',
            execution_status=execution_status or status or 'UNKNOWN',
            execution_context=execution_context or {},
            risk_params=risk_params or params or {},
            band_config=effective_band_config,
        )

        execution_priority = max(min(int(execution_priority), 100), 0)

        # Build human-readable execution reasons list (backward-compat field)
        execution_reasons.append(exec_priority_reason)
        if earliest_root_cause:
            execution_reasons.append("✓ Earliest Root Cause")
        if resolution_effort in ["XS", "S"]:
            execution_reasons.append("✓ Quick Resolution Effort")
        if critical_path:
            execution_reasons.append("✓ On the Critical Path to Go-Live")

        # Determine band name for score_breakdown (for UI display)
        if is_scope_creep or category in ("SCOPE_CREEP", "CHANGE_REQUEST"):
            band_name = "Scope Creep / Change Request"
        else:
            ctx = execution_context or {}
            primary_target = ctx.get('primary_target')
            item_n = item_name or ''
            immediate_unlocks_set = ctx.get('immediate_unlocks') or set()
            transitive_unlocks_map = ctx.get('transitive_unlocks') or {}
            if primary_target and item_n in immediate_unlocks_set:
                band_name = "Immediate Unlock"
            elif primary_target and item_n in transitive_unlocks_map:
                band_name = "Transitive Unlock"
            elif primary_target and item_n == primary_target:
                band_name = "Primary Target"
            else:
                fb = _find_band(graph_role, effective_cascade, effective_band_config)
                band_name = fb.get('band_name', graph_role)

        score_breakdown["Execution Band"] = band_name
        score_breakdown["Execution Reason"] = exec_priority_reason
        score_breakdown["Bonus Points"] = 0.0  # kept for backward-compat
        score_breakdown["Effort Multiplier"] = {'XS': 1.5, 'S': 1.2, 'M': 1.0, 'L': 0.8, 'XL': 0.5}.get(resolution_effort, 1.0)

        # ────────────────────────────────────────────────────────────────────
        # 2. RISK SEVERITY — Schedule Urgency + Business Criticality + Owner
        #    UNCHANGED — do not modify this formula.
        # ────────────────────────────────────────────────────────────────────

        # due_date fallback: if days_until_due is still 9999, try parsing due_date
        effective_days_until_due = days_until_due
        if effective_days_until_due >= 9999 and due_date:
            parsed_days = _parse_due_date(due_date)
            if parsed_days is not None:
                effective_days_until_due = parsed_days
                score_breakdown["Due Date Source"] = f"LLM extracted: {due_date} → {parsed_days} days"

        # Expose parsed_days_until_due so caller can sync days_until_due
        score_breakdown["parsed_days_until_due"] = effective_days_until_due if effective_days_until_due < 9999 else None

        days_to_use = days_to_next_milestone if days_to_next_milestone is not None else effective_days_until_due

        schedule_impact = 0
        if days_to_use is not None:
            if days_to_use <= 0:
                schedule_impact = 100  # Overdue
            elif days_to_use <= 7:
                schedule_impact = 80   # Due within 1 week
            elif days_to_use <= 14:
                schedule_impact = 60   # Due within 2 weeks
            elif days_to_use <= 30:
                schedule_impact = 40   # Due within 1 month
            else:
                schedule_impact = 20   # Due later

        b_score_ratio = {
            "Mission Critical": 1.0,
            "High": 0.8,
            "Medium": 0.5,
            "Low": 0.2
        }.get(business_criticality, 0.5)
        business_impact_score = b_score_ratio * 100

        owner_impact = 100 if dependency_owner in ["Customer", "Vendor"] else 50

        risk_severity = (schedule_impact * 0.40) + (business_impact_score * 0.40) + (owner_impact * 0.20)
        risk_severity = max(min(round(risk_severity), 100), 0)

        # Enforce high risk severity for Scope Creep (unbilled work / revenue leakage)
        if is_scope_creep:
            risk_severity = max(risk_severity, 85)

        # Populate score breakdown for traceability
        score_breakdown["Criticality Score"] = round(criticality_score, 1)
        score_breakdown["Resolution Effort"] = resolution_effort
        score_breakdown["Business Criticality"] = business_criticality
        score_breakdown["Dependency Owner"] = dependency_owner
        score_breakdown["Schedule Impact"] = schedule_impact
        score_breakdown["Days Until Due"] = effective_days_until_due
        score_breakdown["Graph Role"] = graph_role
        score_breakdown["Cascade Count"] = effective_cascade

        # Remove duplicate execution reasons
        unique_reasons = []
        for r in execution_reasons:
            if r not in unique_reasons:
                unique_reasons.append(r)

        return {
            "execution_priority": execution_priority,
            "risk_severity": risk_severity,
            "cascade_priority": cascade_depth,
            "schedule_priority": schedule_impact,
            "score_breakdown": score_breakdown,
            "earliest_root_cause": earliest_root_cause,
            "execution_reasons": unique_reasons,
            "execution_band_name": band_name,
            "exec_priority_reason": exec_priority_reason,  # NEW: PM-readable reason
        }

    @classmethod
    def format_reasoning(
        cls,
        score: int,
        severity: str,
        category: str,
        entity_type: str,
        status: str,
        progress: int,
        earliest_root_cause: bool,
        cascade_count: int,
        blocked_by: list,
        blocking: list,
        direct_blocking: list,
        breakdown: dict,
        mom_evidence: str,
        original_contract_sentence: str = None,
        immediate_unlocks: list = None,
        future_unlocks: list = None,
        longest_path: list = None,
        next_milestone_name: str = None,
        next_milestone_date: str = None,
        days_to_next_milestone: int = None,
        execution_priority: int = 0,
        cascade_priority: int = 0,
        schedule_priority: int = 0,
        execution_reasons: list = None,
        exec_priority_reason: str = None,
        **kwargs
    ) -> str:
        """
        Formats the full evidence-backed reasoning string stored in tracker_items.reasoning
        """
        if "narratives" in kwargs and kwargs["narratives"]:
            import json
            payload = kwargs["narratives"]
            payload["_type"] = "pmo_narrative"

            if original_contract_sentence:
                payload["original_contract_sentence"] = original_contract_sentence
            if mom_evidence:
                payload["mom_evidence"] = mom_evidence
            if longest_path:
                payload["execution_chain"] = longest_path
            if exec_priority_reason:
                payload["exec_priority_reason"] = exec_priority_reason

            return json.dumps(payload)

        # Legacy fallback
        lines = []
        if status == 'RESOLVED' or category == 'RESOLVED':
            lines.append("Current Status\n• Resolved\n")
            lines.append(f'Evidence\n"{mom_evidence}"')
            return "\n".join(lines)

        lines.append(f"Current Status: {status.replace('_', ' ').title()} {f'({progress}%)' if progress is not None else ''}\n")

        if original_contract_sentence:
            lines.append("------------------------\nOriginal Contract\n" + f'"{original_contract_sentence}"\n')

        if mom_evidence:
            clean = mom_evidence.replace("Evidence (MoM)", "").replace("Evidence:", "").strip()
            if clean.startswith('"') and clean.endswith('"'): clean = clean[1:-1].strip()
            if not original_contract_sentence or clean.lower() != original_contract_sentence.lower():
                lines.append(f'------------------------\nEvidence (MoM)\n"{clean}"')

        return "\n".join(lines).strip()
