class RiskRankingEngine:

    @classmethod
    def rank_risks(cls, tracker_items: list, category_priorities: dict = None) -> list:
        """
        Graph-first ranking.
        
        Sort by execution_priority_score (descending) from the band-based scoring engine.
        DO NOT overwrite execution_priority_score — the band system guarantees that:
          - ROOT_CAUSE always scores higher than INTERMEDIATE_BLOCKER
          - INTERMEDIATE_BLOCKER always scores higher than TERMINAL_ACTIVITY
          - SCOPE_CREEP always scores lowest (0-9)
        
        Overwriting with a linear spread would destroy the band invariant.
        """
        eligible_items = [
            i for i in tracker_items
            if i.get("current_status") not in ["COMPLETED", "CANCELLED"]
        ]
        
        # Sort by execution_priority_score (descending) — highest priority first
        # Secondary sort by risk_severity_score (descending) for tiebreaking within same band
        eligible_items.sort(
            key=lambda x: (
                -x.get("execution_priority_score", 0),
                -x.get("risk_severity_score", x.get("risk_score", 0))
            )
        )

        return eligible_items
