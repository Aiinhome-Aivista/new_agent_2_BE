class ProceduralMemory:
    @staticmethod
    def get_rules() -> str:
        return """
Procedural Rules for AI Agents:
1. STRICT SCOPE ADHERENCE: All new requests must be verified against the APPROVED baseline.
2. EVIDENCE REQUIRED: Any claim of 'Out of Scope' must cite exact text from the baseline or IFA.
3. ESCALATION PATH: High risk items (>0.8 risk score) must notify PROJECT_LEAD and ENGAGEMENT_MANAGER.
4. CONFIDENCE THRESHOLD: Do not flag risks if confidence is below 0.6. Log them for manual review instead.
"""
