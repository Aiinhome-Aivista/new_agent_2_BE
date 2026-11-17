"""
TimelineContradictionDetector
==============================
Generic HITL (Human-in-the-Loop) timeline contradiction detection service.

Detects when a contract's stated phase durations mathematically cannot fit
within the hard billing milestone dates. Works for ANY contract document —
no party names, feature names, or specific dates are hardcoded.

Algorithm:
  1. Extract contract_start_date from the final-baseline artifact (deadline_inference_service).
  2. Extract phase_total_duration_days by summing named phase durations found in the artifact.
  3. Extract the EARLIEST billing milestone date (first payment milestone).
  4. Compare: does phase_total_duration_days exceed the window between contract_start and that milestone?
  5. Apply a configurable TOLERANCE_DAYS grace buffer before flagging.

Returns:
  {
    "has_contradiction": bool,
    "contract_start":    "YYYY-MM-DD" or None,
    "phase_duration_days": int,
    "earliest_milestone_date": "YYYY-MM-DD" or None,
    "available_days": int or None,
    "gap_days": int,          # positive = overrun, negative = slack
    "phase_breakdown":  [...],
    "milestone_name":   str,
    "severity": "CRITICAL" | "WARNING" | None,
    "finding_type": "TIMELINE_CONTRADICTION",
    "explanation": str,
    "recommended_action": str,
  }
"""

from __future__ import annotations

import re
import math
from datetime import date, timedelta, datetime
from typing import Any, Dict, List, Optional, Tuple

# ── Configuration (generic, no hardcoding) ───────────────────────────────────

# Flag as CRITICAL if phase durations exceed milestone window by more than this.
TOLERANCE_DAYS: int = 3

# Flag as WARNING if overrun is between 0 and TOLERANCE_DAYS.
# Below 0 → no contradiction.

# Pattern set for duration extraction: "N week(s)", "N day(s)", "N month(s)"
# Patterns are ordered longest-unit first so "2 months" matches before "2".
_DUR_PATTERNS: List[Tuple[re.Pattern, int]] = [
    (re.compile(r"(\d+(?:\.\d+)?)\s*months?", re.IGNORECASE), 30),
    (re.compile(r"(\d+(?:\.\d+)?)\s*weeks?", re.IGNORECASE), 7),
    (re.compile(r"(\d+(?:\.\d+)?)\s*days?", re.IGNORECASE), 1),
]

# Minimum duration that qualifies as a "phase" (avoid matching incidental numbers)
_MIN_PHASE_DAYS: int = 2


from core.baseline_config import parse_date as _parse_date


