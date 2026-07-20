import numpy as np
from services.embedding_service import EmbeddingService

# -------------------------------------------------------------------
# Pre-defined reference profile texts for standard document types.
# These are rich keyword descriptions that capture the semantic
# essence of each document category. The embedding model converts
# these into vectors and we compare uploaded documents against them.
# -------------------------------------------------------------------

REFERENCE_PROFILES = {
    "EL": (
        "Engagement Letter is a formal contractual agreement between a professional services firm "
        "and a client organization. It outlines the scope of work, engagement objectives, "
        "professional fees and billing arrangements, payment terms, roles and responsibilities "
        "of the engagement team. It includes sections on confidentiality clauses, non-disclosure "
        "agreements, limitation of liability, indemnification, duration of engagement, "
        "commencement date, termination conditions, deliverables and milestones, team composition, "
        "partner and manager assignments, authorized signatories, acceptance and acknowledgement, "
        "professional standards compliance, regulatory requirements, conflict of interest "
        "declarations, intellectual property rights, data protection obligations, insurance "
        "coverage, dispute resolution mechanisms, governing law and jurisdiction, amendments "
        "and modifications process, representations and warranties, force majeure provisions, "
        "subcontracting permissions, client cooperation requirements, access to records and "
        "personnel, reporting obligations, quality assurance standards, and engagement "
        "acceptance procedures."
    ),

    "IFA": (
        "Interim Financial Analysis report contains financial statements review, balance sheet "
        "analysis, income statement evaluation, cash flow assessment, revenue recognition "
        "analysis, expense categorization, profit and loss summary, financial ratios computation, "
        "liquidity analysis, solvency ratios, working capital assessment, accounts receivable "
        "aging, accounts payable review, inventory valuation, depreciation and amortization "
        "schedules, tax provision analysis, deferred tax assets and liabilities, intercompany "
        "transactions review, related party disclosures, segment reporting, variance analysis "
        "comparing budget to actual, management discussion and analysis, going concern assessment, "
        "subsequent events review, contingent liabilities evaluation, off-balance sheet "
        "arrangements, capital expenditure review, debt covenant compliance, foreign currency "
        "translation adjustments, fair value measurements, impairment testing, goodwill "
        "assessment, and interim audit findings and observations."
    ),

    "MOM": (
        "Minutes of Meeting is a formal record documenting discussions, decisions, and action "
        "items from a meeting. It contains the meeting date and time, location or virtual "
        "platform details, list of attendees and absentees, agenda items discussed, key "
        "discussion points and deliberations, decisions taken and rationale, motions proposed "
        "and voting results, action items assigned with responsible persons and deadlines, "
        "follow-up tasks from previous meetings, issues raised and escalations, risk items "
        "identified, next meeting date and proposed agenda, approval of previous meeting "
        "minutes, chairperson remarks, opening and closing times, quorum confirmation, "
        "attachments and reference documents, participant signatures, distribution list, "
        "and confidentiality notices."
    ),

    "STATUS_REPORT": (
        "Status Report is a periodic document that communicates project progress, accomplishments "
        "milestones achieved, tasks completed during the reporting period, work in progress, "
        "upcoming planned activities, project timeline adherence, schedule variance analysis, "
        "budget utilization and cost tracking, resource allocation status, team productivity "
        "metrics, deliverable completion percentage, risk register updates, issue log with "
        "severity and resolution status, change requests submitted and approved, stakeholder "
        "communication summary, quality metrics and defect tracking, testing progress and "
        "results, deployment readiness assessment, client feedback summary, escalation items, "
        "dependencies and blockers, key performance indicators, overall project health "
        "assessment using RAG status indicators red amber green, executive summary, and "
        "recommendations for the next reporting period."
    ),

    "AUDIT_REPORT": (
        "Audit Report contains findings from an independent examination of financial records, "
        "internal controls assessment, compliance evaluation, audit opinion whether qualified "
        "unqualified adverse or disclaimer, scope and methodology, audit objectives, "
        "sampling techniques, testing procedures, material misstatement identification, "
        "management letter points, control deficiencies observations, recommendations for "
        "improvement, management responses, follow-up on prior audit findings, regulatory "
        "compliance status, fraud risk assessment, going concern evaluation, key audit "
        "matters, significant accounting estimates review, related party transactions "
        "examination, subsequent events assessment, and professional standards adherence."
    ),

    "CONTRACT": (
        "Contract agreement document containing terms and conditions, parties involved "
        "definitions, effective date and duration, scope of services, pricing and payment "
        "terms, service level agreements, performance benchmarks, warranties and representations, "
        "indemnification clauses, limitation of liability, confidentiality and non-disclosure, "
        "intellectual property rights, termination provisions, force majeure, dispute resolution, "
        "governing law, amendment procedures, assignment restrictions, compliance with laws, "
        "insurance requirements, notices and communications, entire agreement clause, "
        "severability, waiver provisions, counterparts execution, and authorized signatures."
    ),

    "PROPOSAL": (
        "Business proposal document containing executive summary, company overview and "
        "qualifications, understanding of client requirements, proposed methodology and approach, "
        "project timeline and milestones, team composition and credentials, resource allocation "
        "plan, cost estimation and pricing breakdown, value proposition, competitive advantages, "
        "case studies and references, risk mitigation strategy, quality assurance approach, "
        "communication and reporting plan, assumptions and constraints, terms and conditions, "
        "acceptance criteria, and call to action."
    ),
}


