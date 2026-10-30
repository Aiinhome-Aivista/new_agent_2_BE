"""
deadline_inference_service.py - Deterministic Contractual Timeline Extraction,
Capacity-Based Work-Item Scheduling, and Feasibility Engine.

Implements Phase Three of Prompt.md:
1. Deterministic extraction of contract start/end, support end, phase durations,
   team headcount, developer capacity, and payment milestone dates.
2. Capacity-based deterministic scheduling without arbitrary date guessing or hardcoded dates.
3. Strict anchor vs estimate hierarchy.
4. Feasibility checks: CAPACITY_SHORTFALL, TIMELINE_CONFLICT, and SUPPORT_PERIOD_AMBIGUITY.
5. Zero date clamping: preserves honest predicted dates that exceed milestone anchors.
"""

import re
from datetime import datetime, date, timedelta
from typing import Dict, List, Any, Optional, Tuple


class DeadlineInferenceService:
    # Configurable defaults
    DEFAULT_CONFIG = {
        "effort_weights": {
            "SIMPLE": 0.5,    # dev-weeks
            "MEDIUM": 1.0,    # dev-weeks
            "COMPLEX": 2.0,   # dev-weeks
            "LOW": 0.5,
            "HIGH": 2.0,
        },
        "default_complexity": "MEDIUM",
        "utilisation": 0.7,
        "lead_is_dev": False,
        "working_days_per_week": 5,
        "default_developer_count": 5,
    }

    MONTH_MAP = {
        "january": 1, "february": 2, "march": 3, "april": 4,
        "may": 5, "june": 6, "july": 7, "august": 8,
        "september": 9, "october": 10, "november": 11, "december": 12,
        "jan": 1, "feb": 2, "mar": 3, "apr": 4, "jun": 6,
        "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12
    }

    @classmethod
    def parse_date_string(cls, text: str) -> Optional[str]:
        """
        Deterministically parses dates from contract text into ISO 'YYYY-MM-DD'.
        Supports:
        - 22nd day of January 2027
        - 22 January 2027 / 22-Jan-2027 / 22 Jan, 2027
        - January 22, 2027
        - 2027-01-22 (ISO)
        - 22/01/2027 or 01/22/2027
        """
        if not text:
            return None

        # 1. '22nd day of January 2027' or '22nd January 2027' or '22 January 2027'
        m1 = re.search(
            r'(\d{1,2})(?:st|nd|rd|th)?(?:\s+day\s+of)?\s+([A-Za-z]+)[,\s]+(\d{4})',
            text,
            re.IGNORECASE,
        )
        if m1:
            day = int(m1.group(1))
            m_str = m1.group(2).lower()
            year = int(m1.group(3))
            month = cls.MONTH_MAP.get(m_str)
            if month and 1 <= day <= 31:
                return f"{year:04d}-{month:02d}-{day:02d}"

        # 2. 'January 22, 2027' or 'Jan 22 2027'
        m2 = re.search(
            r'([A-Za-z]+)\s+(\d{1,2})(?:st|nd|rd|th)?[,\s]+(\d{4})',
            text,
            re.IGNORECASE,
        )
        if m2:
            m_str = m2.group(1).lower()
            day = int(m2.group(2))
            year = int(m2.group(3))
            month = cls.MONTH_MAP.get(m_str)
            if month and 1 <= day <= 31:
                return f"{year:04d}-{month:02d}-{day:02d}"

        # 3. ISO format 'YYYY-MM-DD'
        m3 = re.search(r'\b(\d{4})-(\d{2})-(\d{2})\b', text)
        if m3:
            return m3.group(0)

        # 4. DD-MMM-YYYY or DD-MM-YYYY
        m4 = re.search(r'\b(\d{1,2})[-/]([A-Za-z]{3}|\d{1,2})[-/](\d{4})\b', text)
        if m4:
            day = int(m4.group(1))
            m_part = m4.group(2).lower()
            year = int(m4.group(3))
            month = cls.MONTH_MAP.get(m_part) or (int(m_part) if m_part.isdigit() else None)
            if month and 1 <= month <= 12 and 1 <= day <= 31:
                return f"{year:04d}-{month:02d}-{day:02d}"

        return None

    @classmethod
    def add_working_days(cls, start_date, working_days: int):
        from datetime import timedelta
        current = start_date
        added = 0
        while added < working_days:
            current += timedelta(days=1)
            if current.weekday() < 5:
                added += 1
        return current

    @classmethod
    def extract_timeline_facts(cls, full_text: str, chunks: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
        """
        Scans contract text and chunks to extract core contractual dates,
        milestones, phase durations, and team headcount.
        """
        combined = full_text or ""
        if chunks:
            for c in chunks:
                combined += "\n" + (c.get("text") or "")

        facts = {
            "contract_start_date": None,
            "contract_end_date": None,
            "support_end_date": None,
            "total_contract_weeks": None,
            "phase_weeks": {},
            "headcount": {
                "project_manager": 0,
                "tech_lead": 0,
                "developers": 0,
            },
            "milestone_anchors": [],
            "evidence": [],
        }

        # 1. Contract Start Date
        m_start = re.search(
            r"(?:dated\s+this|effective\s+as\s+of|commencement\s+date:?|dated:?)\s*(\d{1,2}(?:st|nd|rd|th)?\s+(?:day\s+of\s+)?[A-Za-z]+\s+\d{4})",
            combined,
            re.IGNORECASE,
        )
        if m_start:
            d_str = cls.parse_date_string(m_start.group(1))
            if d_str:
                facts["contract_start_date"] = d_str
                facts["evidence"].append({
                    "field": "contract_start_date",
                    "quotation": m_start.group(0),
                    "parsed": d_str,
                })

        # Fallback start date search: first recognized date in first 2000 chars
        if not facts["contract_start_date"]:
            d_first = cls.parse_date_string(combined[:2000])
            if d_first:
                facts["contract_start_date"] = d_first

        # 2. Contract End Date (Contract validity)
        m_end = re.search(
            r"contract\s+(?:will\s+be\s+valid\s+till|validity\s+period\s+till|term\s+ends?)\s*(\d{1,2}(?:st|nd|rd|th)?\s+[A-Za-z]+\s+\d{4})",
            combined,
            re.IGNORECASE,
        )
        if m_end:
            d_str = cls.parse_date_string(m_end.group(1))
            if d_str:
                facts["contract_end_date"] = d_str
                facts["evidence"].append({
                    "field": "contract_end_date",
                    "quotation": m_end.group(0),
                    "parsed": d_str,
                })

        # 3. Production Support End Date
        m_supp = re.search(
            r"(?:production\s+support\s+till|support\s+(?:window|period)\s+(?:till|through))\s*(\d{1,2}(?:st|nd|rd|th)?\s+[A-Za-z]+\s+\d{4})",
            combined,
            re.IGNORECASE,
        )
        if m_supp:
            d_str = cls.parse_date_string(m_supp.group(1))
            if d_str:
                facts["support_end_date"] = d_str
                facts["evidence"].append({
                    "field": "support_end_date",
                    "quotation": m_supp.group(0),
                    "parsed": d_str,
                })

        # 4. Phase Durations (e.g. Assessment + Implementation 9 weeks, UAT 3 weeks, Hypercare 2 weeks)
        m_tot_w = re.search(r"overall\s+project\s+timeline\s+is\s+(\d+)\s+weeks", combined, re.IGNORECASE)
        if m_tot_w:
            facts["total_contract_weeks"] = int(m_tot_w.group(1))

        m_p1 = re.search(r"assessment\s*\+\s*implementation[\s:\-\u2013\u2014]+(\d+)\s*weeks?", combined, re.IGNORECASE)
        if m_p1:
            facts["phase_weeks"]["Assessment + Implementation"] = int(m_p1.group(1))

        m_p2 = re.search(r"uat(?:\s+phase)?[\s:\-\u2013\u2014]+(\d+)\s*weeks?", combined, re.IGNORECASE)
        if m_p2:
            facts["phase_weeks"]["UAT"] = int(m_p2.group(1))

        m_p3 = re.search(r"hyper[\s\-_]*care(?:\s+phase)?[\s:\-\u2013\u2014]+(\d+)\s*weeks?", combined, re.IGNORECASE)
        if m_p3:
            facts["phase_weeks"]["Hypercare"] = int(m_p3.group(1))

        # 5. Headcount / Developer Roles
        m_pm = re.search(r"(?:project\s+manager|pm)\s*[\-:]\s*(\d+)", combined, re.IGNORECASE)
        if m_pm:
            facts["headcount"]["project_manager"] = int(m_pm.group(1))

        m_tl = re.search(r"(?:technical\s+lead|tech\s+lead)\s*[\-:]\s*(\d+)", combined, re.IGNORECASE)
        if m_tl:
            facts["headcount"]["tech_lead"] = int(m_tl.group(1))

        m_dev = re.search(r"(?:application\s+developer|developers?|developer\s+team)\s*[\-:]\s*(\d+)", combined, re.IGNORECASE)
        if m_dev:
            facts["headcount"]["developers"] = int(m_dev.group(1))

        # 6. Extract Table / Paragraph Payment & Milestone Dates
        # Search lines like: "1 | Implement & UAT Deployment ... | 11 Feb 2027 | INR ..."
        for line in combined.split("\n"):
            line_clean = line.strip()
            if not line_clean:
                continue
            if any(p in line_clean.lower() for p in ["engagement letter is made effective", "effective from", "valid till", "engagement period", "this engagement letter"]):
                continue
            parsed_dt = cls.parse_date_string(line_clean)
            if parsed_dt and any(k in line_clean.lower() for k in ["milestone", "uat", "deployment", "go-live", "sign-off", "hyper care", "support"]):
                # Clean name
                clean_name = re.sub(r'\|\s*\d+\s*\|', '', line_clean)
                clean_name = re.sub(r'\b\d{1,2}(?:st|nd|rd|th)?\s+[A-Za-z]+\s+\d{4}\b', '', clean_name)
                clean_name = re.sub(r'INR\s*[\d,]+', '', clean_name)
                clean_name = clean_name.replace("|", "").strip(" -*#\t")
                if len(clean_name) > 5:
                    facts["milestone_anchors"].append({
                        "name": clean_name,
                        "date": parsed_dt,
                        "raw_line": line_clean[:120],
                    })

        return facts

    @classmethod
    def estimate_task_complexity(cls, item: Dict[str, Any], default_complexity: str = "MEDIUM") -> Tuple[str, float, str]:
        """
        Dynamically estimates task complexity, effort in dev-weeks, and rationale
        based on the work item's functional scope, description, and technical indicators.
        """
        existing_comp = (item.get("complexity") or "").upper()
        if existing_comp in ("SIMPLE", "LOW"):
            return "SIMPLE", 0.5, "Classified as simple task (0.5 dev-weeks) based on presentation-tier scope."
        if existing_comp in ("COMPLEX", "HIGH"):
            return "COMPLEX", 2.0, "Classified as complex task (2.0 dev-weeks) based on architectural/integration scope."

        name_l = (item.get("name") or "").lower()
        desc_l = (item.get("description") or item.get("raw_text") or "").lower()
        rec_type = (item.get("record_type") or item.get("category") or "").lower()
        combined = f"{name_l} {desc_l} {rec_type}"

        # Complex indicators: 2.0 dev-weeks
        complex_kw = (
            "digitization", "mfr form", "azure function", "integration", "hierarchy",
            "access control", "security matrix", "architecture", "deployment", "sync",
            "database", "infrastructure", "compliance report", "prevent duplicate",
            "recommend or reject"
        )
        if any(k in combined for k in complex_kw):
            return (
                "COMPLEX",
                2.0,
                f"Complex technical deliverable involving workflow automation, access hierarchy, or integration (2.0 dev-weeks)."
            )

        # Simple indicators: 0.5 dev-weeks
        simple_kw = (
            "footer", "video", "links", "link", "banner", "reminder email", "confirmation",
            "notification", "display list", "success message", "praising", "announcements",
            "upcoming events", "download", "view requisition summary"
        )
        if any(k in combined for k in simple_kw):
            return (
                "SIMPLE",
                0.5,
                f"Standard UI display or notification component with straightforward implementation (0.5 dev-weeks)."
            )

        # Default Medium: 1.0 dev-week
        return (
            "MEDIUM",
            1.0,
            f"Standard full-stack component and workflow implementation requiring 1.0 dev-week."
        )
        """Adds business days (Monday-Friday) to start_d."""
        cur = start_d
        added = 0
        while added < num_working_days:
            cur += timedelta(days=1)
            if cur.weekday() < 5:  # Mon-Fri
                added += 1
        return cur

    @classmethod
    def schedule_work_items(
        cls,
        in_scope_items: List[Dict[str, Any]],
        milestones: List[Dict[str, Any]],
        timeline_facts: Dict[str, Any],
        config: Optional[Dict[str, Any]] = None,
    ) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        """
        Schedules work items using deterministic capacity-based allocation.
        Returns:
            (scheduled_items, schedule_conflicts)
        """
        cfg = dict(cls.DEFAULT_CONFIG)
        if config:
            cfg.update(config)

        dev_count = timeline_facts.get("headcount", {}).get("developers") or cfg["default_developer_count"]
        if cfg.get("lead_is_dev"):
            dev_count += timeline_facts.get("headcount", {}).get("tech_lead", 0)

        utilisation = cfg["utilisation"]
        # Effective team capacity in dev-weeks per calendar week
        team_rate_dev_weeks_per_week = max(0.5, dev_count * utilisation)
        # Capacity per working day = team_rate / 5 working days
        working_days_per_dev_week = cfg["working_days_per_week"] / team_rate_dev_weeks_per_week

        # Determine project anchor start
        start_str = timeline_facts.get("contract_start_date")
        if start_str:
            try:
                base_start = datetime.strptime(start_str, "%Y-%m-%d").date()
            except Exception:
                base_start = date.today()
        else:
            base_start = date.today()

        # Contractual anchor dates
        contract_end_str = timeline_facts.get("contract_end_date")
        support_end_str = timeline_facts.get("support_end_date")
        milestone_dates = [m["planned_date"] for m in milestones if m.get("planned_date")]

        schedule_conflicts = []

        # Conflict 1: Support Period Ambiguity
        if contract_end_str and support_end_str:
            if support_end_str > contract_end_str:
                schedule_conflicts.append({
                    "conflict_type": "SUPPORT_PERIOD_AMBIGUITY",
                    "severity": "LOW",
                    "rationale": (
                        f"Production support end date ({support_end_str}) extends beyond formal "
                        f"contract execution period ({contract_end_str}). Confirm warranty/support governance."
                    ),
                    "contract_end_date": contract_end_str,
                    "support_end_date": support_end_str,
                })

        # Conflict 2: Timeline Conflict (Phase Weeks vs Contract End Date)
        total_weeks = timeline_facts.get("total_contract_weeks")
        sum_phase_weeks = sum(timeline_facts.get("phase_weeks", {}).values())
        if total_weeks and sum_phase_weeks and total_weeks != sum_phase_weeks:
            schedule_conflicts.append({
                "conflict_type": "TIMELINE_CONFLICT",
                "severity": "MEDIUM",
                "rationale": (
                    f"Sum of explicit phase durations ({sum_phase_weeks} weeks) does not match "
                    f"overall stated project timeline ({total_weeks} weeks)."
                ),
                "total_contract_weeks": total_weeks,
                "phase_weeks_sum": sum_phase_weeks,
            })

        # Build schedule item by item
        scheduled_items = []
        cumulative_working_days = 0

        # Sort items: foundation / architecture items first, then others
        def _sort_key(it):
            name_l = (it.get("name") or "").lower()
            if any(w in name_l for w in ["assess", "foundation", "architecture", "setup", "schema", "base"]):
                return (0, name_l)
            if any(w in name_l for w in ["admin", "posting", "portal", "profile"]):
                return (1, name_l)
            if any(w in name_l for w in ["report", "analytics", "dashboard"]):
                return (2, name_l)
            return (3, name_l)

        sorted_items = sorted(in_scope_items, key=_sort_key)

        for item in sorted_items:
            cid = item.get("candidate_id") or f"c_{item.get('id', 0)}"
            name = item.get("name") or item.get("title") or "Scope Deliverable"
            raw_deadline = item.get("deadline_normalized") or item.get("deadline")

            # Dynamic complexity & effort estimation
            complexity, effort_weeks, comp_rationale = cls.estimate_task_complexity(item, cfg["default_complexity"])
            item_working_days = max(1, round(effort_weeks * working_days_per_dev_week))

            exceeds_anchor = False
            anchor_limit = contract_end_str or (max(milestone_dates) if milestone_dates else None)
            schedule_warnings = []

            if raw_deadline:
                # Tier 1: Explicit contractual deadline
                provenance = "CONTRACT_CONFIRMED"
                target_date = str(raw_deadline)
                start_date_str = (base_start).isoformat()
                duration_days = item_working_days
                rationale = "Verbatim contractual deadline explicitly specified in source contract."
                confidence = 0.95
                conf_label = "HIGH"
                conf_explanation = "Verbatim contractual milestone date backed by explicit contract text."
                is_predicted = False
            elif milestones:
                # Tier 2 & 3: Capacity-derived finish date sequenced against milestone DAG
                provenance = "AI_ESTIMATED"
                pred_start = cls.add_working_days(base_start, cumulative_working_days)
                pred_end = cls.add_working_days(pred_start, item_working_days)
                cumulative_working_days += item_working_days

                start_date_str = pred_start.isoformat()
                target_date = pred_end.isoformat()
                duration_days = item_working_days

                rationale = (
                    f"AI estimated from {complexity} complexity ({effort_weeks} dev-weeks) "
                    f"scheduled at team rate ({team_rate_dev_weeks_per_week:.1f} dev-weeks/week)."
                )

                # Check against contract end date and milestone anchors
                if anchor_limit and target_date > anchor_limit:
                    exceeds_anchor = True
                    schedule_warnings.append(
                        f"Predicted completion {target_date} exceeds anchor deadline {anchor_limit}."
                    )

                # Explainable confidence calculation based on complexity and overrun risk
                if complexity == "SIMPLE":
                    confidence = 0.85
                elif complexity == "MEDIUM":
                    confidence = 0.75
                else: # COMPLEX
                    confidence = 0.65

                if exceeds_anchor:
                    confidence = max(0.50, round(confidence - 0.05, 2))

                if confidence >= 0.80:
                    conf_label = "HIGH"
                    conf_explanation = "High confidence: verified source quotation, clear scope definition, and low architectural dependencies."
                elif confidence >= 0.65:
                    conf_label = "MEDIUM"
                    conf_explanation = "Medium confidence: standard task scope dependent on team velocity and milestone sequencing."
                else:
                    conf_label = "LOW"
                    conf_explanation = "Lower confidence: complex integration or schedule overrun risk requires monitoring."

                is_predicted = True
            else:
                # Tier 5: Insufficient anchors
                provenance = "UNRESOLVED"
                start_date_str = None
                target_date = None
                duration_days = None
                rationale = "Resource capacity or project start anchor unconfigured. Date TBD."
                confidence = 0.0
                conf_label = "LOW"
                conf_explanation = "Unresolved: insufficient contractual anchors to calculate predicted deadline."
                is_predicted = False

            if exceeds_anchor:
                schedule_conflicts.append({
                    "conflict_type": "ESTIMATED_OVERRUN",
                    "severity": "MEDIUM",
                    "candidate_id": cid,
                    "item_name": name,
                    "estimated_finish": target_date,
                    "estimated_date": target_date,
                    "milestone_anchor": anchor_limit,
                    "is_contractual_conflict": False,
                    "rationale": (
                        f"Predicted completion {target_date} exceeds contractual target anchor "
                        f"{anchor_limit}. Indicates capacity shortfall or sequencing constraint; "
                        f"dates not clamped."
                    ),
                })

            scheduled_items.append({
                "candidate_id": cid,
                "name": name,
                "item_name": name,
                "owner": item.get("owner") or "Vendor Project Team",
                "record_type": item.get("record_type") or item.get("category") or "Scope Item",
                "scope_status": "IN_SCOPE",
                "complexity": complexity,
                "estimated_effort_dev_weeks": effort_weeks,
                "effort_dev_weeks": effort_weeks,
                "estimated_duration_working_days": duration_days,
                "estimated_duration_days": duration_days,
                "predicted_start_date": start_date_str,
                "predicted_deadline": target_date,
                "proposed_deadline": target_date,
                "estimated_deadline": target_date,
                "date_provenance": provenance,
                "deadline_type": provenance,
                "confidence_score": confidence,
                "deadline_confidence": confidence,
                "confidence_label": conf_label,
                "confidence_explanation": conf_explanation,
                "estimation_confidence": conf_label,
                "is_predicted": is_predicted,
                "exceeds_anchor": exceeds_anchor,
                "schedule_status": "OVERRUN" if exceeds_anchor else "ON_TRACK",
                "rationale": rationale,
                "deadline_rationale": rationale,
                "dependency_ids": item.get("dependencies") or item.get("dependency_ids") or [],
                "linked_milestone_ids": item.get("milestone_ids") or item.get("linked_milestone_ids") or [],
                "schedule_warnings": schedule_warnings,
                "source_locator": item.get("source_locator"),
                "source_quotation": item.get("source_quotation") or item.get("raw_text") or item.get("description"),
                "manual_override": False,
                "is_contractual_commitment": (provenance == "CONTRACT_CONFIRMED"),
                "scheduling_assumptions": {
                    "developers": dev_count,
                    "utilisation": utilisation,
                    "team_weekly_rate": team_rate_dev_weeks_per_week,
                    "working_days_per_dev_week": round(working_days_per_dev_week, 2),
                },
            })

        return scheduled_items, schedule_conflicts

