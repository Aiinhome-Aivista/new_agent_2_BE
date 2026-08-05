from typing import Dict, Any, List, Optional, Tuple
from abc import ABC, abstractmethod
import json

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
        title = (risk.get("title") or "").lower().strip()
        resolved_items = current_state.get("resolved_items", [])
        
        for res in resolved_items:
            res_name = (res.get("name") or "").lower().strip()
            if title and res_name and (title in res_name or res_name in title):
                evidence = res.get("resolution_evidence", "No evidence provided")
                return True, f"Explicit customer dependency resolved. Evidence: {evidence}", "CUSTOMER_DEPENDENCY_RECONCILIATION"
                
        return False, "", ""

class TechnicalDependencyResolver(ResolutionStrategy):
    def can_resolve(self, risk: Dict[str, Any], current_state: Dict[str, Any]) -> Tuple[bool, str, str]:
        title = (risk.get("title") or "").lower().strip()
        resolved_items = current_state.get("resolved_items", [])
        
        for res in resolved_items:
            res_name = (res.get("name") or "").lower().strip()
            if title and res_name and (title in res_name or res_name in title):
                evidence = res.get("resolution_evidence", "No evidence provided")
                return True, f"Explicit technical dependency resolved. Evidence: {evidence}", "TECHNICAL_DEPENDENCY_RECONCILIATION"
                
        return False, "", ""

class GeneralRiskResolver(ResolutionStrategy):
    def can_resolve(self, risk: Dict[str, Any], current_state: Dict[str, Any]) -> Tuple[bool, str, str]:
        return False, "", ""

class RiskReconciliationEngine:
    _registry = {
        "EXECUTION": ExecutionBlockerResolver(),
        "EXECUTION_BLOCKER": ExecutionBlockerResolver(),
        "DIRECT_EXECUTION_BLOCKER": ExecutionBlockerResolver(),
        "EXECUTION_BLOCKER": ExecutionBlockerResolver(),
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
        
        for risk in open_risks:
            origin_type = risk.get("origin_type")
            if not origin_type:
                risk_cat = risk.get("risk_category", "GENERAL")
                origin_type = risk_cat
                
            strategy = cls.get_strategy(origin_type)
            
            can_resolve, reason, res_type = strategy.can_resolve(risk, current_state)
            if can_resolve:
                risks_to_resolve.append((risk, reason, res_type))
                
        return risks_to_resolve
