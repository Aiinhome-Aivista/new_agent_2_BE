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
        cascade_depth: int = 0,
        blocked_work_count: int = 0,
        execution_unlock_count: int = 0,
        critical_chain: bool = False,
        dependency_source: str = "ENGINEERING",
        days_overdue: int = 0,
        days_until_due: int = 999,
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
        parallel_stream: str = "Stream 1"
    ) -> dict:
        """
        Dual Metric Architecture: Calculates Execution Priority AND Risk Severity separately.
        """
        execution_reasons = []
        
        # 1. Effort Multiplier
        effort_mult = {"XS": 1.5, "S": 1.2, "M": 1.0, "L": 0.8, "XL": 0.5}.get(resolution_effort, 1.0)
        
        # 2. Execution Band Determination
        band = 7
        band_name = "Change Request"
        min_prio, max_prio = 0, 20

        is_waiting = len(blocked_by) > 0 and not earliest_root_cause
        
        if is_scope_creep or category == "CHANGE_REQUEST":
            band = 7
            band_name = "Change Request"
            min_prio, max_prio = 0, 20
        elif earliest_root_cause and (criticality_score >= 75.0 or execution_unlock_count >= 2 or blocked_work_count >= 3):
            band = 1
            band_name = "Immediate Root Cause"
            min_prio, max_prio = 90, 100
        elif earliest_root_cause and execution_unlock_count > 0:
            band = 2
            band_name = "Execution Blocker"
            min_prio, max_prio = 80, 89
        elif is_waiting and criticality_score >= 50.0:
            band = 3
            band_name = "Critical Path Waiting"
            min_prio, max_prio = 70, 79
        elif not is_waiting and len(blocked_by) == 0 and blocked_work_count == 0:
            band = 4
            band_name = "Independent Execution"
            min_prio, max_prio = 60, 69
        elif is_waiting and blocked_work_count > 0:
            band = 5
            band_name = "Waiting Dependency"
            min_prio, max_prio = 45, 59
        elif is_waiting and blocked_work_count == 0:
            band = 6
            band_name = "Downstream Consequence"
            min_prio, max_prio = 25, 44
        else:
            band = 4
            band_name = "Independent Risk"
            min_prio, max_prio = 60, 69

        # PMO Reasoning for UI
        if earliest_root_cause:
            execution_reasons.append("✓ Earliest Root Cause")
        if execution_unlock_count > 0:
            execution_reasons.append(f"✓ Unlocks {execution_unlock_count} immediate tasks")
        if cascade_depth > 0:
            execution_reasons.append(f"✓ Cascade Depth: {cascade_depth}")
        if criticality_score >= 75.0:
            execution_reasons.append("✓ On the Critical Path to Go-Live")
        if resolution_effort in ["XS", "S"]:
            execution_reasons.append("✓ Quick Resolution Effort")

        # 3. Execution Priority (Driven by unlocks, cascade depth, and effort)
        base_priority_points = (execution_unlock_count * 5) + (cascade_depth * 3) + (criticality_score * 0.2)
        adjusted_points = base_priority_points * effort_mult
        
        band_range = max_prio - min_prio
        scaled_addition = min(adjusted_points, band_range)
        
        execution_priority = min_prio + scaled_addition
        execution_priority = max(min(round(execution_priority), 100), 0)

        # 4. Risk Severity (Driven by Schedule Urgency, Business Criticality, and Owner)
        days_to_use = days_to_next_milestone if days_to_next_milestone is not None else days_until_due
        schedule_impact = 0
        if days_to_use is not None:
            if days_to_use <= 0: schedule_impact = 100
            elif days_to_use <= 7: schedule_impact = 80
            elif days_to_use <= 14: schedule_impact = 60
            elif days_to_use <= 30: schedule_impact = 40
            else: schedule_impact = 20
            
        b_score_ratio = {"Mission Critical": 1.0, "High": 0.8, "Medium": 0.5, "Low": 0.2}.get(business_criticality, 0.5)
        business_impact_score = b_score_ratio * 100
        
        owner_impact = 100 if dependency_owner in ["Customer", "Vendor"] else 50
        
        risk_severity = (schedule_impact * 0.40) + (business_impact_score * 0.40) + (owner_impact * 0.20)
        risk_severity = max(min(round(risk_severity), 100), 0)

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
            "score_breakdown": {
                "Execution Band": band_name,
                "Criticality Score": round(criticality_score, 1),
                "Resolution Effort": resolution_effort,
                "Business Criticality": business_criticality,
                "Dependency Owner": dependency_owner
            },
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
        execution_reasons: list = None
    ) -> str:
        """
        Formats the full evidence-backed reasoning string stored in tracker_items.reasoning
        """
        lines = []
        
        if status == 'RESOLVED' or category == 'RESOLVED':
            lines.append("Current Status")
            lines.append("• Resolved")
            lines.append("")
            lines.append("Evidence")
            lines.append(f'"{mom_evidence}"')
            return "\n".join(lines)
            
        # Status & Progress
        status_display = status.replace("_", " ").title() if status else "Unknown"
        lines.append(f"Current Status: {status_display} {f'({progress}%)' if progress is not None else ''}")
        lines.append("")
        
        # Execution Reasons (New Priority logic)
        if execution_reasons:
            lines.append("Execution Reason")
            for reason in execution_reasons:
                lines.append(reason)
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

        # Execution Chain
        lines.append("------------------------")
        lines.append("Execution Chain")
        lines.append("")
        
        # Only use longest_path which is strictly ordered by graph traversal
        raw_chain = longest_path if longest_path else []
        chain_items = []
        
        for item in raw_chain:
            if not item:
                continue
            name = None
            if isinstance(item, str):
                name = item
            elif isinstance(item, dict):
                name = item.get("name") or item.get("deliverable") or item.get("activity") or item.get("title")
            else:
                try:
                    name = str(item)
                except:
                    pass
            
            if name:
                chain_items.append(name)
                
        if not chain_items:
            lines.append("No downstream dependencies")
        else:
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
            immediate_count = len(immediate_unlocks) if immediate_unlocks else 0
            lines.append("Immediate Unlock")
            lines.append(f"{immediate_count}")
            lines.append("")
            lines.append("Cascade Impact")
            lines.append(f"{cascade_count}")
            
        # Score Breakdown
        if breakdown:
            lines.append("------------------------")
            lines.append("Score Breakdown")
            for k, v in breakdown.items():
                k_title = k.replace("_", " ").title()
                lines.append(f"{k_title}: {v}")
            lines.append("")
            
        if next_milestone_name:
            lines.append("------------------------")
            lines.append("Schedule Urgency")
            lines.append(f"Next milestone: {next_milestone_name}")
            if next_milestone_date:
                lines.append(f"Planned Start: {next_milestone_date}")
            if days_to_next_milestone is not None:
                lines.append(f"Starts in: {days_to_next_milestone} days")
            lines.append("")

        if original_contract_sentence:
            lines.append("------------------------")
            lines.append("Original Contract")
            lines.append(f'"{original_contract_sentence}"')
            lines.append("")
            
        if mom_evidence:
            lines.append("------------------------")
            lines.append("Evidence (MoM)")
            lines.append(f'"{mom_evidence}"')

        return "\n".join(lines).strip()

