class RiskRankingEngine:

    # Business priority order — lower number = higher rank in the output.
    # Items within the same tier are ordered by execution_priority_score DESC.
    CATEGORY_TIER = {
        "ROOT_CAUSE":                  1,
        "CUSTOMER_DEPENDENCY":         2,
        "DIRECT_EXECUTION_BLOCKER":    4,
        "TRANSITIVE_EXECUTION_BLOCKER": 5,
        "EXECUTION_BLOCKER":           3,
        "TECHNICAL_DEPENDENCY":        6,
        "SCOPE_CREEP":                 7,
        "DELAY":                       8,
        "GENERAL":                     9,
        "OBSERVATION":                10,
    }

    @classmethod
    def rank_risks(cls, tracker_items: list, category_priorities: dict) -> list:
        """
        Graph-first, score-second ranking.

        Primary sort  : business category tier (ROOT_CAUSE first, GENERAL last)
        Secondary sort : execution_priority_score DESC within the same tier
        Tertiary sort : cascade_count DESC as a tiebreaker
        """

        def sort_key(item):
            # 1. Graph Topological Order (queue_order) - Lowest number first
            q_order = item.get("queue_order", 9999)
            
            # 2. Execution Priority (Highest first) - Tiebreaker
            exec_pri = item.get("execution_priority", 0)
            
            # 3. Cascade Priority (Highest first)
            casc_pri = item.get("cascade_priority", 0)
            
            # 4. Schedule Priority (Highest first)
            sched_pri = item.get("schedule_priority", 0)
            
            # 5. Risk Score (Highest first) - Legacy tiebreaker
            risk_score = item.get("execution_priority_score", 0)
            
            # Return tuple for sorting
            return (q_order, -exec_pri, -casc_pri, -sched_pri, -risk_score)

        eligible_items = [
            i for i in tracker_items
            if i.get("current_status") not in ["COMPLETED", "CANCELLED"]
        ]

        return sorted(eligible_items, key=sort_key)

