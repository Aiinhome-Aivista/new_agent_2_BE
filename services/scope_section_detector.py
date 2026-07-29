import re

class ScopeSectionDetector:
    """
    Deterministically identifies sections in document chunks.
    Avoids using LLMs for section detection to save tokens and ensure consistency.
    """
    
    SECTION_PATTERNS = {
        "Scope of Work": r"(?i)^(?:[#\*\-\s\d\.\:]+)?(?:Scope|Scope of Work|Project Scope|Engagement Scope|In Scope|Scope & Deliverables)",
        "Deliverables": r"(?i)^(?:[#\*\-\s\d\.\:]+)?(?:Deliverables|Key Deliverables|Project Deliverables|Outputs)",
        "Responsibilities": r"(?i)^(?:[#\*\-\s\d\.\:]+)?(?:Responsibilities|Our Responsibilities|Vendor Responsibilities)",
        "Client Responsibilities": r"(?i)^(?:[#\*\-\s\d\.\:]+)?(?:Client Responsibilities|Your Responsibilities)",
        "Out of Scope": r"(?i)^(?:[#\*\-\s\d\.\:]+)?(?:Out of Scope|Exclusions|Not Included|Out-of-Scope)",
        "Assumptions": r"(?i)^(?:[#\*\-\s\d\.\:]+)?(?:Assumptions|Key Assumptions|Project Assumptions|Commercial assumptions)",
        "Dependencies": r"(?i)^(?:[#\*\-\s\d\.\:]+)?(?:Dependencies|Project Dependencies)",
        "Milestones": r"(?i)^(?:[#\*\-\s\d\.\:]+)?(?:Milestones|Project Milestones.*|Timeline|Schedule)"
    }

    @classmethod
    def detect_sections(cls, chunks: list[dict]) -> list[dict]:
        """
        Iterates over document chunks and tags them with the currently active section.
        If a chunk contains multiple sections, it splits the chunk into multiple sub-chunks.
        """
        current_section = "General"
        new_chunks = []
        
        for chunk in chunks:
            text = chunk.get("text", "")
            metadata = chunk.get("metadata") or {}
            
            # Check metadata first if present
            meta_section = None
            for meta_val in metadata.values():
                if meta_val and isinstance(meta_val, str):
                    clean_meta = meta_val.strip()
                    for section_name, pattern in cls.SECTION_PATTERNS.items():
                        if re.search(pattern, clean_meta):
                            meta_section = section_name
                            break
                if meta_section:
                    break
            
            if meta_section:
                current_section = meta_section

            lines = text.split("\n")
            current_subchunk_lines = []
            
            for line in lines:
                clean_line = line.strip()
                
                # Check if this line is a section header
                if 0 < len(clean_line) < 120:
                    clean_header = re.sub(r'^[#\*\-\s\d\.\:]+', '', clean_line).strip()
                    matched_section = None
                    for section_name, pattern in cls.SECTION_PATTERNS.items():
                        if re.search(pattern, clean_line) or (clean_header and re.search(pattern, clean_header)):
                            matched_section = section_name
                            break
                            
                    if matched_section and matched_section != current_section:
                        # Save the previous subchunk if it has content
                        if current_subchunk_lines:
                            new_chunks.append({
                                "chunk_index": chunk.get("chunk_index"),
                                "page_number": chunk.get("page_number"),
                                "text": "\n".join(current_subchunk_lines),
                                "section": current_section
                            })
                            current_subchunk_lines = []
                        current_section = matched_section
                
                current_subchunk_lines.append(line)
                
            # Append the remainder of the chunk
            if current_subchunk_lines:
                new_chunks.append({
                    "chunk_index": chunk.get("chunk_index"),
                    "page_number": chunk.get("page_number"),
                    "text": "\n".join(current_subchunk_lines),
                    "section": current_section
                })
                
        return new_chunks