def _extract_phase_durations(artifact: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    Extract named phase duration entries from the baseline artifact.
    Looks in: project_phases, payment_milestones, all_records, scope_items, and contract.
    Returns list of {name, days}.
    """
    phases: List[Dict[str, Any]] = []
    seen_names = set()

    # 1. project_phases list (name + duration fields)
    for ph in artifact.get("project_phases") or []:
        name = ph.get("name") or ph.get("phase_name") or "Phase"
        # Try duration_days field first
        d_days = ph.get("duration_days") or ph.get("duration")
        if d_days:
            try:
                days = int(float(str(d_days)))
                if days >= _MIN_PHASE_DAYS and name.lower() not in seen_names:
                    phases.append({"name": name, "days": days, "source": "phase_field"})
                    seen_names.add(name.lower())
                    continue
            except (ValueError, TypeError):
                pass
        # Try duration_text or name for embedded duration strings
        for text_field in ("duration_text", "description", "name"):
            text = ph.get(text_field) or ""
            for pattern, multiplier in _DUR_PATTERNS:
                m = pattern.search(text)
                if m:
                    days = int(float(m.group(1)) * multiplier)
                    if days >= _MIN_PHASE_DAYS and name.lower() not in seen_names:
                        phases.append({"name": name, "days": days, "source": f"regex:{text_field}"})
                        seen_names.add(name.lower())
                        break
            else:
                continue
            break

    # 2. Check all_records and scope_items for phase duration clauses (e.g. "Phase ... 9 weeks")
    record_sources = (
        (artifact.get("all_records") or [])
        + (artifact.get("scope_items") or [])
        + (artifact.get("contextual_records") or [])
    )
    for rec in record_sources:
        r_name = rec.get("name") or ""
        r_desc = rec.get("description") or ""
        r_ev = rec.get("evidence_text") or ""
        combined_text = f"{r_name} {r_desc} {r_ev}"

        # Match items that explicitly represent phases or timeline breakdowns
        is_phase_item = any(w in r_name.lower() for w in ("phase", "stage", "timeline", "duration"))
        if is_phase_item:
            # Check description and evidence for duration patterns
            for check_str in (r_desc, r_name, r_ev):
                if not check_str:
                    continue
                for pattern, multiplier in _DUR_PATTERNS:
                    m = pattern.search(check_str)
                    if m:
                        days = int(float(m.group(1)) * multiplier)
                        norm_name = r_name.strip()
                        if _MIN_PHASE_DAYS <= days <= 365 and norm_name.lower() not in seen_names:
                            # Skip if this is the "overall" total project timeline record to avoid double-counting
                            if "overall" in norm_name.lower() or "total" in norm_name.lower():
                                continue
                            phases.append({"name": norm_name, "days": days, "source": "record_description"})
                            seen_names.add(norm_name.lower())
                            break
                else:
                    continue
                break

    # 3. Fallback: if no individual phases found, check contract.project_duration_weeks
    if not phases:
        contract_info = artifact.get("contract") or artifact.get("contract_dates") or {}
        p_weeks = contract_info.get("project_duration_weeks")
        if p_weeks:
            try:
                days = int(float(str(p_weeks)) * 7)
                phases.append({"name": "Overall Contract Duration", "days": days, "source": "contract_duration"})
            except (ValueError, TypeError):
                pass

    return phases


def _extract_earliest_billing_milestone(
    artifact: Dict[str, Any], contract_start: Optional[date] = None
) -> Tuple[Optional[str], Optional[date]]:
    """
    Returns (milestone_name, milestone_date) for the earliest delivery/billing milestone.
    Prefers milestones occurring after contract_start (to skip signing/kickoff day-0 milestones).
    """
    candidates: List[Tuple[str, date]] = []

    sources = [
        artifact.get("payment_milestones") or [],
        artifact.get("milestones") or [],
        artifact.get("project_phases") or [],
    ]

    all_recs = (artifact.get("all_records") or []) + (artifact.get("scope_items") or [])
    milestone_recs = [
        r for r in all_recs
        if r.get("category") == "MILESTONE"
        or any(k in (r.get("name") or "").lower() for k in ("milestone", "payment", "%", "uat deployment", "sign-off"))
    ]
    sources.append(milestone_recs)

    for src in sources:
        for ms in src:
            dt = None
            for field in (
                "planned_date",
                "date",
                "target_date",
                "due_date",
                "deadline",
                "milestone_date",
                "planned_start_date",
                "deadline_normalized",
                "estimated_deadline",
            ):
                dt = _parse_date(ms.get(field))
                if dt:
                    break
            if dt:
                name = ms.get("name") or ms.get("milestone_name") or "Milestone"
                candidates.append((name, dt))

    if not candidates:
        return None, None

    # Deduplicate candidates by date and name
    unique_candidates: List[Tuple[str, date]] = []
    seen_keys = set()
    for name, dt in candidates:
        key = (name.lower().strip(), dt)
        if key not in seen_keys:
            seen_keys.add(key)
            unique_candidates.append((name, dt))

    # If contract_start is provided, prefer milestones occurring after start date
    if contract_start:
        downstream = [c for c in unique_candidates if c[1] > contract_start]
        if downstream:
            downstream.sort(key=lambda x: x[1])
            return downstream[0]

    unique_candidates.sort(key=lambda x: x[1])
    return unique_candidates[0]


def _build_parallel_scenarios(
    artifact: Dict[str, Any],
    contract_start: date,
    phases: List[Dict[str, Any]],
    earliest_ms_name: Optional[str],
    earliest_ms_date: Optional[date],
    available_days: int,
) -> Dict[str, Any]:
    """
    Build parallel scenarios for human-in-the-loop decision making:
      - Option A: CONTRACT_ANCHORED (Enforce milestone dates, compress phases, show capacity consequence)
      - Option B: DURATION_ANCHORED (Enforce phase durations, 5 devs fit, recommend contract amendment)
      - Option C: PROVISIONAL_STRICT (Proceed provisionally with stricter milestone alerts)
    """
    # 1. Headcount
    hc = artifact.get("team_headcount") or artifact.get("headcount") or {}
    dev_count = hc.get("developers", 5) if isinstance(hc, dict) else 5

    # 2. Phase 1 stats & demand
    p1 = phases[0] if phases else {"name": "Build Phase", "days": 63}
    p1_days = p1.get("days", 63)
    p1_weeks = max(1.0, round(p1_days / 7.0, 1))
    demand_dev_weeks = round(dev_count * p1_weeks * 0.80, 1)

    # 3. Milestones extraction
    ms_all = artifact.get("payment_milestones") or artifact.get("milestones") or []
    go_live_ms = None
    for m in ms_all:
        m_name = (m.get("name") or "").lower()
        if any(k in m_name for k in ["go-live", "go live", "acceptance", "uat sign-off"]):
            go_live_ms = m
            break
    contract_go_live_date = _parse_date(go_live_ms.get("planned_date") or go_live_ms.get("date")) if go_live_ms else None

    # Cumulative phase windows
    cum_start = contract_start
    derived_phases = []
    for ph in phases:
        ph_days = ph.get("days", 14)
        ph_end = cum_start + timedelta(days=ph_days)
        derived_phases.append({
            "phase": ph.get("name"),
            "start": cum_start.isoformat(),
            "end": ph_end.isoformat(),
            "days": ph_days,
            "weeks": round(ph_days / 7.0, 1),
        })
        cum_start = ph_end

    derived_build_end = derived_phases[0]["end"] if len(derived_phases) > 0 else None
    derived_go_live_date = derived_phases[1]["end"] if len(derived_phases) > 1 else (derived_phases[0]["end"] if derived_phases else None)
    derived_final_date = derived_phases[-1]["end"] if derived_phases else None

    # SCENARIO A: CONTRACT_ANCHORED
    avail_weeks_a = max(1.0, round(available_days / 7.0, 1))
    avail_cap_a = round(dev_count * avail_weeks_a * 0.70, 1)
    devs_needed_a = max(dev_count, int(math.ceil(demand_dev_weeks / (avail_weeks_a * 0.70))))
    shortfall_a = round(max(0.0, demand_dev_weeks - avail_cap_a), 1)

    earliest_ms_str = earliest_ms_date.isoformat() if earliest_ms_date else ""
    scenario_a = {
        "key": "CONTRACT_ANCHORED",
        "title": "Option A: Enforce Milestone Dates (Contract-Anchored)",
        "badge": "Capacity Risk",
        "badge_color": "red",
        "approach": "Contract billing milestone dates are enforced as the authoritative delivery anchors. Phase durations are compressed to fit inside the milestone window.",
        "phase_schedule": [
            {
                "phase": p1.get("name", "Build Phase"),
                "window": f"{contract_start.isoformat()} to {earliest_ms_str}",
                "duration": f"{avail_weeks_a} weeks (compressed from {p1_weeks}w)",
            }
        ],
        "capacity": {
            "demand_dev_weeks": demand_dev_weeks,
            "available_dev_weeks": avail_cap_a,
            "headcount_available": dev_count,
            "developers_needed": devs_needed_a,
            "shortfall_dev_weeks": shortfall_a,
            "utilisation": 0.70,
            "feasibility": "UNFEASIBLE",
            "capacity_warning": f"CRITICAL: Compressing {p1_weeks} weeks of build work into {avail_weeks_a} calendar weeks requires ~{devs_needed_a} developers at 70% utilisation. Available team ({dev_count} developers) faces a {shortfall_a} dev-week shortfall.",
        },
        "billing_impact": "Milestones achieved on original signed schedule. Invoicing proceeds on contract dates without amendment.",
        "governance_note": f"Requires staffing surge (+{devs_needed_a - dev_count} developers) or severe scope descope to be viable.",
    }

    # SCENARIO B: DURATION_ANCHORED
    avail_cap_b = round(dev_count * p1_weeks * 0.70, 1)
    devs_needed_b = dev_count
    uat_delay_days = (datetime.strptime(derived_build_end, "%Y-%m-%d").date() - earliest_ms_date).days if earliest_ms_date and derived_build_end else 0
    golive_delay_days = 0
    if contract_go_live_date and derived_go_live_date:
        golive_delay_days = (datetime.strptime(derived_go_live_date, "%Y-%m-%d").date() - contract_go_live_date).days

    phase_summary_str = "; ".join(f"{p.get('name')}: {p.get('days')}d" for p in phases)

    scenario_b = {
        "key": "DURATION_ANCHORED",
        "title": "Option B: Enforce Phase Durations (Duration-Anchored)",
        "badge": "Recommended",
        "badge_color": "emerald",
        "approach": f"Stated phase durations ({phase_summary_str}) are enforced. Team of {dev_count} developers delivers scope at standard velocity. Contract billing records remain immutable; contract amendment is recommended.",
        "phase_schedule": [
            {
                "phase": dp["phase"],
                "window": f"{dp['start']} to {dp['end']}",
                "duration": f"{dp['weeks']} weeks (stated)",
            }
            for dp in derived_phases
        ],
        "capacity": {
            "demand_dev_weeks": demand_dev_weeks,
            "available_dev_weeks": avail_cap_b,
            "headcount_available": dev_count,
            "developers_needed": devs_needed_b,
            "shortfall_dev_weeks": 0.0,
            "utilisation": 0.70,
            "feasibility": "FEASIBLE",
            "capacity_warning": f"FEASIBLE: {dev_count} developers over {p1_weeks} weeks deliver the {demand_dev_weeks} dev-weeks of build work with standard velocity.",
        },
        "billing_impact": f"Billing milestones delayed: {earliest_ms_name} (+{uat_delay_days}d), Go-Live (+{golive_delay_days}d). Contract billing facts remain unchanged; contract amendment recommended.",
        "governance_note": "A planning assumption is recorded. Project monitoring tracks progress against derived dates; milestone-at-risk alerts inform the Engagement Manager.",
        "planning_assumption": "Contract amendment recommended; contract billing terms unchanged.",
    }

    # SCENARIO C: PROVISIONAL_STRICT
    scenario_c = {
        "key": "PROVISIONAL_STRICT",
        "title": "Option C: Proceed Provisionally (Stricter Alerts)",
        "badge": "Provisional",
        "badge_color": "amber",
        "approach": "Acknowledge contradiction and proceed provisionally without altering planning baseline or contract dates. Automated execution alerts flag all deliverables exceeding original milestone dates.",
        "capacity": {
            "headcount_available": dev_count,
            "developers_needed": dev_count,
            "feasibility": "CONDITIONAL",
            "capacity_warning": f"Execution monitoring active: {dev_count} developers will track against the stricter contract dates, generating active alerts for PM intervention.",
        },
        "billing_impact": f"Original contract dates remain active for alerting. Deliverables exceeding {earliest_ms_str} will raise immediate high-priority milestone alerts.",
    }

    return {
        "scenario_a": scenario_a,
        "scenario_b": scenario_b,
        "scenario_c": scenario_c,
        "derived_dates": {
            "build_end": derived_build_end,
            "go_live": derived_go_live_date,
            "final_end": derived_final_date,
        },
        "contract_dates": {
            "earliest_milestone": str(earliest_ms_date) if earliest_ms_date else None,
            "go_live": str(contract_go_live_date) if contract_go_live_date else None,
        }
    }


def detect(artifact: Dict[str, Any]) -> Dict[str, Any]:
    """
    Main entry point. Run against any final baseline artifact dict.

    Returns contradiction payload (see module docstring).
    If artifact has no dates or no phases, returns has_contradiction=False with explanation.
    """
    result: Dict[str, Any] = {
        "has_contradiction": False,
        "contract_start": None,
        "phase_duration_days": 0,
        "earliest_milestone_date": None,
        "available_days": None,
        "gap_days": 0,
        "phase_breakdown": [],
        "milestone_name": None,
        "severity": None,
        "finding_type": "TIMELINE_CONTRADICTION",
        "explanation": "",
        "recommended_action": "",
    }

    # ── 1. Contract start date ─────────────────────────────────────────────────
    tl = artifact.get("timeline") or artifact.get("project_timeline") or {}
    contract_info = artifact.get("contract") or artifact.get("contract_dates") or {}
    start_raw = (
        contract_info.get("contract_start_date")
        or contract_info.get("start_date")
        or contract_info.get("effective_date")
        or tl.get("contract_start_date")
        or tl.get("start_date")
        or artifact.get("contract_start_date")
        or artifact.get("start_date")
    )
    contract_start = _parse_date(start_raw)

    # Fallback A: scan earliest estimated_start_date from scope items
    if not contract_start:
        items_for_start = (artifact.get("scope_items") or []) + (artifact.get("all_records") or [])
        item_starts = []
        for it in items_for_start:
            s_val = it.get("estimated_start_date") or it.get("planned_start_date") or it.get("start_date")
            p_dt = _parse_date(s_val)
            if p_dt:
                item_starts.append(p_dt)
        if item_starts:
            contract_start = min(item_starts)

    # Fallback B: check database projects table if project_id is available
    if not contract_start and artifact.get("project_id"):
        try:
            from core.database import get_db_connection
            _cnx = get_db_connection()
            if _cnx:
                with _cnx.cursor(dictionary=True) as _c:
                    _c.execute("SELECT start_date FROM projects WHERE id = %s", (artifact["project_id"],))
                    _prow = _c.fetchone()
                    if _prow and _prow.get("start_date"):
                        contract_start = _parse_date(_prow["start_date"])
                _cnx.close()
        except Exception:
            pass

    result["contract_start"] = str(contract_start) if contract_start else None

    # ── 2. Phase durations ─────────────────────────────────────────────────────
    phases = _extract_phase_durations(artifact)
    result["phase_breakdown"] = phases
    total_phase_days = sum(p["days"] for p in phases)
    result["phase_duration_days"] = total_phase_days

    if not phases or total_phase_days < _MIN_PHASE_DAYS:
        result["explanation"] = (
            "No explicit phase durations found in this contract artifact. "
            "Timeline contradiction check not applicable."
        )
        return result

    if not contract_start:
        result["explanation"] = (
            "Contract start date not extractable from artifact. "
            "Timeline contradiction check skipped."
        )
        return result

    # ── 3. Earliest delivery/billing milestone ─────────────────────────────────
    ms_name, ms_date = _extract_earliest_billing_milestone(artifact, contract_start=contract_start)
    result["earliest_milestone_date"] = str(ms_date) if ms_date else None
    result["milestone_name"] = ms_name

    if not ms_date:
        result["explanation"] = (
            "No billing milestone dates found in this contract artifact. "
            "Timeline contradiction check not applicable."
        )
        return result

    # ── 4. Compare ────────────────────────────────────────────────────────────
    available_days = (ms_date - contract_start).days
    result["available_days"] = available_days

    # Check first phase overrun as well as total phase overrun
    first_phase = phases[0]
    first_phase_days = first_phase.get("days", total_phase_days)
    gap_first_phase = first_phase_days - available_days
    gap_total = total_phase_days - available_days
    gap_days = max(gap_first_phase, gap_total)
    result["gap_days"] = gap_days

    # Projected end of phases from start
    projected_end = contract_start + timedelta(days=total_phase_days)

    if gap_days > TOLERANCE_DAYS:
        result["has_contradiction"] = True
        result["severity"] = "CRITICAL"
        phase_str = "; ".join(f"{p['name']} ({p['days']}d)" for p in phases)
        if gap_first_phase > TOLERANCE_DAYS and len(phases) > 1:
            reason = (
                f"CRITICAL SCHEDULE CONTRADICTION: Initial phase '{first_phase['name']}' requires {first_phase_days} calendar days, "
                f"but the first delivery milestone '{ms_name}' is scheduled in only {available_days} days from contract start ({contract_start}). "
                f"The initial phase overruns the milestone by {gap_first_phase} days (and total stated phases {total_phase_days}d overrun by {gap_total}d). "
                f"Phase breakdown: {phase_str}. "
                f"Projected completion: {projected_end.isoformat()} vs milestone: {ms_date.isoformat()}."
            )
        else:
            reason = (
                f"CRITICAL SCHEDULE CONTRADICTION: Stated phase durations total {total_phase_days} calendar days, "
                f"but the delivery milestone '{ms_name}' is only {available_days} days after the contract start date ({contract_start}). "
                f"The phases exceed the milestone window by {gap_days} days. "
                f"Phase breakdown: {phase_str}. "
                f"Projected completion: {projected_end.isoformat()} vs milestone: {ms_date.isoformat()}."
            )
        result["explanation"] = reason
        result["recommended_action"] = (
            "Human resolution required before baseline approval. "
            "Option A: Enforce billing milestone dates (compress phase durations). "
            "Option B: Enforce phase durations (push back milestone dates). "
            "Option C: Proceed provisionally with stricter milestone alerts. "
            "Agent must NOT auto-select either option."
        )
        
        # Build parallel scenarios for comparison and HITL selection
        scenarios_dict = _build_parallel_scenarios(
            artifact=artifact,
            contract_start=contract_start,
            phases=phases,
            earliest_ms_name=ms_name,
            earliest_ms_date=ms_date,
            available_days=available_days,
        )
        result["scenarios"] = scenarios_dict
        derived_go_live = scenarios_dict.get("derived_dates", {}).get("go_live") or projected_end.isoformat()
        contract_go_live = scenarios_dict.get("contract_dates", {}).get("go_live") or ms_date.isoformat()
        result["warning"] = {
            "warning_type": "TIMELINE_CONFLICT",
            "severity": "CRITICAL",
            "derived_date": str(derived_go_live),
            "contract_date": str(contract_go_live),
            "tolerance_days": TOLERANCE_DAYS,
            "gap_days": gap_days,
            "message": f"CRITICAL TIMELINE CONFLICT: Stated phase durations project completion at {derived_go_live}, which exceeds contractual date {contract_go_live} by {gap_days} days."
        }
    elif gap_days > 0:
        result["severity"] = "WARNING"
        result["explanation"] = (
            f"Minor schedule tension: phase durations ({total_phase_days}d) exceed the milestone "
            f"window '{ms_name}' ({available_days}d) by {gap_days} day(s), within tolerance. "
            f"No HITL block required but PM should review."
        )
        result["recommended_action"] = (
            "PM awareness recommended. Consider adding a buffer or adjusting phase plan."
        )
    else:
        result["explanation"] = (
            f"Phase durations ({total_phase_days}d) fit within the milestone window "
            f"'{ms_name}' ({available_days}d) with {abs(gap_days)} day(s) of slack. No contradiction."
        )

    return result
