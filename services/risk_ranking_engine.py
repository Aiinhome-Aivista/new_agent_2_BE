class RiskRankingEngine:

    @classmethod
    def rank_risks(cls, tracker_items: list, category_priorities: dict = None) -> list:
        """
        Graph-first ranking.
        The UI priority score is derived directly from the Topological Execution Queue order.
        We do NOT sort by risk severity anymore. We sort strictly by queue_order.
        """
        eligible_items = [
            i for i in tracker_items
            if i.get("current_status") not in ["COMPLETED", "CANCELLED"]
        ]
        
        # Sort by queue_order (ascending)
        # queue_order 1 = highest execution priority
        eligible_items.sort(key=lambda x: x.get("queue_order", 9999))
        
        # Assign deterministic UI Execution Priority based on ordinal queue position
        total = len(eligible_items)
        for idx, item in enumerate(eligible_items):
            # E.g., if total=10, 1st gets 100, 2nd gets 90, 3rd gets 80, etc. (capped at 100, min 1)
            # This is exactly what the PM sees in the UI priority badge
            # A more nuanced formula could be used, but this maps queue strictly to a 1-100 score.
            # Example: 100 - ( (idx / max(1, total-1)) * 100 )
            # Let's map it from 100 down to 10
            if total <= 1:
                ui_score = 100
            else:
                ui_score = 100 - int((idx / (total - 1)) * 90)
                
            item["execution_priority_score"] = ui_score
            item["execution_priority"] = ui_score

        return eligible_items
