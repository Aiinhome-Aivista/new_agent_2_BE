class CategoryAssignmentEngine:
    """
    Evaluates the project risk category dynamically based on the DB rule matrix.

    Category hierarchy (highest → lowest priority):
        ROOT_CAUSE
        CUSTOMER_DEPENDENCY
        DIRECT_EXECUTION_BLOCKER     — immediately blocked by root cause / critical path
        TRANSITIVE_EXECUTION_BLOCKER — further downstream on critical path
        TECHNICAL_DEPENDENCY         — has downstream but not on critical path
        SCOPE_CREEP
        GENERAL
    """

    # A milestone is on the critical path ONLY if it blocks one of these specific
    # milestone types. Intentionally tight — "integration" alone is too broad
    # (matches "SAP Integration", "Document Upload Integration", etc.)
    CRITICAL_KEYWORDS = [
        "go-live", "go live", "golive",
        "production deployment", "production go",
        "deploy to production",
        "user acceptance testing", " uat",
        "system integration testing", " sit",
    ]

    # These milestones are always end-of-project or routine deliverables;
    # they should NEVER appear in the Risk Register unless the MoM explicitly
    # states they are blocked, delayed, or failing.
    TERMINAL_KEYWORDS = [
        "knowledge transfer", "closure", "handover",
        "documentation", "analytics dashboard",
        "warranty", "sign-off", "sign off",
        # Routine sub-deliverables that are project plan items, not risks
        "audit log", "activity tracking",
        "user management", "user administration",
        "document upload", "document indexing",
        "training", "go-live support",
    ]

    @classmethod
    def _is_on_critical_path(cls, names: list) -> bool:
        """Return True if any name in `names` matches a critical-path keyword."""
        for n in (names or []):
            nl = n.lower()
            if any(k in nl for k in cls.CRITICAL_KEYWORDS):
                return True
        return False

    @classmethod
    def _is_terminal(cls, name: str) -> bool:
        nl = (name or "").lower()
        return any(k in nl for k in cls.TERMINAL_KEYWORDS)

    @classmethod
    def assign_category(cls,
                        rules: list,
                        entity_type: str,
                        status: str,
                        dependency_source: str = None,
                        is_root_cause: bool = False,
                        cascade_count: int = 0,
                        downstream_names: list = None,
                        is_direct_blocker: bool = False,
                        item_name: str = "") -> str:
        """
        Parameters
        ----------
        downstream_names  : names of ALL transitive downstream milestones
        is_direct_blocker : True when this item is the *immediate* child of an
                            incomplete predecessor (parent status != COMPLETED)
        item_name         : canonical title of the item (used for terminal detection)
        """

        # 1. Terminal items are never execution blockers.
        if item_name and cls._is_terminal(item_name):
            return "GENERAL"

        # 2. Customer dependency always wins
        if dependency_source == "CUSTOMER":
            return "CUSTOMER_DEPENDENCY"

        # 3. Milestone-specific graph-first logic
        if entity_type in ["MILESTONE", "DEPENDENCY", "ACTIVITY"]:

            if is_root_cause:
                return "ROOT_CAUSE"

            if cascade_count > 0:
                critical_downstream = cls._is_on_critical_path(downstream_names or [])

                if not critical_downstream:
                    # Has downstream, but not on the critical path
                    return "TECHNICAL_DEPENDENCY"

                # Critical path → distinguish direct vs transitive
                if is_direct_blocker:
                    return "EXECUTION_BLOCKER"
                else:
                    return "EXECUTION_BLOCKER"

        # ── 3. Rule matrix (DB-driven) ────────────────────────────────────────
        for rule in rules:
            match_entity = (rule["entity_type"] == entity_type)

            match_source = True
            if rule["dependency_source"] is not None:
                match_source = (rule["dependency_source"] == dependency_source)

            match_status = True
            if rule["status"] is not None:
                match_status = (rule["status"] == status)

            if match_entity and match_source and match_status:
                return rule["result_category"]

        # ── 4. Default fallback ───────────────────────────────────────────────
        return "GENERAL"
