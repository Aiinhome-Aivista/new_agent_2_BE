from typing import Dict, Any, List, Optional, Tuple
from abc import ABC, abstractmethod
import json
import re

# Minimum confidence to auto-resolve a tracker item.
# 0.75 chosen to allow short-name vs full-name matches while preventing false positives from unrelated items.
# Increase to 0.85 if false positives occur in production.
RESOLUTION_MATCH_THRESHOLD = 0.75

def _resolution_match(resolved_name: str, tracker_title: str) -> float:
    """
    Multi-tier fuzzy matching for resolution.
    Returns confidence 0.0 to 1.0.
    Uses the same tiers as Step 2B baseline matching to ensure consistency throughout the pipeline.

    Generic: works for any item names from any project.
    No hardcoded names or project-specific logic.
    """
    def _norm(s: str) -> str:
        # Strip punctuation, lowercase, collapse whitespace
        return re.sub(r'\s+', ' ', re.sub(r'[^\w\s]', '', str(s).lower().strip()))

    def _tokens(s: str) -> set:
        return set(_norm(s).split())

    r_norm = _norm(resolved_name)
    t_norm = _norm(tracker_title)

    if not r_norm or not t_norm:
        return 0.0

    # Tier 1: Exact normalized match → 1.0
    if r_norm == t_norm:
        return 1.0

    # Tier 2: One fully contains the other → 0.92
    # Handles "AI Knowledge Search" contained in "AI Knowledge Search RAG based search for enterprise"
    if r_norm in t_norm or t_norm in r_norm:
        return 0.92

    # Tier 3: Token overlap (Jaccard) → variable
    r_tokens = _tokens(resolved_name)
    t_tokens = _tokens(tracker_title)
    if r_tokens and t_tokens:
        intersection = r_tokens & t_tokens
        union = r_tokens | t_tokens
        jaccard = len(intersection) / len(union)
        # Require at least 0.40 overlap AND all resolved name tokens must be present in tracker title
        # (prevents false positives from short names)
        all_resolved_in_tracker = r_tokens.issubset(t_tokens)
        if all_resolved_in_tracker and jaccard >= 0.40:
            return 0.85
        elif jaccard >= 0.60:
            return 0.75

    return 0.0


class ResolutionStrategy(ABC):
    @abstractmethod
    def can_resolve(self, risk: Dict[str, Any], current_state: Dict[str, Any]) -> Tuple[bool, str, str]:
        """
        Returns (True, reason, resolution_type) if the risk should be resolved, else (False, "", "")
        """
        pass

class ExecutionBlockerResolver(ResolutionStrategy):
    def can_resolve(self, risk: Dict[str, Any], current_state: Dict[str, Any]) -> Tuple[bool, str, str]:
        derived_states = current_state.get("derived_states", {})
        m_id = risk.get("reference_id")
        
        if not m_id:
            return False, "", ""
            
        d_state = derived_states.get(m_id)
        if not d_state:
            return False, "", ""
            
        if d_state in ["COMPLETED", "READY"]:
            return True, f"Execution blocker cleared. Derived State: {d_state}", "EXECUTION_RECONCILIATION"
        return False, "", ""

class CustomerDependencyResolver(ResolutionStrategy):
    def can_resolve(self, risk: Dict[str, Any], current_state: Dict[str, Any]) -> Tuple[bool, str, str]:
        return False, "", ""

class TechnicalDependencyResolver(ResolutionStrategy):
    def can_resolve(self, risk: Dict[str, Any], current_state: Dict[str, Any]) -> Tuple[bool, str, str]:
        return False, "", ""

class GeneralRiskResolver(ResolutionStrategy):
    def can_resolve(self, risk: Dict[str, Any], current_state: Dict[str, Any]) -> Tuple[bool, str, str]:
        return False, "", ""

class RiskReconciliationEngine:
    _registry = {
        "EXECUTION": ExecutionBlockerResolver(),
        "EXECUTION_BLOCKER": ExecutionBlockerResolver(),
        "DIRECT_EXECUTION_BLOCKER": ExecutionBlockerResolver(),
        "TRANSITIVE_EXECUTION_BLOCKER": ExecutionBlockerResolver(),
        "CUSTOMER": CustomerDependencyResolver(),
        "CUSTOMER_DEPENDENCY": CustomerDependencyResolver(),
        "TECHNICAL": TechnicalDependencyResolver(),
        "TECHNICAL_DEPENDENCY": TechnicalDependencyResolver(),
    }
    _default_strategy = GeneralRiskResolver()

    @classmethod
    def get_strategy(cls, origin_type: str) -> ResolutionStrategy:
        return cls._registry.get(origin_type, cls._default_strategy)

    @classmethod
    def reconcile_open_risks(cls, open_risks: List[Dict[str, Any]], current_state: Dict[str, Any]) -> List[Tuple[Dict[str, Any], str, str]]:
        """
        Evaluates each OPEN risk to determine if its originating condition no longer exists.
        Returns a list of tuples: (risk, resolution_reason, resolution_type)
        """
        risks_to_resolve = []
        resolved_items = current_state.get("resolved_items", [])
        
        for risk in open_risks:
            t_title = risk.get("title", "")
            matched = False
            
            # 1. Multi-tier resolution matching against resolved_items
            for res in resolved_items:
                r_name = res.get("name", "")
                r_canonical = res.get("canonical_name", "")
                
                conf = 0.0
                if r_canonical and risk.get("canonical_title") and r_canonical.lower().strip() == str(risk.get("canonical_title", "")).lower().strip():
                    conf = 1.0
                else:
                    conf = _resolution_match(r_name, t_title)
                    if conf < RESOLUTION_MATCH_THRESHOLD and r_canonical:
                        conf = max(conf, _resolution_match(r_canonical, t_title))
                
                if conf >= RESOLUTION_MATCH_THRESHOLD:
                    evidence = res.get("resolution_evidence", "Condition resolved in status report")
                    print(f"  [Reconciliation] Matched '{r_name}' → '{t_title}' (confidence: {conf:.2f}) → RESOLVED")
                    risks_to_resolve.append((risk, f"Explicit item resolved. Evidence: {evidence}", "RESOLVED_MATCH"))
                    matched = True
                    break
                elif 0.50 <= conf < RESOLUTION_MATCH_THRESHOLD:
                    print(f"  [Reconciliation] Near-miss: '{r_name}' vs '{t_title}' (confidence: {conf:.2f}) — below threshold, not resolved")

            # 2. Strategy evaluation (e.g. execution blocker resolution via derived state)
            if not matched:
                origin_type = risk.get("origin_type") or risk.get("risk_category", "GENERAL")
                strategy = cls.get_strategy(origin_type)
                
                can_resolve, reason, res_type = strategy.can_resolve(risk, current_state)
                if can_resolve:
                    risks_to_resolve.append((risk, reason, res_type))
                    
        return risks_to_resolve
