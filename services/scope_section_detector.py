import re

class ScopeSectionDetector:
    """
    Deterministically identifies sections in document chunks.
    Avoids using LLMs for section detection to save tokens and ensure consistency.
    """
    
    # List of (section_name, compiled_pattern) tuples.
    # ORDER IS CRITICAL: checked top-to-bottom, first match wins.
    # "Out of Scope" MUST come before any pattern containing "Scope"
    # to prevent "3. Out of Scope" from matching "Scope of Work".
    # Generic: patterns cover all common EL/IFA/SOW/MoM heading variations.
    # No project-specific or industry-specific terms.
    SECTION_PATTERNS = [
        # ── OUT OF SCOPE — checked FIRST (contains word "Scope", must win) ──
        ('Out of Scope', re.compile(
            r'(?i)(?:^|\s|#|\*|\-|\d+[\.\)]?\s)'
            r'(?:'
            r'out[\s\-_]*of[\s\-_]*scope'
            r'|exclusions?'
            r'|not\s+in[\s\-_]*scope'
            r'|items?\s+not\s+(?:covered|included)'
            r'|services?\s+not\s+(?:covered|included|provided)'
            r'|beyond[\s\-_]*scope'
            r'|what\s+(?:is\s+)?not\s+included'
            r')',
            re.MULTILINE
        )),

        # ── IN SCOPE — checked AFTER Out of Scope ──
        ('In Scope', re.compile(
            r'(?i)(?:^|\s|#|\*|\-|\d+[\.\)]?\s)'
            r'(?:'
            r'in[\s\-_]*scope'
            r'|scope\s+of\s+(?:work|services?|engagement)'
            r'|project\s+scope'
            r'|engagement\s+scope'
            r'|what\s+(?:is\s+)?included'
            r'|included\s+services?'
            r'|inclusions?'
            r'|deliverables?(?!\s+not)'
            r'|services?\s+(?:to\s+be\s+)?(?:provided|delivered|included)'
            r'|work\s+to\s+be\s+(?:performed|done|delivered)'
            r'|scope\s+confirmed'
            r'|reaffirmed\s+scope'
            r')',
            re.MULTILINE
        )),

        # ── ASSUMPTIONS / CLIENT RESPONSIBILITIES ──
        ('Assumptions', re.compile(
            r'(?i)(?:^|\s|#|\*|\-|\d+[\.\)]?\s)'
            r'(?:'
            r'assumptions?'
            r'|client\s+responsibilities?'
            r'|customer\s+(?:responsibilities?|obligations?)'
            r'|our\s+responsibilities?'
            r'|prerequisites?'
            r'|dependencies?'
            r'|client\s+(?:will\s+)?provide'
            r'|(?:vendor|supplier)\s+responsibilities?'
            r')',
            re.MULTILINE
        )),

        # ── MILESTONES / TIMELINE ──
        ('Milestones', re.compile(
            r'(?i)(?:^|\s|#|\*|\-|\d+[\.\)]?\s)'
            r'(?:'
            r'milestones?'
            r'|project\s+milestones?'
            r'|(?:project\s+)?timeline'
            r'|(?:project\s+)?schedule'
            r'|phases?\s+(?:&|and)\s+(?:timeline|schedule|milestones?)'
            r'|delivery\s+schedule'
            r'|key\s+dates?'
            r')',
            re.MULTILINE
        )),

        # ── RECURRING COMMITMENTS ──
        ('Recurring Commitments', re.compile(
            r'(?i)(?:^|\s|#|\*|\-|\d+[\.\)]?\s)'
            r'(?:'
            r'recurring\s+(?:commitments?|deliverables?|services?|obligations?)'
            r'|ongoing\s+(?:commitments?|deliverables?|services?)'
            r'|continuous\s+(?:service|improvement|delivery)'
            r'|managed\s+services?\s+commitments?'
            r'|periodic\s+(?:commitments?|deliverables?)'
            r')',
            re.MULTILINE
        )),
    ]

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
                    for section_name, pattern in cls.SECTION_PATTERNS:
                        if pattern.search(clean_meta):
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
                    for section_name, pattern in cls.SECTION_PATTERNS:
                        if pattern.search(clean_line) or (clean_header and pattern.search(clean_header)):
                            matched_section = section_name
                            break

                    # Generic fallback: if no pattern matched and heading contains
                    # "out" before "scope", classify as Out of Scope.
                    # This covers edge cases like "3b. Out-of-Scope Clarifications"
                    if matched_section is None:
                        heading_lower = clean_line.lower()
                        out_idx = heading_lower.find('out')
                        scope_idx = heading_lower.find('scope')
                        if out_idx != -1 and scope_idx != -1 and out_idx < scope_idx:
                            matched_section = 'Out of Scope'
                        elif 'in scope' in heading_lower or 'in-scope' in heading_lower:
                            matched_section = 'In Scope'
                            
                    if matched_section and matched_section != current_section:
                        print(f'[SectionDetector] "{clean_line[:60]}" -> {matched_section} (was {current_section})')
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

