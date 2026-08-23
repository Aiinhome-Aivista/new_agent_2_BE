"""
RiskScoringEngine
=================
Phase 4 of Execution Priority Pipeline.

Calculates Execution Priority and Risk Score based on graph_role-driven bands.

Band System (graph_role → execution_priority_score range):
  Band 1: ROOT_CAUSE (cascade ≥ 2)       → 90–100
  Band 2: ROOT_CAUSE (cascade == 1)       → 80–89
  Band 3: INTERMEDIATE_BLOCKER            → 60–79
  Band 4: TERMINAL_ACTIVITY               → 40–59
  Band 5: ISOLATED                        → 20–39
  Band 6: (reserved)                      → 10–19
  Band 7: SCOPE_CREEP (is_out_of_scope)   → 0–9

Risk Severity is computed INDEPENDENTLY from execution priority.
"""

import re
from datetime import datetime, timedelta


def _parse_due_date(due_date_str: str, reference_date=None) -> int:
    """
    Parse a due_date string into days_until_due from today (or reference_date).
    
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

    today = reference_date or datetime.now().date()
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
        # Rough: days until end of current month
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
                d = datetime.strptime(match.group(1), fmt).date()
                return (d - today).days
            except ValueError:
                continue

    # 5. Not a recognizable date expression
    return None


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
        # ── NEW PARAMETERS ──
        graph_role: str = "ISOLATED",
        due_date: str = None,
        cascade_count: int = None,
    ) -> dict:
        """
        Dual Metric Architecture: Calculates Execution Priority AND Risk Severity separately.
        
        Execution Priority: Driven by graph_role (band) + bonus factors within band.
        Risk Severity: Driven by schedule urgency + business criticality + owner.
        
        This function is STATELESS — each item is scored independently.
        """
        # Normalize cascade_count (use explicit param if provided, else fall back to blocked_work_count)
        effective_cascade = cascade_count if cascade_count is not None else blocked_work_count
        
        execution_reasons = []
        score_breakdown = {}
        
        # ────────────────────────────────────────────────────────────────────
        # 1. EXECUTION PRIORITY — Graph Role Band System
        # ────────────────────────────────────────────────────────────────────
        
        # Band 7: Scope Creep — ALWAYS first check, short-circuit
        if is_scope_creep or category in ("SCOPE_CREEP", "CHANGE_REQUEST"):
            band_name = "Scope Creep / Change Request"
            min_prio, max_prio = 0, 9
            execution_reasons.append("⚠ Out of scope — requires Change Request before execution")
            score_breakdown["Execution Band"] = band_name
            score_breakdown["Band Rule"] = "is_out_of_scope=True → Band 7 (0-9)"
        
        # Band 1: ROOT_CAUSE with cascade ≥ 2 (blocks 2+ downstream items)
        elif graph_role == "ROOT_CAUSE" and effective_cascade >= 2:
            band_name = "Critical Path Root Cause"
            min_prio, max_prio = 90, 100
            execution_reasons.append(f"✓ Root Cause blocking {effective_cascade} downstream activities")
            score_breakdown["Execution Band"] = band_name
            score_breakdown["Band Rule"] = f"graph_role=ROOT_CAUSE, cascade={effective_cascade} → Band 1 (90-100)"
        
        # Band 2: ROOT_CAUSE with cascade == 1 (blocks exactly 1 item)
        elif graph_role == "ROOT_CAUSE" and effective_cascade >= 1:
            band_name = "Root Cause (Single Downstream)"
            min_prio, max_prio = 80, 89
            execution_reasons.append(f"✓ Root Cause blocking {effective_cascade} downstream activity")
            score_breakdown["Execution Band"] = band_name
            score_breakdown["Band Rule"] = f"graph_role=ROOT_CAUSE, cascade={effective_cascade} → Band 2 (80-89)"
        
        # ROOT_CAUSE with cascade == 0 (isolated root)
        elif graph_role == "ROOT_CAUSE" and effective_cascade == 0:
            band_name = "Isolated Root Cause"
            min_prio, max_prio = 70, 79
            execution_reasons.append("✓ Root Cause (no downstream dependents detected)")
            score_breakdown["Execution Band"] = band_name
            score_breakdown["Band Rule"] = f"graph_role=ROOT_CAUSE, cascade=0 → Band (70-79)"
        
        # Band 3: INTERMEDIATE_BLOCKER (has upstream AND downstream edges)
        elif graph_role == "INTERMEDIATE_BLOCKER":
            band_name = "Intermediate Blocker"
            min_prio, max_prio = 60, 79
            execution_reasons.append("✓ Intermediate node — blocked by upstream, blocks downstream")
            score_breakdown["Execution Band"] = band_name
            score_breakdown["Band Rule"] = "graph_role=INTERMEDIATE_BLOCKER → Band 3 (60-79)"
        
        # Band 4: TERMINAL_ACTIVITY (has upstream edges, no downstream)
        elif graph_role == "TERMINAL_ACTIVITY":
            band_name = "Terminal Activity"
            min_prio, max_prio = 40, 59
            execution_reasons.append("✓ Terminal activity — waiting on upstream, no downstream impact")
            score_breakdown["Execution Band"] = band_name
            score_breakdown["Band Rule"] = "graph_role=TERMINAL_ACTIVITY → Band 4 (40-59)"
        
        # Band 5: ISOLATED (no graph edges)
        elif graph_role == "ISOLATED":
            band_name = "Isolated Activity"
            min_prio, max_prio = 20, 39
            execution_reasons.append("Independent activity — no dependency chain")
            score_breakdown["Execution Band"] = band_name
            score_breakdown["Band Rule"] = "graph_role=ISOLATED → Band 5 (20-39)"
        
        # Fallback (should not happen with proper graph_role assignment)
        else:
            band_name = "Unclassified"
            min_prio, max_prio = 20, 39
            score_breakdown["Execution Band"] = band_name
            score_breakdown["Band Rule"] = f"graph_role={graph_role} (unknown) → default Band 5 (20-39)"
        
        # ── Bonus points within band range ──
        # These adjust the score WITHIN the band, never below floor or above ceiling.
        effort_mult = {"XS": 1.5, "S": 1.2, "M": 1.0, "L": 0.8, "XL": 0.5}.get(resolution_effort, 1.0)
        
        bonus_points = 0.0
        band_range = max_prio - min_prio
        
        # Bonus from unlock count (up to 40% of band range)
        if execution_unlock_count > 0:
            unlock_bonus = min(execution_unlock_count * 2.0, band_range * 0.4)
            bonus_points += unlock_bonus
            execution_reasons.append(f"✓ Unlocks {execution_unlock_count} immediate tasks")
        
        # Bonus from cascade depth (up to 30% of band range)
        if cascade_depth > 0:
            cascade_bonus = min(cascade_depth * 1.5, band_range * 0.3)
            bonus_points += cascade_bonus
            execution_reasons.append(f"✓ Cascade Depth: {cascade_depth}")
        
        # Bonus from criticality score (up to 20% of band range)
        if criticality_score > 0:
            crit_bonus = min(criticality_score * 0.1, band_range * 0.2)
            bonus_points += crit_bonus
        
        # Bonus from critical path (up to 5% of band range)
        if critical_path:
            bonus_points += min(3.0, band_range * 0.05)
            execution_reasons.append("✓ On the Critical Path to Go-Live")
        
        # Apply effort multiplier to bonus (quick fixes get a slight boost)
        bonus_points *= effort_mult
        
        # Ensure bonus doesn't exceed band range
        scaled_addition = min(bonus_points, band_range)
        
        execution_priority = min_prio + scaled_addition
        execution_priority = max(min(round(execution_priority), 100), 0)
        
        # Additional execution reasons
        if earliest_root_cause:
            execution_reasons.append("✓ Earliest Root Cause")
        if resolution_effort in ["XS", "S"]:
            execution_reasons.append("✓ Quick Resolution Effort")
        
        score_breakdown["Bonus Points"] = round(bonus_points, 1)
        score_breakdown["Effort Multiplier"] = effort_mult
        
        # ────────────────────────────────────────────────────────────────────
        # 2. RISK SEVERITY — Schedule Urgency + Business Criticality + Owner
        #    (Completely independent from execution priority)
        # ────────────────────────────────────────────────────────────────────
        
        # ── due_date fallback: if days_until_due is still 9999, try parsing due_date ──
        effective_days_until_due = days_until_due
        if effective_days_until_due >= 9999 and due_date:
            parsed_days = _parse_due_date(due_date)
            if parsed_days is not None:
                effective_days_until_due = parsed_days
                score_breakdown["Due Date Source"] = f"LLM extracted: {due_date} → {parsed_days} days"
        
        # FIX 2: Expose parsed_days_until_due in score_breakdown so caller can sync days_until_due
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
        # execution_priority is low (Band 7) but risk_severity stays high
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
            "execution_band_name": band_name
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
        **kwargs
    ) -> str:
        """
        Formats the full evidence-backed reasoning string stored in tracker_items.reasoning
        """
        if "narratives" in kwargs and kwargs["narratives"]:
            import json
            payload = kwargs["narratives"]
            payload["_type"] = "pmo_narrative"
            
            # Mix in legacy properties for the UI to use if it wants
            if original_contract_sentence:
                payload["original_contract_sentence"] = original_contract_sentence
            if mom_evidence:
                payload["mom_evidence"] = mom_evidence
            if longest_path:
                payload["execution_chain"] = longest_path
                
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
            # Deduplicate if mom_evidence is identical to original_contract_sentence
            if not original_contract_sentence or clean.lower() != original_contract_sentence.lower():
                lines.append(f'------------------------\nEvidence (MoM)\n"{clean}"')
            
        return "\n".join(lines).strip()
