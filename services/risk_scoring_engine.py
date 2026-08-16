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
            min_prio, max_prio = 0, 9
        elif earliest_root_cause and (criticality_score >= 50.0 or cascade_depth >= 3):
            band = 1
            band_name = "Critical Path Root Cause"
            min_prio, max_prio = 90, 100
        elif is_waiting and (criticality_score >= 50.0 or cascade_depth >= 3):
            band = 2
            band_name = "Critical Path Waiting"
            min_prio, max_prio = 80, 89
        elif earliest_root_cause and cascade_depth >= 2:
            band = 3
            band_name = "Major Independent Execution"
            min_prio, max_prio = 70, 79
        elif earliest_root_cause:
            band = 4
            band_name = "Minor Root Cause"
            min_prio, max_prio = 50, 69
        elif is_waiting and cascade_depth > 0:
            band = 5
            band_name = "Waiting Dependency"
            min_prio, max_prio = 30, 49
        elif is_waiting:
            band = 6
            band_name = "Downstream Consequence"
            min_prio, max_prio = 10, 29
        else:
            band = 4
            band_name = "Independent Risk"
            min_prio, max_prio = 40, 49

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

        # Enforce high risk severity for Scope Creep (unbilled work/revenue leakage)
        if is_scope_creep:
            risk_severity = max(risk_severity, 85)

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

