import re

class ScopeSectionDetector:
    """
    Deterministically identifies sections in document chunks.
    Avoids using LLMs for section detection to save tokens and ensure consistency.
    """
    
    SECTION_PATTERNS = {
        "Scope of Work": r"(?i)^(?:[0-9]+\.?\s*)?(?:Scope of Work|Project Scope|Engagement Scope)",
        "Deliverables": r"(?i)^(?:[0-9]+\.?\s*)?(?:Deliverables|Key Deliverables|Project Deliverables)",
        "Responsibilities": r"(?i)^(?:[0-9]+\.?\s*)?(?:Responsibilities|Our Responsibilities|Vendor Responsibilities)",
        "Client Responsibilities": r"(?i)^(?:[0-9]+\.?\s*)?(?:Client Responsibilities|Your Responsibilities)",
        "Out of Scope": r"(?i)^(?:[0-9]+\.?\s*)?(?:Out of Scope|Exclusions|Not Included)",
        "Assumptions": r"(?i)^(?:[0-9]+\.?\s*)?(?:Assumptions|Key Assumptions|Project Assumptions)",
        "Dependencies": r"(?i)^(?:[0-9]+\.?\s*)?(?:Dependencies|Project Dependencies)"
    }

    @classmethod
    def detect_sections(cls, chunks: list[dict]) -> list[dict]:
        """
        Iterates over document chunks and tags them with the currently active section.
        """
        current_section = "General"
        
        for chunk in chunks:
            text = chunk.get("text", "")
            
            # Look for headers in the first few lines of the chunk
            lines = [line.strip() for line in text.split("\n") if line.strip()]
            
            for line in lines[:3]: # Only check first 3 lines for headers
                if len(line) > 100:
                    continue # Too long to be a header
                    
                matched_section = None
                for section_name, pattern in cls.SECTION_PATTERNS.items():
                    if re.search(pattern, line):
                        matched_section = section_name
                        break
                        
                if matched_section:
                    current_section = matched_section
                    break # Found the header for this chunk
                    
            chunk["section"] = current_section
            
        return chunks
