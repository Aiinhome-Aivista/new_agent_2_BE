"""
RiskScoringEngine
=================
Phase 4 of Execution Priority Pipeline.

Calculates Execution Priority and Risk Score based on configuration.
"""

class RiskScoringEngine:
    @classmethod
    def calculate(
        cls, 
        status: str,
        blocked_by: list,
        is_root_cause: bool,
        cascade_count: int,
        days_overdue: int,
        days_until_due: int,
        is_scope_creep: bool,
        confidence: float,
        business_impact: str,
        params: dict,
        impact_matrix: dict,
        item_name: str = None,
        category: str = "GENERAL",
        immediate_unlocks: list = None,
        future_unlocks: list = None,
        next_milestone_name: str = None,
        next_milestone_date: str = None,
        days_to_next_milestone: int = None,
    ) -> dict:
        """
        Step 1: Calculate parameter scores
        Step 2: Calculate execution score
        Step 3: Calculate risk score
        
        category: the pre-assigned business category (ROOT_CAUSE, CUSTOMER_DEPENDENCY, etc.)
                  Used to correctly assign bonus points without polluting downstream items.
        """
        score_breakdown = {}
        total_score = 0
        
        def add_score(param_code, condition, value=None):
            nonlocal total_score
            param = params.get(param_code, {})
            if not param.get("enabled", True):
                return
            
            weight = param.get("weight", 1.0)
            max_s = param.get("max_score", 0)
            eval_type = param.get("evaluation_type", "NUMERIC")
            
            if eval_type == "BOOLEAN" and condition:
                pts = max_s * weight
                score_breakdown[param_code] = round(pts)
                total_score += pts
            elif eval_type == "NUMERIC" and condition and value is not None:
                # Value is expected to be a ratio (0.0 to 1.0) of the max score
                pts = min(value * max_s, max_s) * weight
                score_breakdown[param_code] = round(pts)
                total_score += pts
            elif eval_type == "ENUM" and condition and value is not None:
                # Value is the direct addition
                pts = value * weight
                score_breakdown[param_code] = round(pts)
                total_score += pts

        # --- NEW PRIORITY ENGINE ---
        
        # 1. Execution Priority
        execution_priority = 0
        if category in ["ROOT_CAUSE_BLOCKER", "EXECUTION_BLOCKER"]:
            execution_priority = 100
        elif is_root_cause and cascade_count > 0:
            execution_priority = 100
        elif cascade_count > 0 and len(blocked_by) == 0:
            execution_priority = 100
        elif cascade_count > 0:
            execution_priority = 60
        elif is_root_cause:
            execution_priority = 90
            
        # 2. Cascade Priority
        cascade_priority = cascade_count
        
        # 3. Schedule Priority
        schedule_priority = 0
        days_to_use = None
        if days_until_due is not None and days_until_due < 9999:
            days_to_use = days_until_due
        elif days_to_next_milestone is not None:
            days_to_use = days_to_next_milestone
            
        if days_to_use is not None:
            if days_to_use <= 0:
                schedule_priority = 100
            elif days_to_use <= 7:
                schedule_priority = 90
            elif days_to_use <= 14:
                schedule_priority = 80
            elif days_to_use <= 30:
                schedule_priority = 60
            elif days_to_use <= 60:
                schedule_priority = 40
            else:
                schedule_priority = 20
        else:
            schedule_priority = 10

        # Legacy score breakdown calculation for backwards compatibility (can be phased out later)

        # EXECUTION_UNLOCK_IMPACT (30)
        # Ratio based on immediate downstream blocks. Unblocking 1 gives 50%, 2 gives 75%, >=3 gives 100%
        has_immediate = bool(immediate_unlocks)
        unlock_ratio = 0.0
        if has_immediate:
            num = len(immediate_unlocks)
            unlock_ratio = min(num / 3.0, 1.0)
            if num == 1: unlock_ratio = 0.5
            elif num == 2: unlock_ratio = 0.75
        add_score("EXECUTION_UNLOCK_IMPACT", has_immediate, unlock_ratio)

        # SCHEDULE_URGENCY (18)
        urgency_ratio = 0.0
        if days_to_next_milestone is not None:
            if days_to_next_milestone <= 0:
                urgency_ratio = 1.0
            elif days_to_next_milestone <= 7:
                urgency_ratio = 0.8
            elif days_to_next_milestone <= 14:
                urgency_ratio = 0.5
            elif days_to_next_milestone <= 30:
                urgency_ratio = 0.2
        add_score("SCHEDULE_URGENCY", days_to_next_milestone is not None, urgency_ratio)
        
        # ROOT_CAUSE (15)
        add_score("ROOT_CAUSE", is_root_cause)
        
        # CASCADE_DEPTH (10)
        # Count of future unlocks
        future_len = len(future_unlocks) if future_unlocks else 0
        cascade_ratio = min(future_len / 5.0, 1.0) if future_len > 0 else 0.0
        add_score("CASCADE_DEPTH", future_len > 0, cascade_ratio)
        
        # BUSINESS_IMPACT (20)
        # Business Impact = critical path weight + customer-facing milestone + number of downstream milestones + delivery phase
        b_score_ratio = 0.0
        
        # 1. Number of downstream milestones (Up to 40% of the impact)
        future_len = len(future_unlocks) if future_unlocks else 0
        if future_len > 0:
            b_score_ratio += min(future_len / 5.0, 1.0) * 0.4
            
        # 2. Critical path weight (Up to 30% of the impact)
        if is_root_cause:
            b_score_ratio += 0.3
            
        # 3. Customer-facing / Delivery phase (Up to 30% of the impact)
        name_cat = f"{item_name or ''} {category} {next_milestone_name or ''}".upper()
        if any(kw in name_cat for kw in ["CUSTOMER", "CLIENT", "UAT", "LIVE", "PROD", "DEPLOY", "GO-LIVE", "BUSINESS", "EXTERNAL"]):
            b_score_ratio += 0.3
            
        b_score_ratio = min(b_score_ratio, 1.0)
        
        # Fallback to older matrix if 0 and explicit impact provided
        if b_score_ratio == 0.0 and business_impact:
            impact_key = business_impact.upper()
            fallbacks = {"CRITICAL": 1.0, "HIGH": 0.8, "MEDIUM": 0.5, "LOW": 0.2}
            b_score_ratio = fallbacks.get(impact_key, 0.0)
            
        add_score("BUSINESS_IMPACT", b_score_ratio > 0, b_score_ratio)
        
        # DEPENDENCY (25)
        # Combines customer/technical dependency
        is_cust_dep = (category == "CUSTOMER_DEPENDENCY" or category == "DEPENDENCY")
        is_tech_dep = (len(blocked_by) > 0 and cascade_count > 0 and not is_cust_dep and category not in ("ROOT_CAUSE", "EXECUTION_BLOCKER"))
        
        dep_ratio = 0.0
        if is_cust_dep:
            dep_ratio = 1.0
        elif is_tech_dep:
            dep_ratio = 0.5
            
        add_score("DEPENDENCY", dep_ratio > 0, dep_ratio)
        
        # CONFIDENCE (5)
        add_score("CONFIDENCE", True, confidence)
        
        # OTHER (5)
        is_urgent_status = status in ["BLOCKED", "DELAYED"]
        add_score("OTHER", is_urgent_status, 1.0)

        
        final_score = min(round(total_score), 100)
        
        # Final result wrapper
        return {
            "execution_priority_score": final_score,
            "score": total_score,
            "execution_priority": execution_priority,
            "cascade_priority": cascade_priority,
            "schedule_priority": schedule_priority,
            "score_breakdown": score_breakdown,
            "is_root_cause": is_root_cause
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
        is_root_cause: bool,
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
    ) -> str:
        """
        Formats the full evidence-backed reasoning string stored in tracker_items.reasoning
        into an operational decision-support dashboard view.
        """
        lines = []
        
        if status == 'RESOLVED' or category == 'RESOLVED':
            lines.append("Current Status")
            lines.append("• Resolved")
            lines.append("")
            lines.append("Evidence")
            lines.append(f"\"{mom_evidence}\"")
            return "\n".join(lines)
            
        lines.append("Current Status")
        
        # Status & Progress
        status_display = status.replace("_", " ").title() if status else "Unknown"
        if progress is not None and str(progress).isdigit():
            lines.append(f"• {status_display} ({progress}%)")
        else:
            lines.append(f"• {status_display}")
            
        lines.append("")
        
        # Execution Unlock Impact
        lines.append("------------------------")
        lines.append("Execution Chain")
        lines.append("")
        
        raw_chain = longest_path if longest_path else (immediate_unlocks or []) + (future_unlocks or [])
        chain_items = []
        
        for item in (raw_chain or []):
            if not item:
                continue
            name = None
            if isinstance(item, str):
                name = item
            elif isinstance(item, dict):
                name = item.get("name") or item.get("deliverable") or item.get("activity") or item.get("title")
            elif hasattr(item, "name"):
                name = getattr(item, "name")
            elif hasattr(item, "deliverable"):
                name = getattr(item, "deliverable")
            else:
                try:
                    name = str(item)
                except Exception:
                    pass
            
            if name:
                chain_items.append(name)
                
        if not chain_items:
            lines.append("No downstream dependencies")
        else:
            # Remove duplicates while preserving order
            seen = set()
            unique_chain = []
            for item in chain_items:
                if item not in seen:
                    unique_chain.append(item)
                    seen.add(item)
            
            for i, item in enumerate(unique_chain):
                lines.append(f"{item}")
                if i < len(unique_chain) - 1:
                    lines.append("↓")
            
            lines.append("")
            lines.append(f"Total Unlocked Milestones: {len(unique_chain)}")
            
        lines.append("")
            
        # Schedule Urgency
        if next_milestone_name:
            lines.append("------------------------")
            lines.append("Schedule Urgency")
            lines.append("")
            lines.append("Next milestone")
            lines.append(f"{next_milestone_name}")
            lines.append("")
            if next_milestone_date:
                lines.append("Planned Start")
                lines.append(f"{next_milestone_date}")
                lines.append("")
            if days_to_next_milestone is not None:
                lines.append("Starts in")
                lines.append(f"{days_to_next_milestone} days")
                lines.append("")

        # Blocked By
        if blocked_by:
            lines.append("------------------------")
            if entity_type == "DEPENDENCY" or category == "CUSTOMER_DEPENDENCY":
                lines.append("Dependency / Waiting For")
            else:
                lines.append("Blocked By")
            for b in blocked_by:
                lines.append(f"• {b}")
            lines.append("")

        # Multi-Dimensional Priority Engine
        lines.append("------------------------")
        
        def render_stars(val):
            if val >= 90: return "★★★★★"
            elif val >= 70: return "★★★★☆"
            elif val >= 50: return "★★★☆☆"
            elif val >= 30: return "★★☆☆☆"
            elif val > 0: return "★☆☆☆☆"
            else: return "☆☆☆☆☆"
            
        lines.append("Execution Priority")
        lines.append(render_stars(execution_priority))
        if execution_priority >= 90:
            lines.append("Blocks the next scheduled activity")
        elif execution_priority > 0:
            lines.append("Blocks downstream work")
        else:
            lines.append("Does not block immediate work")
        lines.append("")
        
        lines.append("Cascade Impact")
        # For cascade priority, scale to stars (e.g. 1 = 20, 5+ = 100)
        cascade_scaled = min(cascade_priority * 20, 100)
        lines.append(render_stars(cascade_scaled))
        if cascade_priority > 0:
            lines.append(f"Blocks {cascade_priority} downstream dependency/dependencies")
        else:
            lines.append("No downstream cascade impact")
        lines.append("")
        
        lines.append("Schedule Urgency")
        lines.append(render_stars(schedule_priority))
        if schedule_priority >= 80:
            lines.append("Start date is imminent or overdue")
        elif schedule_priority >= 50:
            lines.append("Start date is approaching")
        elif schedule_priority > 10:
            lines.append("Sufficient float in schedule")
        else:
            lines.append("No schedule urgency")
        lines.append("")

        if original_contract_sentence:
            lines.append("------------------------")
            lines.append("Original Contract")
            lines.append(f"\"{original_contract_sentence}\"")
            lines.append("")
            
        if mom_evidence:
            lines.append("------------------------")
            lines.append("Evidence (MoM)")
            lines.append(f"\"{mom_evidence}\"")

        return "\n".join(lines).strip()
