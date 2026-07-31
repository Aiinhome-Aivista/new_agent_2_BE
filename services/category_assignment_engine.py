class CategoryAssignmentEngine:
    """
    Evaluates the project risk category dynamically based on the DB rule matrix.
    Rules define categories by entity_type, dependency_source, and status.
    """

    @classmethod
    def assign_category(cls,
                        rules: list,
                        entity_type: str,
                        status: str,
                        dependency_source: str = None,
                        is_root_cause: bool = False,
                        cascade_count: int = 0) -> str:
        
        # 1. Highest precedence logical overrides
        if entity_type == "MILESTONE":
            if is_root_cause:
                return "ROOT_CAUSE"
            elif cascade_count > 0:
                return "EXECUTION_BLOCKER"

        # 2. Matrix evaluation
        for rule in rules:
            match_entity = (rule["entity_type"] == entity_type)
            
            # None in the rule means "any" or "not applicable"
            match_source = True
            if rule["dependency_source"] is not None:
                match_source = (rule["dependency_source"] == dependency_source)
                
            match_status = True
            if rule["status"] is not None:
                match_status = (rule["status"] == status)

            if match_entity and match_source and match_status:
                return rule["result_category"]

        # Default fallback
        return "GENERAL"
