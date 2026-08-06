from services.dependency_graph_builder import DependencyGraphBuilder

class ValidationService:
    @classmethod
    def enrich_candidates(cls, candidates: list, scope_items: list = None) -> list:
        """
        Deterministic Validation & Enrichment Layer.
        1. Normalizes and validates candidates against the baseline scope.
        2. Builds the dependency graph (via DependencyGraphBuilder).
        3. Assigns meaningful semantic risk types.
        NEVER drops an entity.
        """
        if scope_items is None:
            scope_items = []
            
        # Optional: Normalize against scope items here if needed
        # (Assuming baseline matching already happened in extraction phase)
        
        # 1. Build Dependency Graph and detect root causes / cascade counts
        enriched = DependencyGraphBuilder.build_and_enrich(candidates)
        
        # 2. Assign Semantic Risk Types based on deterministic rules
        for cand in enriched:
            status = cand.get('status', 'OPEN')
            is_root_cause = cand.get('is_root_cause', False)
            blocked_work_count = cand.get('blocked_work_count', 0)
            blocked_by = cand.get('blocked_by', [])
            entity_type = cand.get('entity_type', 'ACTIVITY')
            dependency_source = cand.get('dependency_source', 'ENGINEERING')
            
            # Default fallback
            risk_cat = "GENERAL"
            
            if status in ['COMPLETED', 'RESOLVED']:
                risk_cat = "RESOLVED"
            elif entity_type in ['CHANGE_REQUEST', 'SCOPE_REQUEST', 'ISSUE']:
                risk_cat = entity_type
            elif entity_type == 'ACTION_ITEM':
                if blocked_work_count > 0:
                    risk_cat = "EXECUTION_BLOCKER"
                else:
                    risk_cat = "ACTION_ITEM"
            elif 'CHANGE_REQUEST' in cand.get('risk_cat', ''):
                risk_cat = "CHANGE_REQUEST"
            elif is_root_cause and blocked_work_count > 0:
                risk_cat = "ROOT_CAUSE_BLOCKER"
            elif len(blocked_by) > 0 and blocked_work_count > 0:
                # Blocks downstream but is also waiting for something upstream
                risk_cat = "WAITING_DEPENDENCY"
            elif len(blocked_by) > 0 and blocked_work_count == 0:
                # Is waiting for something, but doesn't block anything downstream
                risk_cat = "CUSTOMER_DEPENDENCY" if dependency_source == "CUSTOMER" else "INTERNAL_DEPENDENCY"
            elif blocked_work_count > 0 and not blocked_by:
                # Blocks downstream (is a root cause, but maybe milestone level)
                risk_cat = "EXECUTION_BLOCKER"
            elif status == "IN_PROGRESS" or (status == "OPEN" and blocked_work_count == 0):
                risk_cat = "IN_PROGRESS_RISK"
            else:
                if status == "BLOCKED":
                    if blocked_work_count > 0:
                        risk_cat = "EXECUTION_BLOCKER"
                    else:
                        risk_cat = "CUSTOMER_DEPENDENCY" if dependency_source == "CUSTOMER" else "INTERNAL_DEPENDENCY"
                else:
                    risk_cat = "IN_PROGRESS_RISK"
            
            # Preserve user-assigned category if it was already accurately set
            current_cat = cand.get('risk_cat')
            if current_cat in ["SCOPE_CREEP", "CHANGE_REQUEST", "SCOPE_REQUEST", "ACTION_ITEM", "ISSUE"]:
                risk_cat = current_cat
                
            cand['risk_cat'] = risk_cat
            cand['category'] = risk_cat
            
        return enriched
