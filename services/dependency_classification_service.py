class DependencyClassificationService:
    """
    Deterministically identifies the source of a dependency based on keyword heuristics.
    Used to enrich `DEPENDENCY` entity types with their source.
    """

    @classmethod
    def classify(cls, item_name: str, blocked_by: list, evidence: str = "",
                 entity_type: str = "DEPENDENCY") -> str:
        """
        Determines if a dependency is CUSTOMER, TECHNICAL, PROJECT, or EXTERNAL.

        IMPORTANT: CUSTOMER classification only applies when entity_type == "DEPENDENCY".
        Milestones that have API/VPN in their evidence text should NOT be classified as
        CUSTOMER — only explicit dependency items (API Credentials, VPN Access) should.
        """
        # Only the item name + its direct blockers (not the full evidence) are used for classification.
        # This prevents milestone evidence text (e.g. "requires API integration") from
        # incorrectly triggering CUSTOMER for non-dependency milestones.
        if entity_type == "DEPENDENCY":
            # For explicit dependency items, check name + blocked_by
            name_and_blockers = f"{item_name} {' '.join(blocked_by)}".upper()
            customer_keywords = ["API", "VPN", "CREDENTIAL", "ACCESS", "CUSTOMER", "CLIENT",
                                 "APPROVAL", "SIGN OFF", "SIGNOFF"]
            if any(keyword in name_and_blockers for keyword in customer_keywords):
                return "CUSTOMER"

            external_keywords = ["VENDOR", "THIRD PARTY", "3RD PARTY", "PARTNER", "EXTERNAL", "SANDBOX"]
            if any(keyword in name_and_blockers for keyword in external_keywords):
                return "EXTERNAL"

            technical_keywords = ["SERVER", "DATABASE", "DB", "ENVIRONMENT", "DEPLOYMENT",
                                  "SSL", "CERTIFICATE", "FIREWALL", "PORT", "INTEGRATION"]
            if any(keyword in name_and_blockers for keyword in technical_keywords):
                return "TECHNICAL"

        # For MILESTONE entities — only classify source if they have an explicit external blocker
        else:
            # A milestone has a CUSTOMER source only if one of its direct blockers
            # is explicitly a customer/external dependency by name
            customer_keywords = ["API CREDENTIAL", "VPN ACCESS", "API ACCESS", "CUSTOMER CREDENTIAL",
                                  "CLIENT CREDENTIAL", "SIGN OFF", "SIGNOFF", "APPROVAL"]
            blockers_text = " ".join(blocked_by).upper()
            if any(keyword in blockers_text for keyword in customer_keywords):
                return "CUSTOMER"

        return "PROJECT"

