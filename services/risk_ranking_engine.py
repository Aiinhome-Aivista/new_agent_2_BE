class RiskRankingEngine:
    @classmethod
    def rank_risks(cls, tracker_items: list, category_priorities: dict) -> list:
        """
        Ranks a list of tracker items to determine the Highest Action Priority.
        
        Ordering criteria:
        1. Root Cause (True > False)
        2. Cascade Count (Descending)
        3. Due Date Proximity (Is Overdue > Days Until Due Descending)
        4. Execution Priority Score (Descending)
        5. Category Priority (1 = Highest, so Ascending order of priority_order)
        """
        
        def sort_key(item):
            # 1. Root Cause (True = 1, False = 0) - sort descending (True first)
            is_root_cause = 1 if item.get("is_root_cause", False) else 0
            
            # 2. Cascade Count - sort descending
            cascade_count = item.get("cascade_count", 0)
            
            # 3. Due Date Proximity
            # For simplicity in sorting: we want overdue things first.
            # So we create a unified date score. Higher is more urgent.
            date_score = 0
            days_overdue = item.get("days_overdue")
            days_until_due = item.get("days_until_due")
            if days_overdue is not None and days_overdue > 0:
                date_score = 10000 + days_overdue  # Highly urgent, more overdue = higher
            elif days_until_due is not None:
                # Less days until due = more urgent. Max days ~ 3650 (10 years)
                date_score = max(3650 - days_until_due, 0) 
            
            # 4. Execution Priority Score - sort descending
            exec_score = item.get("execution_priority_score", 0)
            
            # 5. Category Priority - sort ascending (1 is best). 
            # We invert it for descending sort: -1 > -5.
            cat_priority = category_priorities.get(item.get("category", "OBSERVATION"), 99)
            inv_cat_priority = -cat_priority
            
            # Return tuple for sorting
            return (
                is_root_cause,
                cascade_count,
                date_score,
                exec_score,
                inv_cat_priority
            )
            
        # Filter to incomplete execution blockers or risks
        eligible_items = [
            i for i in tracker_items 
            if i.get("status") not in ["COMPLETED"]
        ]
        
        ranked_items = sorted(eligible_items, key=sort_key, reverse=True)
        return ranked_items
