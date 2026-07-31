class DependencyClassificationService:
    """
    Deterministically identifies the source of a dependency based on keyword heuristics.
    Used to enrich `DEPENDENCY` entity types with their source.
    """

    @classmethod
    def classify(cls, item_name: str, blocked_by: list, evidence: str = "") -> str:
        """
        Determines if a dependency is CUSTOMER, TECHNICAL, PROJECT, or EXTERNAL.
        """
        text_to_analyze = f"{item_name} {' '.join(blocked_by)} {evidence}".upper()

        # Customer Dependency Heuristics
        customer_keywords = ["API", "VPN", "CREDENTIAL", "ACCESS", "CUSTOMER", "CLIENT", "APPROVAL", "SIGN OFF", "SIGNOFF"]
        if any(keyword in text_to_analyze for keyword in customer_keywords):
            return "CUSTOMER"

        # External Dependency Heuristics
        external_keywords = ["VENDOR", "THIRD PARTY", "3RD PARTY", "PARTNER", "EXTERNAL", "SANDBOX"]
        if any(keyword in text_to_analyze for keyword in external_keywords):
            return "EXTERNAL"

        # Technical Dependency Heuristics
        technical_keywords = ["SERVER", "DATABASE", "DB", "ENVIRONMENT", "DEPLOYMENT", "SSL", "CERTIFICATE", "FIREWALL", "PORT", "INTEGRATION"]
        if any(keyword in text_to_analyze for keyword in technical_keywords):
            return "TECHNICAL"

        # Default to Project / Execution dependency
        return "PROJECT"