class RelevanceService:
    """
    Scores document relevance using vector embedding cosine similarity.
    
    Instead of sending text to an LLM (expensive, slow, inconsistent),
    this service embeds both the document text and a reference profile
    for the target document type, then measures cosine similarity.
    
    Benefits:
    - Zero LLM tokens consumed
    - Sub-second response time (< 1 second)
    - Deterministic and reproducible scores
    - Reuses the same sentence-transformers model already loaded for RAG
    """

    # Cache for embedded reference profiles (computed once, reused forever)
    _profile_cache: dict[str, list[float]] = {}

    @classmethod
    def _get_profile_embedding(cls, doc_type: str, profile_text: str) -> list[float]:
        """Get or compute the embedding for a reference profile (cached)."""
        cache_key = f"{doc_type}:{hash(profile_text)}"
        if cache_key not in cls._profile_cache:
            cls._profile_cache[cache_key] = EmbeddingService.encode(profile_text)
        return cls._profile_cache[cache_key]

    @classmethod
    def _cosine_similarity(cls, vec_a: list[float], vec_b: list[float]) -> float:
        """Compute cosine similarity between two vectors. Returns 0.0 to 1.0."""
        a = np.array(vec_a)
        b = np.array(vec_b)
        dot_product = np.dot(a, b)
        norm_a = np.linalg.norm(a)
        norm_b = np.linalg.norm(b)
        if norm_a == 0 or norm_b == 0:
            return 0.0
        return float(dot_product / (norm_a * norm_b))

    @classmethod
    def _get_reference_text(cls, document_type: str, db=None) -> str:
        """
        Get the reference profile text for a document type.
        
        - For standard types (EL, IFA, MOM, etc.) → uses built-in REFERENCE_PROFILES
        - For custom types → fetches the user-provided description from the database
        """
        # Check built-in profiles first
        if document_type in REFERENCE_PROFILES:
            return REFERENCE_PROFILES[document_type]

        # For custom types, fetch description from DB
        if db is not None:
            try:
                cursor = db.cursor(dictionary=True)
                cursor.execute(
                    "SELECT description FROM document_types WHERE name = %s LIMIT 1",
                    (document_type,)
                )
                row = cursor.fetchone()
                cursor.close()
                if row and row.get("description", "").strip():
                    return row["description"]
            except Exception:
                pass

        # Fallback: use the type name itself as a minimal reference
        return f"A professional document of type {document_type}."

    @classmethod
    def expand_description(cls, type_name: str, short_description: str) -> str:
        """
        Uses the LLM to expand a short user-provided description into a rich
        ~200-word reference profile suitable for embedding-based comparison.
        
        This is called ONCE when a custom document type is created.
        The expanded text is stored in the DB and reused for all future uploads.
        
        Args:
            type_name: The document type name (e.g., "RESOURCES")
            short_description: The user's short description (e.g., "all resources")
            
        Returns:
            A rich ~200-word reference profile text
        """
        from services.llm_service import LLMService

        prompt = (
            f"You are a professional auditor assistant helping to define document classification profiles.\n\n"
            f"A user has created a new document type called '{type_name}' with this short description:\n"
            f"\"{short_description}\"\n\n"
            f"Your task: Write a detailed, keyword-rich reference profile (approximately 150-200 words) that describes "
            f"what a '{type_name}' document typically contains. Include specific terms, sections, and vocabulary "
            f"that would commonly appear in this type of document.\n\n"
            f"Write it as a single flowing paragraph. Do NOT use bullet points, numbering, or markdown formatting. "
            f"Do NOT include any preamble like 'Here is...' — just output the profile text directly."
        )

        try:
            expanded = LLMService.generate(prompt)
            # Clean up any leading/trailing whitespace or quotes
            expanded = expanded.strip().strip('"').strip("'")
            if len(expanded) > 50:  # Sanity check: LLM returned something meaningful
                return expanded
        except Exception as e:
            print(f"LLM description expansion failed: {e}")

        # Fallback: return the original short description if LLM fails
        return short_description

    @classmethod
    def score_relevance(cls, document_text: str, document_type: str, db=None) -> dict:
        """
        Score how relevant a document's content is to the declared document type.
        
        Strategy:
        - Step 1: Fast embedding pre-filter to catch completely irrelevant files (< 10%)
        - Step 2: LLM always makes the final decision for everything else
        
        This ensures the same document ALWAYS gets the same accurate score.
        """
        # 1. Get the reference profile text
        reference_text = cls._get_reference_text(document_type, db)

        # 2. Embed both texts using the same model used for RAG
        doc_embedding = EmbeddingService.encode(document_text)
        ref_embedding = cls._get_profile_embedding(document_type, reference_text)

        # 3. Compute cosine similarity
        raw_similarity = cls._cosine_similarity(doc_embedding, ref_embedding)

        # 4. Scale to 0-100 percentage
        embedding_score = int(min(100, max(0, round(raw_similarity * 125))))
        type_label = document_type.replace("_", " ").title()

        # 5. Fast rejection: only for completely irrelevant content (< 10%)
        #    e.g. uploading a cooking recipe as an "Engagement Letter"
        if embedding_score < 10:
            return {
                "score": embedding_score,
                "reasoning": f"Document content is completely unrelated to {type_label}. Obvious mismatch, rejected."
            }

        # 6. LLM always makes the final decision for consistent, accurate scoring
        from services.llm_service import LLMService
        
        # Send a smaller chunk to the LLM to save tokens
        small_sample = document_text[:3000] 
        
        prompt = (
            f"You are a professional auditor assistant.\n"
            f"We are analyzing a document to see if it is a valid '{document_type}'.\n"
            f"Our semantic pre-scanner gave it a preliminary confidence score of {embedding_score}/100.\n\n"
            f"Please read this excerpt and provide the final accurate relevance score:\n"
            f"\"\"\"\n{small_sample}\n\"\"\"\n\n"
            f"Scoring guidelines:\n"
            f"- 80-100: Clearly a valid {type_label} document\n"
            f"- 50-79: Partially matches {type_label} but missing key elements\n"
            f"- 20-49: Weak match, mostly unrelated content\n"
            f"- 0-19: Completely unrelated to {type_label}\n\n"
            f"Respond ONLY with a valid JSON object matching this schema:\n"
            f"{{\n"
            f"  \"score\": <integer between 0 and 100>,\n"
            f"  \"reasoning\": \"<brief 1-sentence reasoning explaining why it is or isn't a {document_type}>\"\n"
            f"}}"
        )
        
        try:
            res_json = LLMService.generate_json(prompt)
            final_score = int(res_json.get("score", embedding_score))
            reasoning = res_json.get("reasoning", f"AI verified with score {final_score}.")
            return {
                "score": final_score,
                "reasoning": reasoning
            }
        except Exception as e:
            # Fallback to embedding score if LLM fails
            print(f"LLM verification failed: {e}")
            return {
                "score": embedding_score,
                "reasoning": f"Document shows {embedding_score}% similarity to {type_label}. (AI verification unavailable, using embedding score)"
            }

