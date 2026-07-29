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
