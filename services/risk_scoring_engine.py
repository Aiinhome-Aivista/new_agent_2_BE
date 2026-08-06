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
    ) -> dict:
        """
        Calculates Execution Priority and Final Risk Score using dynamic PMO weights.
        """
        execution_reasons = []
        
        # 1. Dynamic Execution Priority Weights
        # -------------------------------------
        weight_root_cause = 40 if earliest_root_cause else 0
        if earliest_root_cause:
            execution_reasons.append("✓ Earliest Root Cause")
            
        weight_blocked_work = min(blocked_work_count * 5, 20)
        if blocked_work_count > 0:
            execution_reasons.append(f"✓ Blocked Work: {blocked_work_count} downstream tasks")
            
        weight_unlock = min(execution_unlock_count * 10, 20)
        if execution_unlock_count > 0:
            execution_reasons.append(f"✓ Immediate Unlock: Will unblock {execution_unlock_count} tasks immediately")
            
        weight_critical = 0
        if critical_chain:
            weight_critical = 15
            execution_reasons.append("✓ Critical Chain: Impacts terminal milestone")
        elif critical_path:
            weight_critical = 10
            execution_reasons.append("✓ Critical Path: Longest sequence of dependent tasks")

        weight_dep_source = 0
        if earliest_root_cause:
            if dependency_source in ["CUSTOMER", "VENDOR", "SECURITY"]:
                weight_dep_source = 15
                execution_reasons.append(f"✓ External Blocker ({dependency_source})")
            elif dependency_source == "PMO":
                weight_dep_source = 10
                execution_reasons.append(f"✓ Internal Blocker (PMO)")
            else:
                weight_dep_source = 5
                execution_reasons.append(f"✓ Engineering Blocker")

        # Independent Task vs Waiting vs Pure Downstream
        weight_base = 0
        if earliest_root_cause:
            pass # handled above
        elif len(blocked_by) == 0 and blocked_work_count == 0 and category != "CHANGE_REQUEST":
            weight_base = 30
            execution_reasons.append("✓ Independent Task")
        elif len(blocked_by) > 0 and blocked_work_count > 0:
            weight_base = 20
            execution_reasons.append("✓ Waiting Dependency")
        elif len(blocked_by) > 0 and blocked_work_count == 0:
            weight_base = 10
            execution_reasons.append("✓ Pure Downstream Consequence")
        elif category == "CHANGE_REQUEST":
            weight_base = 5
            execution_reasons.append("✓ Scope Change Request")

        execution_priority = weight_root_cause + weight_blocked_work + weight_unlock + weight_critical + weight_dep_source + weight_base
        execution_priority = min(execution_priority, 100)

        # 2. Schedule Urgency / Impact
        schedule_impact = 0
        days_to_use = days_to_next_milestone if days_to_next_milestone is not None else days_until_due
        
        if days_to_use is not None:
            if days_to_use <= 0:
                schedule_impact = 100
                msg = f"({next_milestone_name})" if next_milestone_name else ""
                execution_reasons.append(f"✓ Schedule: Blocks overdue milestone {msg}")
            elif days_to_use <= 7:
                schedule_impact = 80
                msg = f"({next_milestone_name})" if next_milestone_name else ""
                execution_reasons.append(f"✓ Schedule: Blocks upcoming milestone {msg}")
            elif days_to_use <= 14:
                schedule_impact = 60
            elif days_to_use <= 30:
                schedule_impact = 40
            else:
                schedule_impact = 20
            
        # 3. Business Impact
        b_score_ratio = 0.0
        if business_impact:
            impact_key = business_impact.upper()
            fallbacks = {"CRITICAL": 1.0, "HIGH": 0.8, "MEDIUM": 0.5, "LOW": 0.2}
            b_score_ratio = fallbacks.get(impact_key, 0.0)
            
        business_impact_score = b_score_ratio * 100
        
        confidence_score = confidence * 100 if confidence <= 1.0 else confidence
        evidence_score = 100 if status in ["BLOCKED", "DELAYED"] else 60
        
        # RISK SCORE ENGINE (60/15/10/10/5 weights)
        final_score = (
            (execution_priority * 0.60) +
            (schedule_impact * 0.15) +
            (business_impact_score * 0.10) +
            (evidence_score * 0.10) +
            (confidence_score * 0.05)
        )
        
        # Enforce ranking rules (Execution Blockers must surface at the top of severity too)
        if earliest_root_cause and dependency_source in ["CUSTOMER", "VENDOR"]:
            final_score = max(final_score, 90)
        elif earliest_root_cause:
            final_score = max(final_score, 80)
            
        final_score = min(round(final_score), 100)

        # Remove duplicate execution reasons
        unique_reasons = []
        for r in execution_reasons:
            if r not in unique_reasons:
                unique_reasons.append(r)

        return {
            "execution_priority_score": final_score,
            "score": final_score,
            "execution_priority": execution_priority,
            "cascade_priority": cascade_depth,
            "schedule_priority": schedule_impact,
            "score_breakdown": {},
            "earliest_root_cause": earliest_root_cause,
            "execution_reasons": unique_reasons
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
            lines.append(f"Total Unlocked Milestones: {len(unique_chain)}")
            
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

