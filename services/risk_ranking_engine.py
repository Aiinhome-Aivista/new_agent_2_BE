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
            cat = item.get("category", "GENERAL")

            # Use CATEGORY_TIER first; fall back to the DB priority table,
            # then to a safe default of 10.
            tier = cls.CATEGORY_TIER.get(
                cat,
                category_priorities.get(cat, 10)
            )

            exec_score  = item.get("execution_priority_score", 0)
            cascade     = item.get("cascade_count", 0)

            # Return (-score, tier, -cascade) so score is the primary driver
            return (-exec_score, tier, -cascade)

        eligible_items = [
            i for i in tracker_items
            if i.get("current_status") not in ["COMPLETED", "CANCELLED"]
        ]

        return sorted(eligible_items, key=sort_key)

