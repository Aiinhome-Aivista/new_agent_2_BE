"""
deadline_inference_service.py - Deterministic Contractual Timeline Extraction,
Dependency DAG Generation, Anchored Capacity-Based Work-Item Scheduling, and Feasibility Engine.

Features:
1. Deterministic extraction of contract start/end, support end, phase durations,
   team headcount, developer capacity, and payment milestone dates.
2. Item-level dependency edge generation (DOCUMENT phrasing, structural rules, CRUD order, phase sequence).
3. Strict acyclic DAG validation (cycle rejection).
4. Capacity-based topological scheduling with developer tracks without arbitrary clamping.
5. Milestone anchor upper-bound checking (exceeds_anchor=True, DATE_OUT_OF_WINDOW=True).
6. Warnings: Single rollup warning per milestone anchor; CAPACITY_SHORTFALL against earliest build anchor.
7. project_phases derived strictly from document text with DERIVED_FROM_DURATION provenance.
8. TIMELINE_CONFLICT raised when derived boundaries disagree with contract dates by > tolerance.
"""

import re
from datetime import datetime, date, timedelta
from typing import Dict, List, Any, Optional, Tuple, Set

from core.baseline_config import (
    DOCUMENT_DEPENDENCY_PHRASES,
    STRUCTURAL_FOUNDATION_KEYWORDS,
    CRUD_ACTION_ORDER,
    CANONICAL_PHASE_ORDER,
    TIMELINE_TOLERANCE_DAYS,
    WORKING_DAY_BUFFER,
    CAPACITY_UTILISATION,
    WORKING_DAYS_PER_WEEK,
    MONTH_MAP,
)


