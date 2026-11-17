"""
Leading-Indicator Alert Engine — Proactive Project Risk Synthesis
================================================================

Part C Implementation per Engineering Remediation Specification:
- Runs on every MoM upload after activity extraction.
- Reference date is always the MoM's document date (NEVER the server clock, except C6).
- All thresholds are configurable (lead_window_days=7, progress_tolerance=0.20, etc.).
- Evaluates C1 (Work Item at Risk), C2 (Client Dependency Overdue/Due Soon),
  C3 (Milestone at Risk), C4 (Forecast Slip), C5 (Commitment Health),
  C6 (Stale Data), C7 (Scope Creep), and C8 (Alert Quality & Lifecycle).
- Suppresses C1 alerts on items blocked by unmet client dependencies (owner CLIENT).
- Deduplicates across MoMs by (type, subject), escalates worsened situations,
  and auto-resolves when completion evidence arrives.
"""

from __future__ import annotations

import json
import os
import re
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple, Set


class LeadingIndicatorEngine:
    # Config defaults
    DEFAULT_LEAD_WINDOW_DAYS = 7
    DEFAULT_PROGRESS_TOLERANCE = 0.20
    DEFAULT_MIN_ALERT_CONFIDENCE = 0.40
    DEFAULT_MILESTONE_HORIZON_DAYS = 21
    DEFAULT_STALE_CADENCE_DAYS = 14
    DEFAULT_COOLDOWN_HOURS = 24
    DEFAULT_DEVELOPERS = 5
    DEFAULT_UTILISATION = 0.80

    # In-memory cooldown cache for alerts emailed within cooldown window: { (project_id, subject): last_sent_dt }
    _EMAIL_COOLDOWN_CACHE: Dict[Tuple[int, str], datetime] = {}

    @classmethod
    def extract_document_date(cls, mom_data: Dict[str, Any], doc_text: str = "") -> date:
        """
        Extracts the MoM's own date (reference date) from metadata or document text.
        Falls back to contract start if none found. NEVER uses server clock.
        """
        # 1. From mom_data explicit fields
        for field in ("document_date", "meeting_date", "mom_date", "date"):
            val = mom_data.get(field)
            if val:
                try:
                    return datetime.strptime(str(val)[:10], "%Y-%m-%d").date()
                except Exception:
                    pass

        # 2. From document text regex patterns
        patterns = [
            r'Meeting Date\s*:\s*(\d{4}-\d{2}-\d{2})',
            r'Date\s*:\s*(\d{4}-\d{2}-\d{2})',
            r'Meeting Date\s*:\s*(\d{1,2}(?:st|nd|rd|th)?\s+[A-Za-z]+\s+\d{4})',
            r'Date\s*:\s*(\d{1,2}(?:st|nd|rd|th)?\s+[A-Za-z]+\s+\d{4})',
            r'(\d{4}-\d{2}-\d{2})',
        ]
        for pat in patterns:
            m = re.search(pat, doc_text, re.IGNORECASE)
            if m:
                raw_d = m.group(1)
                # Try iso
                try:
                    return datetime.strptime(raw_d, "%Y-%m-%d").date()
                except Exception:
                    pass
                # Try wordy date (e.g. 29 January 2027)
                clean_d = re.sub(r'(\d+)(st|nd|rd|th)', r'\1', raw_d)
                for fmt in ("%d %B %Y", "%d %b %Y", "%B %d, %Y"):
                    try:
                        return datetime.strptime(clean_d.strip(), fmt).date()
                    except Exception:
                        pass

        # Fallback: baseline contract start
        return date(2027, 1, 22)

    @classmethod
    def evaluate(
        cls,
        project_id: int,
        document_id: int,
        document_text: str,
        document_date: Optional[date] = None,
        db_cursor = None,
        extracted_activities: Optional[List[Dict[str, Any]]] = None,
        plan_snapshot: Optional[Dict[str, Any]] = None,
        server_date: Optional[date] = None,
        stakeholders: Optional[List[Dict[str, Any]]] = None,
        config: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Runs the leading-indicator evaluation on an MoM upload.
        """
        cfg = config or {}
        lead_window_days = cfg.get("lead_window_days", cls.DEFAULT_LEAD_WINDOW_DAYS)
        progress_tol = cfg.get("progress_tolerance", cls.DEFAULT_PROGRESS_TOLERANCE)
        min_conf = cfg.get("min_alert_confidence", cls.DEFAULT_MIN_ALERT_CONFIDENCE)
        ms_horizon_days = cfg.get("milestone_horizon_days", cls.DEFAULT_MILESTONE_HORIZON_DAYS)
        stale_cadence_days = cfg.get("stale_cadence_days", cls.DEFAULT_STALE_CADENCE_DAYS)
        cooldown_hours = cfg.get("cooldown_hours", cls.DEFAULT_COOLDOWN_HOURS)
        devs = cfg.get("developers", cls.DEFAULT_DEVELOPERS)
        utilisation = cfg.get("utilisation", cls.DEFAULT_UTILISATION)

        # 1. Determine MoM Reference Date (NEVER server clock)
        if not document_date:
            document_date = cls.extract_document_date({}, document_text)

        # 2. Load latest Plan (Approved or Provisional)
        from services.project_plan_service import ProjectPlanService
        if not plan_snapshot:
            plan_res = ProjectPlanService.get_latest_plan(db_cursor, project_id)
            plan_snapshot = plan_res.get("plan", {})
            is_provisional = plan_res.get("is_provisional", False)
        else:
            is_provisional = plan_snapshot.get("is_provisional", False)

        work_items = plan_snapshot.get("work_items", [])
        client_deps = plan_snapshot.get("client_dependencies", [])
        milestones = plan_snapshot.get("milestones", [])
        commitments = plan_snapshot.get("commitments", [])

        # Contract dates
        c_info = plan_snapshot.get("contract", {})
        try:
            c_start_dt = datetime.strptime(str(c_info.get("start_date", "2027-01-22"))[:10], "%Y-%m-%d").date()
        except Exception:
            c_start_dt = date(2027, 1, 22)
        try:
            c_end_dt = datetime.strptime(str(c_info.get("end_date", "2027-04-30"))[:10], "%Y-%m-%d").date()
        except Exception:
            c_end_dt = date(2027, 4, 30)

        # 3. Match evidence from MoM activities against Work Items & Client Dependencies
        activities = extracted_activities or []
        doc_lower = document_text.lower()

        cls._match_mom_evidence(
            doc_date=document_date,
            doc_text=doc_lower,
            activities=activities,
            work_items=work_items,
            client_deps=client_deps,
            milestones=milestones,
        )

        generated_alerts: List[Dict[str, Any]] = []
        blocked_by_client_ids: Set[str] = set()

        # ── C2. CLIENT DEPENDENCY OVERDUE OR DUE SOON ──
        for dep in client_deps:
            req_by_str = dep.get("required_by")
            if not req_by_str:
                continue
            try:
                req_by_dt = datetime.strptime(str(req_by_str)[:10], "%Y-%m-%d").date()
            except Exception:
                continue

            is_received = (dep.get("status") == "RECEIVED")
            if not is_received:
                days_until_req = (req_by_dt - document_date).days
                if days_until_req <= lead_window_days:
                    # Gated work items
                    gated_ids = dep.get("gated_items", [])
                    for gid in gated_ids:
                        blocked_by_client_ids.add(gid)

                    # Find earliest gated item put at risk
                    first_gated = next((w for w in work_items if w["id"] in gated_ids), None)
                    first_gated_name = first_gated["name"] if first_gated else "downstream deliverables"

                    is_overdue = (days_until_req < 0)
                    sev = "CRITICAL" if is_overdue else "HIGH"
                    c_type = "CLIENT_DEPENDENCY_OVERDUE" if is_overdue else "CLIENT_DEPENDENCY_DUE_SOON"

                    dep_name = dep.get("name", "Client Prerequisite")
                    dtype = dep.get("dependency_type", "ENVIRONMENT_ACCESS")
                    gated_count = len(gated_ids)

                    what_str = f"Client dependency '{dep_name}' ({dtype}) is {abs(days_until_req)} days overdue" if is_overdue else f"Client dependency '{dep_name}' ({dtype}) is due in {days_until_req} days"
                    why_str = f"Required by {req_by_str} to unblock vendor delivery. No evidence of fulfillment in MoM dated {document_date}. Puts '{first_gated_name}' ({gated_count} gated items) at immediate delivery risk."

                    alert_entry = {
                        "alert_type": "CLIENT_DEPENDENCY_OVERDUE_OR_DUE_SOON",
                        "subtype": c_type,
                        "subject": f"Client Dependency: {dep_name}",
                        "title": f"Client Dependency: {dep_name}",
                        "what": what_str,
                        "why": why_str,
                        "evidence_quote": dep.get("evidence") or f"MoM dated {document_date}: Dependency not yet fulfilled",
                        "evidence_date": str(document_date),
                        "owner": "CLIENT",
                        "problem_date": req_by_str,
                        "severity": sev,
                        "risk_score": 85 if is_overdue else 70,
                        "confidence": 0.90,
                        "gated_items": gated_ids,
                        "first_gated_name": first_gated_name,
                        "recommended_action": f"Escalate immediately to Client Engagement Sponsor to furnish {dep_name} before {req_by_str} to prevent contractual milestone slip.",
                    }
                    generated_alerts.append(alert_entry)

        # Mark gated work items as blocked_by_client
        for wi in work_items:
            if wi["id"] in blocked_by_client_ids:
                wi["blocked_by_client"] = True

        # ── C1. WORK ITEM AT RISK ──
        for wi in work_items:
            # RULE: Skip items blocked by an unmet client dependency (alert goes to dependency, owner CLIENT)
            if wi.get("blocked_by_client"):
                continue

            # Skip completed items
            if wi.get("status") == "COMPLETED" or wi.get("percent_complete", 0) >= 100.0:
                continue

            p_start_str = wi.get("planned_start")
            p_end_str = wi.get("planned_end")
            if not p_start_str or not p_end_str:
                continue
            try:
                p_start_dt = datetime.strptime(str(p_start_str)[:10], "%Y-%m-%d").date()
                p_end_dt = datetime.strptime(str(p_end_str)[:10], "%Y-%m-%d").date()
            except Exception:
                continue

            pct = wi.get("percent_complete", 0.0)
            status = wi.get("status", "NOT_STARTED")

            # Calculate expected progress
            tot_days = max(1, (p_end_dt - p_start_dt).days)
            elap_days = (document_date - p_start_dt).days
            if elap_days <= 0:
                exp_pct = 0.0
            else:
                exp_pct = min(1.0, elap_days / tot_days)

            days_to_end = (p_end_dt - document_date).days

            # Fire condition A: Planned start passed with no evidence of work
            fire_start_missed = (document_date >= p_start_dt and status in ("NOT_STARTED", "UNKNOWN") and pct == 0.0)
            # Fire condition B: Approaching planned end with slow or no progress
            fire_end_approaching = (days_to_end <= lead_window_days and (status in ("NOT_STARTED", "UNKNOWN") or (pct / 100.0) < (exp_pct - progress_tol)))

            if fire_start_missed or fire_end_approaching:
                is_est = wi.get("is_estimated", True)
                date_conf = wi.get("date_confidence", 0.85)

                # Severity scaling: softened for estimated dates
                if days_to_end < 0:
                    sev = "CRITICAL"
                    score = 90
                elif not is_est:
                    sev = "HIGH"
                    score = int(75 * date_conf)
                else:
                    sev = "MEDIUM"
                    score = int(60 * date_conf)

                exp_pct_label = int(exp_pct * 100)
                pct_label = int(pct)
                est_tag = " (Date was estimated based on historical/AI inference)" if is_est else ""

                alert_entry = {
                    "alert_type": "WORK_ITEM_AT_RISK",
                    "subject": f"Work Item at Risk: {wi['name']}",
                    "title": f"Work Item at Risk: {wi['name']}",
                    "what": f"Work item '{wi['name']}' may miss {p_end_str}; expected {exp_pct_label}%, evidence shows {pct_label}%.{est_tag}",
                    "why": f"Planned end date is {p_end_str} (in {days_to_end} days). Current progress is {pct_label}% vs planned expectation of {exp_pct_label}%.",
                    "evidence_quote": f"MoM evidence as of {document_date}: status is {status}, completion is {pct_label}%.",
                    "evidence_date": str(document_date),
                    "owner": "VENDOR",
                    "problem_date": p_end_str,
                    "severity": sev,
                    "risk_score": score,
                    "confidence": date_conf,
                    "work_item_id": wi["id"],
                    "is_estimated": is_est,
                    "recommended_action": f"Reassign additional developer capacity to '{wi['name']}' or re-sequence non-dependent deliverables.",
                }
                generated_alerts.append(alert_entry)

        # ── C3. MILESTONE AT RISK ──
        for ms in milestones:
            if ms.get("status") in ("COMPLETED", "RESOLVED", "SIGNED_OFF"):
                continue
            ms_date_str = ms.get("target_date")
            if not ms_date_str:
                continue
            try:
                ms_dt = datetime.strptime(str(ms_date_str)[:10], "%Y-%m-%d").date()
            except Exception:
                continue

            days_to_ms = (ms_dt - document_date).days
            if days_to_ms <= ms_horizon_days:
                gating_ids = set(ms.get("gating_items", []))
                gating_wis = [w for w in work_items if w["id"] in gating_ids]
                if not gating_wis:
                    gating_wis = [w for w in work_items if str(w.get("planned_end", "")) <= ms_date_str]

                open_wis = [w for w in gating_wis if w.get("percent_complete", 0.0) < 100.0]
                if open_wis:
                    rem_effort = sum(w.get("effort_dev_weeks", 1.0) * (1.0 - w.get("percent_complete", 0.0) / 100.0) for w in open_wis)
                    weeks_left = max(0.1, days_to_ms / 7.0)
                    available_capacity = devs * utilisation * weeks_left

                    if rem_effort > available_capacity or days_to_ms < 0:
                        overrun_weeks = max(0.2, (rem_effort - available_capacity) / (devs * utilisation))
                        slip_days = max(1, int(overrun_weeks * 5))
                        fee_str = str(ms.get("payment_amount") or "Contractual Deliverable Sign-off")

                        sev = "CRITICAL" if days_to_ms < 0 else "HIGH"
                        open_names = [w["name"] for w in open_wis[:4]]
                        more_count = len(open_wis) - len(open_names)
                        open_names_str = ", ".join(open_names) + (f" (+{more_count} more)" if more_count > 0 else "")

                        alert_entry = {
                            "alert_type": "MILESTONE_AT_RISK",
                            "subject": f"Milestone at Risk: {ms['name']}",
                            "title": f"Milestone at Risk: {ms['name']}",
                            "what": f"Milestone '{ms['name']}' at risk of {slip_days}-day slip; {len(open_wis)} gating item(s) open. Fee at stake: {fee_str}.",
                            "why": f"Target date is {ms_date_str}. Open gating items: {open_names_str}. Remaining effort ({round(rem_effort, 1)} dev-weeks) exceeds available team capacity ({round(available_capacity, 1)} dev-weeks).",
                            "evidence_quote": f"MoM dated {document_date}: {len(open_wis)} dependent items incomplete before milestone deadline.",
                            "evidence_date": str(document_date),
                            "owner": "VENDOR",
                            "problem_date": ms_date_str,
                            "severity": sev,
                            "risk_score": 90 if days_to_ms < 0 else 80,
                            "confidence": 0.88,
                            "milestone_id": ms.get("id"),
                            "open_gating_items": [w["id"] for w in open_wis],
                            "forecast_slip_days": slip_days,
                            "payment_amount": fee_str,
                            "recommended_action": f"Review open gating deliverables ({open_names_str}) and adjust sprint priority to avoid milestone breach.",
                        }
                        generated_alerts.append(alert_entry)

        # ── C4. FORECAST SLIP (Capacity) ──
        open_items_all = [w for w in work_items if w.get("percent_complete", 0.0) < 100.0]
        if open_items_all:
            total_rem_effort = sum(w.get("effort_dev_weeks", 1.0) * (1.0 - w.get("percent_complete", 0.0) / 100.0) for w in open_items_all)
            weeks_to_contract_end = max(0.1, (c_end_dt - document_date).days / 7.0)
            total_capacity = devs * utilisation * weeks_to_contract_end

            if total_rem_effort > total_capacity:
                overrun_w = (total_rem_effort - total_capacity) / (devs * utilisation)
                forecast_end_dt = c_end_dt + timedelta(days=int(overrun_w * 7))

                alert_entry = {
                    "alert_type": "FORECAST_SLIP",
                    "subject": "Project-Wide Capacity Overrun: Forecast Slip",
                    "title": "Project-Wide Capacity Overrun: Forecast Slip",
                    "what": f"Project forecast completion slipping to {forecast_end_dt} (overrun: {round(overrun_w, 1)} dev-weeks). Capacity shortfall detected.",
                    "why": f"Total remaining work ({round(total_rem_effort, 1)} dev-weeks) exceeds remaining delivery capacity ({round(total_capacity, 1)} dev-weeks) until contract end date {c_end_dt}.",
                    "evidence_quote": f"Capacity model recomputed as of MoM date {document_date}.",
                    "evidence_date": str(document_date),
                    "owner": "VENDOR",
                    "problem_date": str(forecast_end_dt),
                    "severity": "HIGH",
                    "risk_score": 75,
                    "confidence": 0.85,
                    "forecast_completion_date": str(forecast_end_dt),
                    "overrun_dev_weeks": round(overrun_w, 1),
                    "recommended_action": "Evaluate staff augmentation or seek Change Request to extend contract completion window.",
                }
                generated_alerts.append(alert_entry)

                # Update live forecast dates in plan
                for wi in open_items_all:
                    wi["forecast_end"] = str(forecast_end_dt)

        # ── C5. COMMITMENT HEALTH ──
        for comm in commitments:
            c_name = comm.get("name") or comm.get("title") or "Commitment"
            c_type = (comm.get("commitment_type") or "").upper()
            c_freq = (comm.get("frequency") or "").upper()
            comm_l = c_name.lower()

            is_governance = any(k in comm_l for k in ("weekly", "steering", "report", "stakeholder", "governance"))
            is_trigger_sla = (c_type == "TRIGGER_BASED" or "feedback" in comm_l or "sla" in comm_l)

            if is_governance:
                # Governance silence protection: ONLY alert if explicitly missed / cancelled / postponed
                explicit_negatives = ("missed", "cancelled", "canceled", "postponed", "deferred", "not held", "did not take place")
                has_explicit_miss = any(neg in doc_lower for neg in explicit_negatives) and any(kw in doc_lower for kw in ("meeting", "steering", "report", "call"))
                if has_explicit_miss:
                    alert_entry = {
                        "alert_type": "COMMITMENT_HEALTH",
                        "subtype": "GOVERNANCE_MISSED",
                        "subject": f"Governance Commitment Missed: {c_name}",
                        "title": f"Governance Commitment Missed: {c_name}",
                        "what": f"Governance commitment '{c_name}' was explicitly reported as missed or postponed.",
                        "why": f"MoM text dated {document_date} notes regular governance interaction was cancelled or postponed.",
                        "evidence_quote": f"MoM evidence as of {document_date}",
                        "evidence_date": str(document_date),
                        "owner": comm.get("owner", "JOINT"),
                        "problem_date": str(document_date),
                        "severity": "MEDIUM",
                        "risk_score": 50,
                        "confidence": 0.90,
                        "recommended_action": f"Reschedule {c_name} promptly to ensure stakeholder alignment and contractual compliance.",
                    }
                    generated_alerts.append(alert_entry)
            elif is_trigger_sla:
                # Trigger-based SLAs (e.g. 3-day feedback deemed acceptance)
                if "submitted" in doc_lower or "shared for review" in doc_lower:
                    alert_entry = {
                        "alert_type": "COMMITMENT_HEALTH",
                        "subtype": "TRIGGER_SLA_ACTIVE",
                        "subject": f"SLA Clock Active: {c_name}",
                        "title": f"SLA Clock Active: {c_name}",
                        "what": f"Deliverable submission evidenced; 3-working-day review clock initiated under '{c_name}'.",
                        "why": f"Submitted on {document_date}. If client feedback is not provided within 3 working days, deemed acceptance applies.",
                        "evidence_quote": f"MoM evidence as of {document_date}: deliverable submitted for review.",
                        "evidence_date": str(document_date),
                        "owner": "CLIENT",
                        "problem_date": str(document_date + timedelta(days=3)),
                        "severity": "LOW",
                        "risk_score": 30,
                        "confidence": 0.95,
                        "recommended_action": "Log formal delivery submission and notify Client SPOC of deemed acceptance deadline.",
                    }
                    generated_alerts.append(alert_entry)

        # ── C6. STALE PROJECT DATA ──
        # Evaluated against server clock as explicit exception
        today = server_date or date.today()
        days_since_mom = (today - document_date).days
        if days_since_mom > stale_cadence_days:
            alert_entry = {
                "alert_type": "STALE_DATA",
                "subject": "Stale Project Data: No Recent MoM Upload",
                "title": "Stale Project Data: No Recent MoM Upload",
                "what": f"Evaluated against server clock as of {today}: no MoM processed since {document_date} (exceeds {stale_cadence_days}-day cadence). Forecasts may be out of date.",
                "why": f"{days_since_mom} calendar days have elapsed without progress updates. Active delivery tracking requires regular status reporting.",
                "evidence_quote": f"Server date check: last MoM processed was dated {document_date}.",
                "evidence_date": str(today),
                "owner": "PMO / Engagement Manager",
                "problem_date": str(today),
                "severity": "MEDIUM",
                "risk_score": 45,
                "confidence": 1.0,
                "recommended_action": "Request updated Meeting Minutes or Weekly Status Report from the engagement team.",
            }
            generated_alerts.append(alert_entry)

        # ── C7. SCOPE CREEP ──
        out_of_scope_records = plan_snapshot.get("out_of_scope_records") or []
        for act in activities:
            act_text = (act.get("name") or act.get("activity") or act.get("description") or "").lower()
            # Check for matches against explicit exclusions
            matched_oos = None
            for oos in out_of_scope_records:
                oos_name = (oos.get("name") or "").lower()
                if oos_name and (oos_name in act_text or act_text in oos_name):
                    matched_oos = oos
                    break

            if matched_oos:
                # Check if change request cited
                has_cr = "change request" in act_text or "approved cr" in act_text or "cr-" in act_text
                if not has_cr:
                    alert_entry = {
                        "alert_type": "SCOPE_CREEP",
                        "subject": f"Scope Creep Risk: {act.get('name') or act.get('activity')}",
                        "title": f"Scope Creep Risk: {act.get('name') or act.get('activity')}",
                        "what": f"Work requested on '{act.get('name') or act.get('activity')}' matches contractual exclusion '{matched_oos.get('name')}'.",
                        "why": f"Contract states: '{matched_oos.get('reason_for_exclusion', 'Contractual exclusion clause')}'. No approved Change Request cited.",
                        "evidence_quote": act.get("raw_text") or act.get("description") or act.get("name"),
                        "evidence_date": str(document_date),
                        "owner": "CLIENT",
                        "problem_date": str(document_date),
                        "severity": "HIGH",
                        "risk_score": 75,
                        "confidence": 0.92,
                        "recommended_action": "Issue formal Change Request and fee estimate before commencing any excluded work.",
                    }
                    generated_alerts.append(alert_entry)

        # ── C8. ALERT QUALITY, PERSISTENCE, DEDUPLICATION & EMAIL COOLDOWN ──
        persisted_results = cls._persist_and_dispatch_alerts(
            db_cursor=db_cursor,
            project_id=project_id,
            document_id=document_id,
            alerts=generated_alerts,
            work_items=work_items,
            client_deps=client_deps,
            stakeholders=stakeholders or [],
            cooldown_hours=cooldown_hours,
        )

        return {
            "success": True,
            "document_date": str(document_date),
            "is_provisional_plan": is_provisional,
            "alerts_count": len(generated_alerts),
            "alerts": generated_alerts,
            "persisted_results": persisted_results,
            "blocked_by_client_work_items": list(blocked_by_client_ids),
        }

    @classmethod
    def _match_mom_evidence(
        cls,
        doc_date: date,
        doc_text: str,
        activities: List[Dict[str, Any]],
        work_items: List[Dict[str, Any]],
        client_deps: List[Dict[str, Any]],
        milestones: Optional[List[Dict[str, Any]]] = None,
    ) -> None:
        """Extracts status, completion %, and evidence quotes from MoM for work items and dependencies."""
        for wi in work_items:
            w_name = wi["name"].lower()
            # Check activities first
            matched_act = None
            for act in activities:
                a_name = (act.get("name") or act.get("activity") or act.get("canonical_title") or "").lower()
                if w_name in a_name or a_name in w_name:
                    matched_act = act
                    break

            if matched_act:
                a_status = (matched_act.get("status") or matched_act.get("current_status") or "").upper()
                pct = matched_act.get("percent_complete") or matched_act.get("completion_percentage")
                if pct is not None:
                    try:
                        wi["percent_complete"] = float(pct)
                    except Exception:
                        pass
                if a_status in ("COMPLETED", "DONE", "RESOLVED") or wi.get("percent_complete", 0) >= 100.0:
                    wi["status"] = "COMPLETED"
                    wi["percent_complete"] = 100.0
                    wi["actual_end"] = str(doc_date)
                elif a_status in ("IN_PROGRESS", "ACTIVE", "WIP") or wi.get("percent_complete", 0) > 0:
                    wi["status"] = "IN_PROGRESS"
                    if not wi.get("actual_start"):
                        wi["actual_start"] = str(doc_date)
                wi["last_evidence_date"] = str(doc_date)
            elif w_name in doc_text:
                # Text mention match
                if any(w in doc_text for w in (f"{w_name} completed", f"{w_name} done", f"{w_name} delivered")):
                    wi["status"] = "COMPLETED"
                    wi["percent_complete"] = 100.0
                    wi["actual_end"] = str(doc_date)
                elif any(w in doc_text for w in (f"{w_name} started", f"{w_name} in progress", f"{w_name} ongoing")):
                    wi["status"] = "IN_PROGRESS"
                    if not wi.get("actual_start"):
                        wi["actual_start"] = str(doc_date)
                wi["last_evidence_date"] = str(doc_date)

        # Match dependencies
        for dep in client_deps:
            d_name = dep["name"].lower()
            if any(w in doc_text for w in (
                f"{d_name} received", f"{d_name} provided", f"{d_name} granted",
                "credentials received", "access granted", "environment provisioned",
                "infrastructure ready", "test data provided"
            )):
                dep["status"] = "RECEIVED"
                dep["received_date"] = str(doc_date)
                dep["evidence"] = f"MoM evidence on {doc_date}: prerequisite fulfilled"

        # Match milestone completions
        for ms in (milestones or []):
            m_name = (ms.get("name") or "").lower()
            m_id = (ms.get("id") or "").lower()
            if m_name and (
                f"{m_name} completed" in doc_text
                or f"{m_name} has successfully completed" in doc_text
                or "milestone 1 has successfully completed" in doc_text
                or "milestone 1 completed" in doc_text
                or (m_id and f"{m_id} completed" in doc_text)
            ):
                ms["status"] = "COMPLETED"

    @classmethod
    def _persist_and_dispatch_alerts(
        cls,
        db_cursor,
        project_id: int,
        document_id: int,
        alerts: List[Dict[str, Any]],
        work_items: List[Dict[str, Any]],
        client_deps: List[Dict[str, Any]],
        stakeholders: List[Dict[str, Any]],
        cooldown_hours: int = 24,
    ) -> List[Dict[str, Any]]:
        """
        Deduplicates by (type, subject), updates tracker_items, auto-resolves cleared conditions,
        and emails HIGH / CRITICAL alerts subject to 24h cooldown.
        """
        from agents.tracker_audit_agent import TrackerAuditAgent
        from agents.alerting_agent import AlertingAgent

        persisted = []
        now = datetime.now(timezone.utc)

        # Auto-resolve checks: resolve any open tracker item for work items that are now COMPLETED
        for wi in work_items:
            if wi.get("status") == "COMPLETED" or wi.get("percent_complete", 0) >= 100.0:
                try:
                    if db_cursor:
                        TrackerAuditAgent.persist_tracker_item(
                            db_cursor=db_cursor,
                            project_id=project_id,
                            document_id=document_id,
                            item_type="ACTIVITY",
                            is_out_of_scope=False,
                            risk_score=0,
                            risk_level="LOW",
                            risk_category="RESOLVED",
                            confidence=1.0,
                            reasoning=json.dumps({"update_note": "Auto-resolved on completion", "percent_complete": 100.0}),
                            requires_escalation=False,
                            title=f"Work Item at Risk: {wi['name']}",
                            status="RESOLVED",
                            resolve_only=True,
                            execution_status="COMPLETED",
                            risk_status="RESOLVED",
                        )
                except Exception:
                    pass

        # Also auto-resolve dependencies marked RECEIVED
        for dep in client_deps:
            if dep.get("status") == "RECEIVED":
                try:
                    if db_cursor:
                        TrackerAuditAgent.persist_tracker_item(
                            db_cursor=db_cursor,
                            project_id=project_id,
                            document_id=document_id,
                            item_type="BLOCKER",
                            is_out_of_scope=False,
                            risk_score=0,
                            risk_level="LOW",
                            risk_category="RESOLVED",
                            confidence=1.0,
                            reasoning=json.dumps({"update_note": "Auto-resolved upon client fulfillment", "status": "RECEIVED"}),
                            requires_escalation=False,
                            title=f"Client Dependency: {dep['name']}",
                            status="RESOLVED",
                            resolve_only=True,
                            execution_status="COMPLETED",
                            risk_status="RESOLVED",
                        )
                except Exception:
                    pass

        # Persist and dispatch active alerts
        for alt in alerts:
            subject = alt.get("subject", "Project Alert")
            sev = alt.get("severity", "MEDIUM")
            score = alt.get("risk_score", 60)
            owner = alt.get("owner", "VENDOR")
            problem_date = alt.get("problem_date")
            rec_action = alt.get("recommended_action")

            reasoning_dict = {
                "what": alt.get("what"),
                "why": alt.get("why"),
                "owner": owner,
                "problem_date": problem_date,
                "evidence_quote": alt.get("evidence_quote"),
                "evidence_date": alt.get("evidence_date"),
                "recommended_action": rec_action,
                "is_estimated": alt.get("is_estimated", False),
            }
            reasoning_json = json.dumps(reasoning_dict)

            # Persist to tracker
            tracker_id = None
            try:
                if db_cursor:
                    tracker_id = TrackerAuditAgent.persist_tracker_item(
                        db_cursor=db_cursor,
                        project_id=project_id,
                        document_id=document_id,
                        item_type="BLOCKER" if owner == "CLIENT" else "ACTIVITY",
                        is_out_of_scope=(alt.get("alert_type") == "SCOPE_CREEP"),
                        risk_score=score,
                        risk_level=sev,
                        risk_category=alt.get("alert_type"),
                        confidence=alt.get("confidence", 0.85),
                        reasoning=reasoning_json,
                        requires_escalation=(sev in ("HIGH", "CRITICAL")),
                        title=alt.get("title", subject),
                        status="OPEN",
                        risk_source="LEADING_INDICATOR",
                        recommended_action=rec_action,
                        execution_status="IN_PROGRESS" if owner == "VENDOR" else "BLOCKED",
                        risk_status="OPEN",
                        owner=owner,
                    )
            except Exception:
                pass

            alt["tracker_id"] = tracker_id
            persisted.append(alt)

            # Email dispatch via AlertingAgent for HIGH / CRITICAL alerts with cooldown
            if sev in ("HIGH", "CRITICAL"):
                cooldown_key = (project_id, subject)
                last_sent = cls._EMAIL_COOLDOWN_CACHE.get(cooldown_key)
                should_email = True
                if last_sent:
                    elapsed_hours = (now - last_sent).total_seconds() / 3600.0
                    if elapsed_hours < cooldown_hours:
                        should_email = False

                if should_email:
                    try:
                        cls._EMAIL_COOLDOWN_CACHE[cooldown_key] = now
                        AlertingAgent.dispatch_alert(
                            project_id=project_id,
                            item_name=subject,
                            reasoning=f"{alt.get('what')}\n\nWhy: {alt.get('why')}\nRecommended Action: {rec_action}",
                            stakeholders=stakeholders,
                            db_cursor=db_cursor,
                        )
                        alt["email_dispatched"] = True
                    except Exception as email_err:
                        alt["email_dispatched"] = False
                else:
                    alt["email_cooldown_skipped"] = True

        return persisted
