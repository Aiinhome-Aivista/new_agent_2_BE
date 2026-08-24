# services/rag_guardrail_service.py
import re
from typing import Dict, Any, Optional, Tuple

class RAGGuardrailService:
    """
    Guardrail and Intent Service for RAG Chatbot.
    Enforces project domain boundaries, detects out-of-scope/general-knowledge queries,
    prevents hallucinations, and handles intent clarification.
    """

    OUT_OF_DOMAIN_PATTERNS = [
        r"\b(?:prime minister|president|capital of|who is the president|who is the prime minister)\b",
        r"\b(?:weather in|movie|song|actor|actress|cricket|football|fifa|olympics|sports score)\b",
        r"\b(?:tell me a joke|write a poem|write a song|recipe for|how to cook)\b",
        r"\b(?:who won the|history of the world|distance from earth to)\b",
        r"\b(?:stock price of|bitcoin price|crypto market|horoscope)\b"
    ]

    SAFE_REFUSAL_MESSAGE = (
        "I am the Project AI Assistant dedicated to this project. "
        "I do not have information on general knowledge topics outside this project's "
        "contractual scope, uploaded documents, deliverables, and risk tracking."
    )

    @classmethod
    def classify_and_guard(cls, query: str, project_name: str = "") -> Dict[str, Any]:
        """
        Evaluates user query against safety guardrails and project scope boundaries.
        Returns:
            {
                "is_in_domain": bool,
                "confidence": float,
                "safe_response": Optional[str],
                "needs_clarification": bool,
                "clarification_prompt": Optional[str]
            }
        """
        q_clean = query.strip()
        q_lower = q_clean.lower()

        # 1. Fast Regex Guardrail for Out-Of-Domain / General Knowledge
        for pattern in cls.OUT_OF_DOMAIN_PATTERNS:
            if re.search(pattern, q_lower):
                return {
                    "is_in_domain": False,
                    "confidence": 1.0,
                    "safe_response": cls.SAFE_REFUSAL_MESSAGE,
                    "needs_clarification": False,
                    "clarification_prompt": None
                }

        # 2. Check for empty or ultra-short ambiguous queries
        words = q_clean.split()
        if len(words) <= 1 and words[0].lower() in ["hi", "hello", "hey", "help"]:
            return {
                "is_in_domain": True,
                "confidence": 1.0,
                "safe_response": (
                    f"Hello! I am your Project AI Assistant for **{project_name or 'this project'}**. "
                    "I can help you review baseline scope deliverables, check current milestone statuses, "
                    "trace root cause risk blockers, and simulate unblock scenarios. How can I assist you today?"
                ),
                "needs_clarification": False,
                "clarification_prompt": None
            }

        # 3. Check for ultra-ambiguous single-word inputs
        if len(words) == 1 and len(words[0]) > 2:
            single_term = words[0]
            if single_term.lower() in ["status", "risk", "delay", "blocker", "milestone"]:
                return {
                    "is_in_domain": True,
                    "confidence": 0.6,
                    "safe_response": None,
                    "needs_clarification": True,
                    "clarification_prompt": (
                        f"Could you please specify which deliverable, milestone, or risk area you'd like the {single_term} for? "
                        "(e.g., *'What is the status of User Acceptance Testing?'* or *'Show active root cause blockers'*)"
                    )
                }

        return {
            "is_in_domain": True,
            "confidence": 0.95,
            "safe_response": None,
            "needs_clarification": False,
            "clarification_prompt": None
        }

    @classmethod
    def get_guardrail_system_instructions(cls) -> str:
        """
        Returns strict anti-hallucination and pinpoint response guidelines.
        """
        return """=== ENTERPRISE PMO GUARDRAILS & ANTI-HALLUCINATION RULES ===
1. DIRECT PINPOINT ANSWER FIRST: Always begin your answer immediately with a direct, concise 1-2 sentence pinpoint summary that directly answers the user's question without beating around the bush.
2. BRIEF SUPPORTING BREAKDOWN: Follow the direct answer with brief, structured, numbered points explaining the core reasons, blockers, or timeline dates only if needed for clarity. Keep supporting points concise and impactful (avoid fluff or long essays).
3. SOURCE TRUTH: Rely strictly on the four provided authoritative context blocks (MySQL Scope Items, Vector DB Document Excerpts, Live PM Execution Engine, and GraphRAG Lineage).
4. NEVER HALLUCINATE: Never invent milestones, deliverables, task owners, due dates, or dependencies that are not explicitly documented in the sources.
5. UNCERTAINTY HANDLING: If the requested information is not mentioned in the provided context, state clearly: "I have no information about this in the project documents or risk tracker."
6. CONFIDENCE & CLARIFICATION: If you are uncertain or the user's question references ambiguous items with multiple candidates, provide the closest factual context and ask a targeted clarifying question.
7. ROOT CAUSE TRANSPARENCY: When explaining why an item is in risk or not completed, trace the exact dependency lineage (e.g. Root Cause -> Blocker -> Activity).
8. PROFESSIONAL TONE: Provide concise, executive-ready PMO insights with direct references to document names, page numbers, and execution priority scores where available.
"""
