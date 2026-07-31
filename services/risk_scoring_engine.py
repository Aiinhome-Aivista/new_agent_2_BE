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
        impact_matrix: dict
    ) -> dict:
        """
        Step 1: Calculate parameter scores
        Step 2: Calculate execution score
        Step 3: Calculate risk score
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

        # EXECUTION_PRIORITY
        # Base urgency if blocked, delayed, or in progress
        is_urgent = status in ["BLOCKED", "DELAYED", "IN_PROGRESS", "NOT_STARTED"]
        # Normalize execution urgency to a 0.0-1.0 scale (1.0 for blocked/delayed)
        urgency_ratio = 0.0
        if status in ["BLOCKED", "DELAYED"]:
            urgency_ratio = 1.0
        elif status == "IN_PROGRESS":
            urgency_ratio = 0.5
        elif status == "NOT_STARTED":
            urgency_ratio = 0.3
            
        add_score("EXECUTION_PRIORITY", is_urgent, urgency_ratio)
        
        # CASCADE_IMPACT
        # Normalize cascade count (cap at 5 for max score ratio)
        cascade_ratio = min(cascade_count / 5.0, 1.0) if cascade_count > 0 else 0.0
        add_score("CASCADE_IMPACT", cascade_count > 0, cascade_ratio)
        
        # DATE_PROXIMITY
        proximity_ratio = 0.0
        if days_overdue is not None and days_overdue > 0:
            proximity_ratio = 1.0
        elif days_until_due is not None:
            if days_until_due <= 0:
                proximity_ratio = 1.0
            elif days_until_due <= 7:
                proximity_ratio = 0.8
            elif days_until_due <= 14:
                proximity_ratio = 0.5
            elif days_until_due <= 30:
                proximity_ratio = 0.2
        add_score("DATE_PROXIMITY", proximity_ratio > 0, proximity_ratio)
        
        # ROOT_CAUSE
        add_score("ROOT_CAUSE", is_root_cause)
        
        # CUSTOMER_DEPENDENCY
        # Naive check: if 'API' or 'VPN' or 'Customer' in blocked_by
        is_cust_dep = any("API" in b.upper() or "VPN" in b.upper() or "CUSTOMER" in b.upper() for b in blocked_by)
        add_score("CUSTOMER_DEPENDENCY", is_cust_dep)
        
        # TECHNICAL_DEPENDENCY
        # If blocked, but not by customer
        is_tech_dep = len(blocked_by) > 0 and not is_cust_dep
        add_score("TECHNICAL_DEPENDENCY", is_tech_dep)
        
        # SCOPE_CREEP
        add_score("SCOPE_CREEP", is_scope_creep)
        
        # BUSINESS_IMPACT
        impact_key = (business_impact or "LOW").upper()
        impact_add = impact_matrix.get(impact_key, 0)
        add_score("BUSINESS_IMPACT", impact_add > 0, impact_add)
        
        # CONFIDENCE
        add_score("CONFIDENCE", True, confidence)
        
        final_score = min(round(total_score), 100)
        
        return {
            "execution_priority_score": final_score,
            "score_breakdown": score_breakdown
        }

    @classmethod
    def format_reasoning(
        cls,
        score: int,
        severity: str,
        breakdown: dict,
        mom_evidence: str,
        llm_reasoning: str,
        original_contract_sentence: str = None
    ) -> str:
        """
        Formats the full evidence-backed reasoning string stored in tracker_items.reasoning.
        """
        lines = [
            f"Execution Priority Score: {score}  |  Severity: {severity}",
            "",
            "Score Breakdown:"
        ]
        if breakdown:
            for k, v in breakdown.items():
                lines.append(f"  - {k}: {v} pts")
        else:
            lines.append("  (No risk signals detected)")

        lines.append("")
        
        if original_contract_sentence:
            lines.append("Original Contract:")
            lines.append(original_contract_sentence)
            lines.append("")

        if llm_reasoning:
            lines.append("Reasoning:")
            lines.append(llm_reasoning)
            lines.append("")
            
        if mom_evidence:
            lines.append("Evidence (MoM):")
            lines.append(mom_evidence)

        return "\n".join(lines).strip()