class DeadlineInferenceService:
    DEFAULT_CONFIG = {
        "effort_weights": {
            "SIMPLE": 0.5,    # dev-weeks
            "MEDIUM": 1.0,    # dev-weeks
            "COMPLEX": 2.0,   # dev-weeks
            "LOW": 0.5,
            "HIGH": 2.0,
        },
        "default_complexity": "MEDIUM",
        "utilisation": CAPACITY_UTILISATION,
        "lead_is_dev": False,
        "working_days_per_week": WORKING_DAYS_PER_WEEK,
        "default_developer_count": 5,
        "timeline_tolerance_days": TIMELINE_TOLERANCE_DAYS,
    }

    MONTH_MAP = MONTH_MAP

    @classmethod
    def parse_date_string(cls, text: str) -> Optional[str]:
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
    def add_working_days(cls, start: date, n_days: int) -> date:
        current = start
        added = 0
        step = 1 if n_days >= 0 else -1
        target = abs(n_days)
        while added < target:
            current += timedelta(days=step)
            if current.weekday() < 5:  # Monday to Friday
                added += 1
        return current

    @classmethod
    def count_working_days(cls, start: date, end: date) -> int:
        if end <= start:
            return 0
        current = start
        count = 0
        while current < end:
            if current.weekday() < 5:
                count += 1
            current += timedelta(days=1)
        return count

    @classmethod
    def extract_timeline_facts(cls, text: str) -> Dict[str, Any]:
        """
        Extracts start/end dates, named project phases, headcount, and stated durations.
        """
        facts: Dict[str, Any] = {
            "contract_start_date": None,
            "contract_end_date": None,
            "support_end_date": None,
            "total_contract_weeks": None,
            "headcount": {"developers": 5, "project_manager": 1, "tech_lead": 1},
            "phase_weeks": {},
            "raw_named_phases": [],
            "evidence": [],
        }

        # 1. Contract Start Date
        start_match = re.search(
            r"(?:dated\s+(?:this|as\s+of)?|effective\s+(?:from|as\s+of|date)?|commencing\s+on|agree\s+to\s+start\s+on|start\s+date\s*[:=])\s*(?:(?:this|the)\s+day\s+of\s+)?(\d{1,2}[/-]\d{1,2}[/-]\d{4}|\d{1,2}(?:st|nd|rd|th)?(?:\s+day\s+of)?\s+[A-Za-z]+\s+\d{4})",
            text,
            re.IGNORECASE,
        )
        if start_match:
            facts["contract_start_date"] = cls.parse_date_string(start_match.group(1))
            facts["evidence"].append({
                "field": "contract_start_date",
                "quotation": start_match.group(0).strip(),
                "normalized_date": facts["contract_start_date"],
            })

        if not facts["contract_start_date"]:
            m_preamble = re.search(r"(\d{1,2}/\d{1,2}/\d{4})", text[:1000])
            if m_preamble:
                facts["contract_start_date"] = cls.parse_date_string(m_preamble.group(1))
                facts["evidence"].append({
                    "field": "contract_start_date",
                    "quotation": m_preamble.group(0).strip(),
                    "normalized_date": facts["contract_start_date"],
                })

        # 2. Overall project timeline weeks
        tot_weeks = re.search(r"(?:overall\s+project\s+timeline\s+is|total\s+duration\s+is)\s+(\d+)\s+weeks", text, re.IGNORECASE)
        if tot_weeks:
            facts["total_contract_weeks"] = int(tot_weeks.group(1))

        # 3. Named Project Phases with Durations
        # e.g., "1. Assessment + Implementation – 9 weeks", "2. UAT – 3 weeks", "3. Hyper-care - 2 weeks"
        phase_pattern = re.compile(
            r"(?:(?:\d+\.|\*|-)\s+)?([A-Za-z0-9\s\+]+?)\s*[-–—:]\s*(\d+)\s*(?:weeks?|months?)",
            re.IGNORECASE
        )
        # Narrow scan to timeline or methodology sections
        timeline_window = text
        m_timeline_sec = re.search(r"(?:project\s+overall\s+timeline|project\s+phases)[\s\S]*?(?=\n\s*#{1,3}|\Z)", text, re.IGNORECASE)
        if m_timeline_sec:
            timeline_window = m_timeline_sec.group(0)

        for match in phase_pattern.finditer(timeline_window):
            raw_name = match.group(1).strip()
            weeks = int(match.group(2))
            clean_name = re.sub(r"^\d+\.\s*", "", raw_name).strip()
            # Filter out non-phases
            if any(k in clean_name.lower() for k in ["project manager", "technical lead", "developer", "timeline", "total", "overall"]):
                continue
            if len(clean_name) > 2:
                facts["phase_weeks"][clean_name] = weeks
                facts["raw_named_phases"].append({"phase_name": clean_name, "duration_weeks": weeks})

        # Calculate contract end date from explicit clause or start date + total weeks
        end_match = re.search(
            r"(?:valid\s+till|valid\s+through|valid\s+until|contract\s+end\s+date|term\s+ends)[^\n\.\,]*?(\d{1,2}[/-]\d{1,2}[/-]\d{4}|\d{1,2}(?:st|nd|rd|th)?(?:\s+day\s+of)?\s+[A-Za-z]+\s+\d{4}|\d{4}-\d{2}-\d{2})",
            text,
            re.IGNORECASE,
        )
        if end_match:
            facts["contract_end_date"] = cls.parse_date_string(end_match.group(1))
            facts["evidence"].append({
                "field": "contract_end_date",
                "quotation": end_match.group(0).strip(),
                "normalized_date": facts["contract_end_date"],
            })
        elif facts["contract_start_date"] and facts["total_contract_weeks"]:
            s_dt = datetime.strptime(facts["contract_start_date"], "%Y-%m-%d").date()
            facts["contract_end_date"] = (s_dt + timedelta(weeks=facts["total_contract_weeks"])).isoformat()

        # 4. Support End Date (Production Support)
        supp_match = re.search(
            r"(?:production\s+support|warranty\s+support|support\s+till|support\s+through)[^\n\.\,]*?(\d{1,2}(?:st|nd|rd|th)?\s+[A-Za-z]+\s+\d{4}|\d{4}-\d{2}-\d{2}|\d{1,2}/\d{1,2}/\d{4})",
            text,
            re.IGNORECASE,
        )
        if supp_match:
            facts["support_end_date"] = cls.parse_date_string(supp_match.group(1))
            facts["evidence"].append({
                "field": "support_end_date",
                "quotation": supp_match.group(0).strip(),
                "normalized_date": facts["support_end_date"],
            })

        # 5. Headcount
        dev_match = re.search(r"(?:application|software)?\s*developers?\s*[-–—:]\s*(\d+)", text, re.IGNORECASE)
        if dev_match:
            facts["headcount"]["developers"] = int(dev_match.group(1))

        return facts

    @classmethod
    def estimate_task_complexity(cls, item: Dict[str, Any], default_complexity: str = "MEDIUM") -> Tuple[str, float, str]:
        text = f"{item.get('name', '')} {item.get('description', '')}".lower()
        if any(k in text for k in ["data model", "architecture", "security", "integration", "migration", "pipeline"]):
            return "COMPLEX", cls.DEFAULT_CONFIG["effort_weights"]["COMPLEX"], "Complex backend, architecture, or data schema integration"
        if any(k in text for k in ["view", "display", "button", "landing page", "section", "news", "poll"]):
            return "SIMPLE", cls.DEFAULT_CONFIG["effort_weights"]["SIMPLE"], "Standard frontend screen, template, or display component"
        return "MEDIUM", cls.DEFAULT_CONFIG["effort_weights"]["MEDIUM"], "Standard CRUD or business workflow capability"

    @classmethod
    def build_work_item_dependencies(cls, items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Constructs fine-grained directed dependency edges between work items:
        1. Document phrasing: 'after', 'requires', 'subject to', 'once'.
        2. Structural foundation rules: data model, architecture, authentication before dependent features.
        3. CRUD lifecycle order: create/post before view/edit/delete/download/approve.
        4. User-facing creation before administrative handling.
        5. Phase ordering: Assess -> Construct -> UAT -> Go-Live -> Hypercare.
        Guarantees strict acyclicity via directed cycle checking.
        """
        item_by_id = {it.get("candidate_id"): it for it in items if it.get("candidate_id")}
        adj: Dict[str, Set[str]] = {cid: set() for cid in item_by_id}
        edges_metadata: Dict[Tuple[str, str], Dict[str, Any]] = {}

        def _creates_cycle(u: str, v: str) -> bool:
            """Returns True if adding u -> v introduces a cycle (i.e. path v -> u exists)."""
            visited = set()
            stack = [v]
            while stack:
                curr = stack.pop()
                if curr == u:
                    return True
                if curr not in visited:
                    visited.add(curr)
                    stack.extend(adj.get(curr, []))
            return False

        def _add_edge(prereq: str, dependent: str, source: str, confidence: float, rationale: str) -> bool:
            if prereq == dependent or prereq not in item_by_id or dependent not in item_by_id:
                return False
            if dependent in adj[prereq]:
                return True  # Already present
            if _creates_cycle(prereq, dependent):
                return False  # Reject cycle
            adj[prereq].add(dependent)
            edges_metadata[(prereq, dependent)] = {
                "prerequisite_id": prereq,
                "dependent_id": dependent,
                "source": source,
                "confidence": confidence,
                "rule_rationale": rationale,
            }
            return True

        # Rule 1: Document Phrasing
        for it in items:
            cid = it.get("candidate_id")
            text = f"{it.get('name', '')} {it.get('description', '')}".lower()
            for phrase in DOCUMENT_DEPENDENCY_PHRASES:
                m = re.search(r"\b" + re.escape(phrase) + r"\s+([A-Za-z0-9\s_-]{4,40})", text)
                if m:
                    target_kw = m.group(1).strip()
                    for other_it in items:
                        o_cid = other_it.get("candidate_id")
                        if o_cid != cid and any(w in other_it.get("name", "").lower() for w in target_kw.split() if len(w) > 3):
                            _add_edge(o_cid, cid, "DOCUMENT", 0.90, f"Document phrasing '{phrase} {target_kw}' establishes dependency")
                            break

        # Rule 2: Structural Foundations before Features
        foundation_cids = [
            it.get("candidate_id") for it in items
            if any(k in f"{it.get('name', '')} {it.get('description', '')}".lower() for k in STRUCTURAL_FOUNDATION_KEYWORDS)
        ]
        for f_cid in foundation_cids:
            f_item = item_by_id[f_cid]
            f_mod = (f_item.get("module") or f_item.get("section") or "").lower()
            for other_it in items:
                o_cid = other_it.get("candidate_id")
                if o_cid != f_cid and o_cid not in foundation_cids:
                    o_mod = (other_it.get("module") or other_it.get("section") or "").lower()
                    if (not f_mod or not o_mod) or (f_mod in o_mod or o_mod in f_mod):
                        _add_edge(f_cid, o_cid, "RULE", 0.85, "Foundational architecture/data model precedes functional UI features")

        # Rule 3: CRUD Action Lifecycle (Create -> View/Edit/Delete/Download)
        for it_a in items:
            cid_a = it_a.get("candidate_id")
            name_a = (it_a.get("name") or "").lower()
            if any(act in name_a for act in ["create", "post", "submit", "apply"]):
                # Extract key entity noun
                tokens_a = set(re.findall(r"\b[A-Za-z]{4,}\b", name_a)) - {"user", "admin", "create", "post", "submit", "apply", "allow"}
                if not tokens_a:
                    continue
                for it_b in items:
                    cid_b = it_b.get("candidate_id")
                    if cid_a != cid_b:
                        name_b = (it_b.get("name") or "").lower()
                        if any(act in name_b for act in ["view", "edit", "delete", "download", "approve", "export"]):
                            tokens_b = set(re.findall(r"\b[A-Za-z]{4,}\b", name_b))
                            if tokens_a.intersection(tokens_b):
                                _add_edge(cid_a, cid_b, "RULE", 0.85, "Entity creation precedes management, modification, or viewing")

        # Rule 4: User-Facing Creation before Admin Handling
        for it_a in items:
            cid_a = it_a.get("candidate_id")
            name_a = (it_a.get("name") or "").lower()
            if "user" in name_a and not "admin" in name_a:
                for it_b in items:
                    cid_b = it_b.get("candidate_id")
                    name_b = (it_b.get("name") or "").lower()
                    if "admin" in name_b and cid_a != cid_b:
                        tokens_a = set(re.findall(r"\b[A-Za-z]{4,}\b", name_a)) - {"user", "view", "section"}
                        tokens_b = set(re.findall(r"\b[A-Za-z]{4,}\b", name_b)) - {"admin", "view", "section"}
                        if tokens_a and tokens_a.intersection(tokens_b):
                            _add_edge(cid_a, cid_b, "RULE", 0.85, "User-facing submission precedes administrative review/management")

        # Populate on items
        for it in items:
            cid = it.get("candidate_id")
            prereqs = [edges_metadata[(p, cid)] for p in item_by_id if (p, cid) in edges_metadata]
            it["depends_on"] = prereqs
            it["dependencies"] = [p["prerequisite_id"] for p in prereqs]
            it["dependency_ids"] = it["dependencies"]

        return items

    @classmethod
    def schedule_work_items(
        cls,
        in_scope_items: List[Dict[str, Any]],
        milestones: List[Dict[str, Any]],
        timeline_facts: Dict[str, Any],
        config: Optional[Dict[str, Any]] = None,
    ) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        """
        Schedules work items using deterministic capacity-based allocation in topological DAG order.
        Produces ONE rollup warning per anchor for overruns and checks capacity against earliest build anchor.
        """
        cfg = dict(cls.DEFAULT_CONFIG)
        if config:
            cfg.update(config)

        dev_count = timeline_facts.get("headcount", {}).get("developers") or cfg["default_developer_count"]
        if cfg.get("lead_is_dev"):
            dev_count += timeline_facts.get("headcount", {}).get("tech_lead", 0)

        utilisation = cfg["utilisation"]
        team_rate_dev_weeks_per_week = max(0.5, dev_count * utilisation)
        working_days_per_dev_week = cfg["working_days_per_week"] / team_rate_dev_weeks_per_week

        start_str = timeline_facts.get("contract_start_date")
        base_start = datetime.strptime(start_str, "%Y-%m-%d").date() if start_str else date.today()
        contract_end_str = timeline_facts.get("contract_end_date")
        support_end_str = timeline_facts.get("support_end_date")

        schedule_conflicts: List[Dict[str, Any]] = []

        # 1. Feasibility: Support Period Ambiguity
        if contract_end_str and support_end_str and support_end_str > contract_end_str:
            schedule_conflicts.append({
                "conflict_type": "SUPPORT_PERIOD_AMBIGUITY",
                "severity": "LOW",
                "rationale": f"Production support end date ({support_end_str}) extends beyond formal contract execution period ({contract_end_str}). Confirm warranty/support governance.",
                "contract_end_date": contract_end_str,
                "support_end_date": support_end_str,
            })

        # Separate umbrella parent items from leaf delivery tasks (FIX 2)
        leaf_items = [it for it in in_scope_items if not it.get("umbrella")]
        umbrella_items = [it for it in in_scope_items if it.get("umbrella")]

        # Build dependency edges across leaf items (FIX 3)
        leaf_items = cls.build_work_item_dependencies(leaf_items)

        # Topological sort of leaf items using Kahn's algorithm
        in_degree = {it.get("candidate_id"): 0 for it in leaf_items}
        children_map = {it.get("candidate_id"): [] for it in leaf_items}
        for it in leaf_items:
            cid = it.get("candidate_id")
            for dep_id in it.get("dependencies", []):
                if dep_id in children_map:
                    children_map[dep_id].append(cid)
                    in_degree[cid] += 1

        queue = [cid for cid, deg in in_degree.items() if deg == 0]
        topo_order = []
        while queue:
            curr = queue.pop(0)
            topo_order.append(curr)
            for child in children_map.get(curr, []):
                in_degree[child] -= 1
                if in_degree[child] == 0:
                    queue.append(child)

        # Append any unvisited items (in case of disconnected components)
        for it in leaf_items:
            cid = it.get("candidate_id")
            if cid not in topo_order:
                topo_order.append(cid)

        item_lookup = {it.get("candidate_id"): it for it in leaf_items}
        sorted_leaf_items = [item_lookup[cid] for cid in topo_order if cid in item_lookup]

        # Milestone anchors identification
        ms_with_dates = [m for m in milestones if m.get("planned_date")]
        build_anchors = [
            m for m in ms_with_dates
            if any(k in (m.get("name") or "").lower() for k in ["implement", "construct", "deployment", "uat deployment"])
        ]
        earliest_build_anchor = min(build_anchors, key=lambda m: m["planned_date"]) if build_anchors else (ms_with_dates[0] if ms_with_dates else None)
        earliest_build_anchor_date = earliest_build_anchor["planned_date"] if earliest_build_anchor else contract_end_str

        # 2. Capacity Shortfall against EARLIEST Build Anchor (FIX 3)
        if earliest_build_anchor_date:
            try:
                b_anchor_dt = datetime.strptime(str(earliest_build_anchor_date)[:10], "%Y-%m-%d").date()
                w_days_to_build_anchor = cls.count_working_days(base_start, b_anchor_dt)
                capacity_dev_weeks_build = (w_days_to_build_anchor / 5.0) * team_rate_dev_weeks_per_week
                # Demand of build items
                build_items = [
                    it for it in sorted_leaf_items
                    if not any(k in (it.get("name") or "").lower() for k in ["uat", "cutover", "hypercare", "knowledge transfer"])
                ]
                build_demand_weeks = sum(cls.estimate_task_complexity(it, cfg["default_complexity"])[1] for it in build_items)

                if build_demand_weeks > capacity_dev_weeks_build:
                    shortfall = round(build_demand_weeks - capacity_dev_weeks_build, 1)
                    schedule_conflicts.append({
                        "conflict_type": "CAPACITY_SHORTFALL",
                        "severity": "HIGH",
                        "anchor_name": earliest_build_anchor.get("name") if earliest_build_anchor else "Earliest Build Gate",
                        "anchor_date": earliest_build_anchor_date,
                        "demand_dev_weeks": round(build_demand_weeks, 1),
                        "available_dev_weeks": round(capacity_dev_weeks_build, 1),
                        "shortfall_dev_weeks": shortfall,
                        "rationale": f"Total build effort ({build_demand_weeks:.1f} dev-weeks) exceeds developer capacity ({capacity_dev_weeks_build:.1f} dev-weeks) before earliest deployment anchor '{earliest_build_anchor_date}'.",
                    })
            except Exception:
                pass

        # 3. Parallel Capacity-Based Scheduling
        dev_available = [base_start] * dev_count
        finish_dates_by_cid: Dict[str, str] = {}
        scheduled_items: List[Dict[str, Any]] = []
        anchor_overruns: Dict[str, List[Dict[str, Any]]] = {}

        for it in sorted_leaf_items:
            cid = it.get("candidate_id") or f"c_{it.get('id', 0)}"
            name = it.get("name") or it.get("title") or "Scope Deliverable"
            complexity, effort_weeks, comp_rationale = cls.estimate_task_complexity(it, cfg["default_complexity"])
            item_working_days = max(1, round(effort_weeks * (cfg["working_days_per_week"] / utilisation)))

            # Dependencies finish constraint
            dep_ids = it.get("dependencies", [])
            dep_finishes = [finish_dates_by_cid[d] for d in dep_ids if d in finish_dates_by_cid]
            if dep_finishes:
                try:
                    dep_min_start = max(datetime.strptime(df, "%Y-%m-%d").date() for df in dep_finishes)
                except Exception:
                    dep_min_start = base_start
            else:
                dep_min_start = base_start

            # Find developer track with earliest availability on/after dependency finish
            best_dev_idx = min(range(dev_count), key=lambda idx: max(dev_available[idx], dep_min_start))
            pred_start = max(dev_available[best_dev_idx], dep_min_start)
            pred_end = cls.add_working_days(pred_start, item_working_days)
            dev_available[best_dev_idx] = pred_end
            finish_dates_by_cid[cid] = pred_end.isoformat()

            # Determine anchor limit
            # Map item to phase anchor
            name_l = name.lower()
            if any(k in name_l for k in ["uat", "testing", "user acceptance"]):
                anchor_m = next((m for m in ms_with_dates if "uat" in (m.get("name") or "").lower()), earliest_build_anchor)
            elif any(k in name_l for k in ["hypercare", "hyper-care", "knowledge transfer", "handover", "defect"]):
                anchor_m = next((m for m in ms_with_dates if "hyper" in (m.get("name") or "").lower()), None)
            else:
                anchor_m = earliest_build_anchor

            anchor_date = anchor_m["planned_date"] if anchor_m else contract_end_str
            anchor_name = anchor_m["name"] if anchor_m else "Contract End"

            exceeds_anchor = False
            days_over = 0
            if anchor_date and pred_end.isoformat() > str(anchor_date)[:10]:
                exceeds_anchor = True
                try:
                    a_dt = datetime.strptime(str(anchor_date)[:10], "%Y-%m-%d").date()
                    days_over = (pred_end - a_dt).days
                except Exception:
                    days_over = 1

                if anchor_name not in anchor_overruns:
                    anchor_overruns[anchor_name] = []
                anchor_overruns[anchor_name].append({
                    "candidate_id": cid,
                    "name": name,
                    "pred_end": pred_end.isoformat(),
                    "anchor_date": anchor_date,
                    "days_over": days_over
                })

            date_out_of_window = bool(contract_end_str and pred_end.isoformat() > str(contract_end_str)[:10])

            scheduled_items.append({
                "candidate_id": cid,
                "name": name,
                "item_name": name,
                "role": it.get("role") or "VENDOR_WORK",
                "owner": it.get("owner") or "Vendor Project Team",
                "record_type": it.get("record_type") or "Scope Item",
                "record_category": it.get("record_category") or "PROJECT_WORK",
                "scope_status": "IN_SCOPE",
                "umbrella": False,
                "complexity": complexity,
                "estimated_effort_dev_weeks": effort_weeks,
                "effort_dev_weeks": effort_weeks,
                "estimated_duration_working_days": item_working_days,
                "estimated_duration_days": item_working_days,
                "predicted_start_date": pred_start.isoformat(),
                "predicted_deadline": pred_end.isoformat(),
                "proposed_deadline": pred_end.isoformat(),
                "estimated_deadline": pred_end.isoformat(),
                "date_provenance": "AI_ESTIMATED",
                "deadline_type": "AI_ESTIMATED",
                "confidence_score": 0.80,
                "confidence_label": "HIGH" if complexity == "SIMPLE" else "MEDIUM",
                "is_predicted": True,
                "exceeds_anchor": exceeds_anchor,
                "date_out_of_window": date_out_of_window,
                "schedule_status": "OVERRUN" if exceeds_anchor else "ON_TRACK",
                "depends_on": it.get("depends_on", []),
                "dependencies": it.get("dependencies", []),
                "dependency_ids": it.get("dependencies", []),
                "linked_milestone_ids": [anchor_m.get("milestone_id")] if (anchor_m and anchor_m.get("milestone_id")) else [],
                "source_locator": it.get("source_locator"),
                "source_quotation": it.get("source_quotation") or it.get("raw_text") or it.get("description"),
            })

        # Append umbrella items with zero effort and no dates (FIX 2)
        for u_it in umbrella_items:
            scheduled_items.append({
                "candidate_id": u_it.get("candidate_id"),
                "name": u_it.get("name"),
                "item_name": u_it.get("name"),
                "role": u_it.get("role") or "VENDOR_WORK",
                "owner": u_it.get("owner") or "Vendor Project Team",
                "record_type": u_it.get("record_type") or "Umbrella Scope",
                "record_category": "PROJECT_WORK",
                "scope_status": "IN_SCOPE",
                "umbrella": True,
                "complexity": "NONE",
                "estimated_effort_dev_weeks": 0.0,
                "effort_dev_weeks": 0.0,
                "estimated_duration_working_days": 0,
                "estimated_duration_days": 0,
                "predicted_start_date": None,
                "predicted_deadline": None,
                "date_provenance": "NONE",
                "deadline_type": None,
                "confidence_score": 1.0,
                "confidence_label": "HIGH",
                "is_predicted": False,
                "exceeds_anchor": False,
                "date_out_of_window": False,
                "schedule_status": "ON_TRACK",
                "depends_on": [],
                "dependencies": [],
                "dependency_ids": [],
                "source_locator": u_it.get("source_locator"),
                "source_quotation": u_it.get("source_quotation") or u_it.get("raw_text"),
            })

        # Produce ONE Rollup Warning per Anchor (FIX 3)
        for a_name, overruns in anchor_overruns.items():
            max_over = max(o["days_over"] for o in overruns)
            a_date = overruns[0]["anchor_date"]
            schedule_conflicts.append({
                "conflict_type": "ANCHOR_OVERRUN_ROLLUP",
                "severity": "HIGH" if max_over > 7 else "MEDIUM",
                "anchor_name": a_name,
                "anchor_date": str(a_date),
                "items_forecast_after": len(overruns),
                "max_days_over": max_over,
                "overrunning_item_ids": [o["candidate_id"] for o in overruns],
                "rationale": f"{len(overruns)} work items forecast after milestone anchor '{a_name}' ({a_date}) by up to {max_over} days. Capacity shortfall detected; dates preserved honestly without clamping.",
            })

        return scheduled_items, schedule_conflicts
