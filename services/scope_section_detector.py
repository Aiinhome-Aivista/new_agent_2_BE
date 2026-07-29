import re
from typing import Dict, Any

class ScopeSectionDetector:
    """
    Phase 2: Semantic Section Classification
    Operates on the hierarchical DocumentTree. Classifies the semantic type of each section.
    """
    
    SECTION_PATTERNS = {
        "FUNCTIONAL_SCOPE": r"(?i)^(?:[0-9]+\.?\s*)?(?:Scope of (?:Work|Services|Engagement)|Project Scope|Engagement Scope|(?:Our |Vendor |)Scope|Services(?: to be Provided)?|In[- ]?Scope(?:\s+Items)?|Work(?:\s+Packages)?|(?:Key |Core )?Activities|Phases(?: of Work)?|Services Overview|Service Description|Service Scope)",
        "DELIVERABLES": r"(?i)^(?:[0-9]+\.?\s*)?(?:(?:Key |Project |Service |Expected |Schedule of )?Deliverables|Milestones(?:\s+(?:and|&)\s+Deliverables)?|Outputs|Work Products)",
        "MILESTONES": r"(?i)^(?:[0-9]+\.?\s*)?(?:Milestones|Timeline|Schedule|Project Timeline|Delivery Schedule)",
        "VENDOR_RESPONSIBILITIES": r"(?i)^(?:[0-9]+\.?\s*)?(?:(?:Our |Vendor |Firm |Consultant |Provider )?Responsibilities|Vendor(?:'s)? Obligations|(?:Our|Firm) Obligations|Roles\s+(?:and|&)\s+Responsibilities)",
        "CLIENT_DEPENDENCY": r"(?i)^(?:[0-9]+\.?\s*)?(?:(?:Client|Customer|Your|Company)(?:'s)?\s+Responsibilities|(?:Client|Customer|Your)\s+Obligations|Client Dependencies|Dependencies on Client)",
        "OUT_OF_SCOPE": r"(?i)^(?:[0-9]+\.?\s*)?(?:Out[\s-]+of[\s-]+Scope|Exclusions|Not[\s-]+Included|(?:Items?\s+)?(?:Not\s+In|Outside(?:\s+the)?)\s+Scope|Limitations|Excluded\s+(?:Services|Items|Activities)|Items\s+Out\s+of\s+Scope|Restrictions|What(?:'s| is) Not (?:Included|Covered))",
        "ASSUMPTIONS": r"(?i)^(?:[0-9]+\.?\s*)?(?:(?:Key |Project |Commercial |General )?Assumptions|Preconditions)",
        "TECH_STACK": r"(?i)^(?:[0-9]+\.?\s*)?(?:Technology Stack|Tech Stack|Tools|Platforms|Software Requirements|Technical Architecture)",
        "LEGAL": r"(?i)^(?:[0-9]+\.?\s*)?(?:Legal|Confidentiality|Governing Law|Limitation of Liability|Warranties|Indemnification|Terms and Conditions)",
        "COMMERCIAL": r"(?i)^(?:[0-9]+\.?\s*)?(?:Commercial Terms|Pricing|Fees|Payment Terms|Cost|Budget)",
        "STAKEHOLDERS": r"(?i)^(?:[0-9]+\.?\s*)?(?:Stakeholders|Project Team|Key Contacts|Organizations|Sponsors)",
        "ACTORS": r"(?i)^(?:[0-9]+\.?\s*)?(?:User Roles|Actors|Target Audience|Personas|End Users)"
    }

    @classmethod
    def classify_tree(cls, doc_tree: Dict[str, Any]) -> Dict[str, Any]:
        """Iterates over the Document Tree sections and attaches a semantic_type."""
        for section in doc_tree.get("sections", []):
            section_name = section.get("section_name", "")
            semantic_type = "UNKNOWN"
            
            # 1. Regex Pattern Matching
            for sem_type, pattern in cls.SECTION_PATTERNS.items():
                if re.search(pattern, section_name):
                    semantic_type = sem_type
                    break
            
            # 2. Relaxed Keyword Matching
            if semantic_type == "UNKNOWN":
                lower_name = section_name.lower()
                if any(k in lower_name for k in ["overview", "architecture", "background", "introduction", "purpose"]):
                    semantic_type = "INTRODUCTION"
                elif "deliverable" in lower_name:
                    semantic_type = "DELIVERABLES"
                elif "timeline" in lower_name or "schedule" in lower_name or "milestone" in lower_name:
                    semantic_type = "MILESTONES"
                elif "technology" in lower_name or "stack" in lower_name:
                    semantic_type = "TECH_STACK"
                elif "responsibility" in lower_name or "obligation" in lower_name:
                    if "client" in lower_name or "customer" in lower_name or "your" in lower_name:
                        semantic_type = "CLIENT_DEPENDENCY"
                    else:
                        semantic_type = "VENDOR_RESPONSIBILITIES"
                elif "stakeholder" in lower_name or "contact" in lower_name:
                    semantic_type = "STAKEHOLDERS"
                elif "role" in lower_name or "actor" in lower_name or "persona" in lower_name:
                    semantic_type = "ACTORS"
                elif any(k in lower_name for k in ["fee", "payment", "price", "commercial", "invoice", "cost"]):
                    semantic_type = "COMMERCIAL"
                elif any(k in lower_name for k in ["law", "liability", "warranty", "legal", "confidentiality", "intellectual property", "acceptance", "termination"]):
                    semantic_type = "LEGAL"

            # Removed fallback to FUNCTIONAL_SCOPE for UNKNOWN to prevent pollution.
            # UNKNOWN sections will remain UNKNOWN and be skipped/logged by SectionDispatcher.

            section["semantic_type"] = semantic_type
            
        return doc_tree
