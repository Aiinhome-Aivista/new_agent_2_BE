"""
RiskScoringEngine
=================
Phase 2: Deterministic weighted scoring.

The LLM (Phase 1) diagnoses WHAT risk exists and which signals are present.
This engine measures HOW SEVERE the risk is — no LLM involved.

Flow:
  Phase 1 (LLM)  → risk_category + signals dict + business_impact + confidence
  Phase 2 (this) → numeric score (0–100) + human-readable breakdown list

The doctor analogy:
  Phase 1 = Diagnose the disease ("Pneumonia")
  Phase 2 = Measure severity (temperature, O2, blood pressure → "Severe")

Weights come from risk_parameter_config table (via RiskConfigurationService).
Rules   come from business_rule_config table (via RiskConfigurationService).
Impact  comes from impact_matrix table (via RiskConfigurationService).

Changing a weight requires only:
    UPDATE risk_parameter_config SET weight = 25 WHERE parameter_code = 'TIMELINE';
No code deployment needed.
"""


class RiskScoringEngine:
    """
    Deterministic weighted scorer for risk items.
    All configuration is injected (not read from DB directly).
    """

    @classmethod
    def calculate(
        cls, 
        risk_category: str, 
        signals: dict, 
        confidence: float, 
        business_impact: str, 
        params: dict, 
        impact_matrix: dict, 
        rules: list, 
        dependent_count: int = 0,
        blocked_milestones: list = None,
        dependency_config: list = None
    ) -> tuple[int, list]:
        """
        Calculate the weighted risk score from diagnostic signals.

        Args:
            risk_category:   "SCOPE_CREEP" | "DELAY" | "DEPENDENCY" | "BLOCKED" | "NONE"
            signals:         Dict of boolean flags from Phase 1 LLM output:
                               deadline_missed, customer_dependency, technical_dependency,
                               progress_behind, milestone_slipping, missing_deliverable
            confidence:      Float 0.0–1.0 — LLM's confidence in its assessment
            business_impact: "LOW" | "MEDIUM" | "HIGH" — LLM's impact estimate
            params:          From RiskConfigurationService.get_parameters()
            impact_matrix:   From RiskConfigurationService.get_impact_matrix()
            rules:           From RiskConfigurationService.get_rules()
            dependent_count: How many other items are blocked by this item (0 = no impact)

        Returns:
            (score: int 0–100, breakdown: list[str])
        """
        score = 0
        breakdown = []

        # ── SCOPE_MATCH (30) ─────────────────────────────────────────────────
        # Full weight when not in baseline (scope creep).
        # Partial credit when delay/dependency on an in-scope item.
        if risk_category == "SCOPE_CREEP":
            param = params.get("SCOPE_MATCH", {})
            if param.get("enabled", True):
                w = param.get("weight", 30)
                score += w
                breakdown.append(f"✓ Not in approved baseline (+{w})")

        # ── TIMELINE (20) ────────────────────────────────────────────────────
        if signals.get("deadline_missed"):
            param = params.get("TIMELINE", {})
            if param.get("enabled", True) and rules.get("DEADLINE_MISSED_INCREASES_RISK", True):
                w = param.get("weight", 20)
                score += w
                breakdown.append(f"✓ Deadline exceeded (+{w})")

        # ── MILESTONE (10) ───────────────────────────────────────────────────
        if signals.get("milestone_slipping"):
            param = params.get("MILESTONE", {})
            if param.get("enabled", True):
                w = param.get("weight", 10)
                score += w
                breakdown.append(f"✓ Milestone slipping (+{w})")

        # ── CUSTOMER_DEPENDENCY (10) ─────────────────────────────────────────
        if signals.get("customer_dependency"):
            param = params.get("CUSTOMER_DEPENDENCY", {})
            if param.get("enabled", True) and rules.get("CUSTOMER_DEP_INCREASES_RISK", True):
                w = param.get("weight", 10)
                score += w
                breakdown.append(f"✓ Customer dependency pending (+{w})")

        # ── PROGRESS (10) ────────────────────────────────────────────────────
        if signals.get("progress_behind"):
            param = params.get("PROGRESS", {})
            if param.get("enabled", True):
                w = param.get("weight", 10)
                score += w
                breakdown.append(f"✓ Progress behind schedule (+{w})")

        # ── TECHNICAL_DEPENDENCY (5) ─────────────────────────────────────────
        if signals.get("technical_dependency"):
            param = params.get("TECHNICAL_DEPENDENCY", {})
            if param.get("enabled", True):
                w = param.get("weight", 5)
                score += w
                breakdown.append(f"✓ Technical dependency blocking (+{w})")

        # ── MISSING_DELIVERABLE (5) ──────────────────────────────────────────
        if signals.get("missing_deliverable"):
            param = params.get("MISSING_DELIVERABLE", {})
            if param.get("enabled", True) and rules.get("MISSING_DELIVERABLE_RISK", True):
                w = param.get("weight", 5)
                score += w
                breakdown.append(f"✓ Missing deliverable (+{w})")

        # ── CONFIDENCE (5) ───────────────────────────────────────────────────
        # confidence is float 0.0–1.0; scale into the weight bucket
        param = params.get("CONFIDENCE", {})
        if param.get("enabled", True):
            w = param.get("weight", 5)
            conf_score = round(confidence * w)
            if conf_score > 0:
                score += conf_score
                breakdown.append(f"✓ Evidence confidence ({int(confidence * 100)}%) (+{conf_score})")

        # ── BUSINESS_IMPACT (5) ──────────────────────────────────────────────
        # LLM classifies impact; impact_matrix maps that label to a score.
        param = params.get("BUSINESS_IMPACT", {})
        if param.get("enabled", True):
            impact_key = (business_impact or "LOW").upper()
            impact_add = impact_matrix.get(impact_key, 0)
            if impact_add > 0:
                score += impact_add
                breakdown.append(f"✓ Business impact {impact_key.title()} (+{impact_add})")

        # ── DEPENDENCY_IMPACT (Cascade Risk) ─────────────────────────────────
        if blocked_milestones is None: blocked_milestones = []
        if dependency_config is None: dependency_config = []
        dependent_count = len(blocked_milestones)
        
        if dependent_count > 0 and risk_category != "NONE":
            # Get points from config
            dep_score = 0
            for cfg in dependency_config:
                if dependent_count >= cfg['blocked_count_threshold']:
                    dep_score = cfg['risk_points']
                    break
                    
            if dep_score > 0:
                score += dep_score
                blocked_word = "milestone" if dependent_count == 1 else "milestones"
                breakdown.append(f"✓ Blocking {dependent_count} dependent {blocked_word}: {', '.join(blocked_milestones)} — cascade risk (+{dep_score})")

        final_score = min(score, 100)
        return final_score, breakdown

    @classmethod
    def format_reasoning(
        cls,
        score: int,
        severity: str,
        breakdown: list,
        mom_evidence: str,
        llm_reasoning: str,
        original_contract_sentence: str = None
    ) -> str:
        """
        Formats the full evidence-backed reasoning string stored in tracker_items.reasoning.
        """
        lines = [
            f"Risk Score: {score}  |  Severity: {severity}",
            "",
            "Contributing Factors:",
        ]
        if breakdown:
            lines.extend(breakdown)
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
            lines.append("Evidence:")
            lines.append(mom_evidence)

        return "\n".join(lines).strip()
