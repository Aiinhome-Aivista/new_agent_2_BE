import re
from typing import List, Dict, Any, Tuple, Optional


class ScopeSectionDetector:
    """
    Deterministically identifies structural sections in document chunks.
    Maintains the active section based on actual structural headings
    rather than ordinary body-text keywords.
    """

    # All 17 intended contractual target sections (Taxonomy)
    DEFINED_SECTIONS = [
        "Scope of Work",
        "Deliverables",
        "Responsibilities",
        "Client Responsibilities",
        "Out of Scope",
        "Assumptions",
        "Dependencies",
        "Milestones",
        "Recurring Commitments",
        "General",
        "Functional Scope",
        "Technical Scope",
        "Methodology",
        "Governance",
        "Support",
        "Acceptance",
        "Resources",
    ]

    # Ordered list of (section_name, compiled_pattern) tuples.
    # Order is critical: more specific and negative sections are matched before broad ones.
    SECTION_PATTERNS = [
        # 1. OUT OF SCOPE — checked FIRST (contains word "Scope", must win)
        (
            "Out of Scope",
            re.compile(
                r"(?i)(?:^|[\s#\*\-])"
                r"(?:"
                r"out[\s\-_]*of[\s\-_]*scope"
                r"|exclusions?"
                r"|not\s+in[\s\-_]*scope"
                r"|items?\s+not\s+(?:covered|included)"
                r"|services?\s+not\s+(?:covered|included|provided)"
                r"|beyond[\s\-_]*scope"
                r"|what\s+(?:is\s+)?not\s+included"
                r")",
                re.MULTILINE,
            ),
        ),
        # 2. CLIENT RESPONSIBILITIES — checked before generic Responsibilities
        (
            "Client Responsibilities",
            re.compile(
                r"(?i)(?:^|[\s#\*\-])"
                r"(?:"
                r"client\s+(?:responsibilit(?:y|ies)|obligations?|prerequisites?)"
                r"|customer\s+(?:responsibilit(?:y|ies)|obligations?)"
                r"|responsibilit(?:y|ies):\s*(?:client|customer|your|[a-z0-9_\-]+)"
                r"|(?:client|customer|your|[a-z0-9_\-]+)\s+responsibilit(?:y|ies)"
                r"|client\s+(?:inputs?|dependencies|will\s+provide)"
                r"|client\s+obligations"
                r")",
                re.MULTILINE,
            ),
        ),
        # 3. RESPONSIBILITIES / ROLES
        (
            "Responsibilities",
            re.compile(
                r"(?i)(?:^|[\s#\*\-])"
                r"(?:"
                r"responsibilit(?:y|ies)\s+matrix"
                r"|roles?\s+(?:&|and)\s+responsibilit(?:y|ies)"
                r"|project\s+roles?"
                r"|team\s+responsibilit(?:y|ies)"
                r"|our\s+responsibilit(?:y|ies)"
                r"|vendor\s+responsibilit(?:y|ies)"
                r"|description\s+of\s+responsibilities"
                r"|responsibilit(?:y|ies):?\s*(?:our|vendor|service\s+provider|consultant|[a-z0-9_\s]+)"
                r"|responsibilit(?:y|ies)\b"
                r"|appendix\s*[\-\–\—\s\ufffd]*1\b"
                r")",
                re.MULTILINE,
            ),
        ),
        # 4. DEPENDENCIES
        (
            "Dependencies",
            re.compile(
                r"(?i)(?:^|[\s#\*\-])"
                r"(?:"
                r"dependencies"
                r"|project\s+dependencies"
                r"|external\s+dependencies"
                r"|technical\s+dependencies"
                r"|assumptions?\s+(?:&|and)\s+dependencies"
                r")",
                re.MULTILINE,
            ),
        ),
        # 5. ASSUMPTIONS
        (
            "Assumptions",
            re.compile(
                r"(?i)(?:^|[\s#\*\-])"
                r"(?:"
                r"assumptions?"
                r"|key\s+assumptions?"
                r"|key\s+assgptions?"
                r"|assgptions?\s+(?:&|and)\s+dependencies"
                r"|assumptions?\s+(?:&|and)\s+dependencies"
                r"|general\s+assumptions?"
                r"|engagement\s+assumptions?"
                r")",
                re.MULTILINE,
            ),
        ),
        # 6. DELIVERABLES
        (
            "Deliverables",
            re.compile(
                r"(?i)(?:^|[\s#\*\-|])"
                r"(?:"
                r"list\s+of\s+deliverables(?:\s+with\s+milestones?)?"
                r"|project\s+deliverables?"
                r"|key\s+deliverables?"
                r"|expected\s+deliverables?"
                r"|documentation\s+deliverables?"
                r"|deliverables?"
                r")",
                re.MULTILINE,
            ),
        ),
        # 7. MILESTONES / TIMELINE / FEES
        (
            "Milestones",
            re.compile(
                r"(?i)(?:^|[\s#\*\-])"
                r"(?:"
                r"milestones?(?:\s+table|\s+schedule|\s+list)?"
                r"|project\s+overall\s+timeline"
                r"|project\s+timeline"
                r"|project\s+phases?"
                r"|delivery\s+milestones?"
                r"|billing\s+milestones?"
                r"|invoices?\s+for\s+fees\s+shall\s+be\s+raised"
                r"|fees?,?\s+expenses?,?\s+(?:and|&)\s+billing\s+milestones?"
                r"|payment\s+(?:milestones?|terms?|schedule)"
                r")",
                re.MULTILINE,
            ),
        ),
        # 8. RECURRING COMMITMENTS
        (
            "Recurring Commitments",
            re.compile(
                r"(?i)(?:^|[\s#\*\-])"
                r"(?:"
                r"recurring\s+(?:commitments?|deliverables?|services?|obligations?)"
                r"|ongoing\s+(?:commitments?|deliverables?|services?)"
                r"|continuous\s+(?:service|improvement|delivery)"
                r"|managed\s+services?\s+commitments?"
                r"|periodic\s+(?:commitments?|deliverables?)"
                r")",
                re.MULTILINE,
            ),
        ),
        # 9. TECHNICAL SCOPE — distinct category from Functional Scope
        (
            "Technical Scope",
            re.compile(
                r"(?i)(?:^|[\s#\*\-])"
                r"(?:"
                r"technical\s+scope"
                r"|system\s+scope"
                r"|architecture(?:\s+scope)?"
                r"|infrastructure(?:\s+scope)?"
                r"|technology\s+(?:stack|landscape|scope)"
                r")",
                re.MULTILINE,
            ),
        ),
        # 10. FUNCTIONAL SCOPE / MODULE VIEWS
        (
            "Functional Scope",
            re.compile(
                r"(?i)(?:^|[\s#\*\-])"
                r"(?:"
                r"functional\s+scope"
                r"|employee\s+collaboration(?:\s+portal)?"
                r"|internal\s+job\s+posting"
                r"|admin(?:istrator)?\s+view"
                r"|user\s+(?:or|and)\s+employee\s+view"
                r"|manager\s+view"
                r"|application\s+features?"
                r"|portal\s+features?"
                r")",
                re.MULTILINE,
            ),
        ),
        # 11. SCOPE OF WORK (and In Scope)
        (
            "Scope of Work",
            re.compile(
                r"(?i)(?:^|[\s#\*\-])"
                r"(?:"
                r"scope\s+of\s+(?:our\s+)?services?"
                r"|scope\s+of\s+work"
                r"|in[\s\-_]*scope"
                r"|project\s+scope"
                r"|engagement\s+scope"
                r"|scope\s+of\s+engagement"
                r"|organi[sz]ational\s+scope"
                r"|geographical\s+scope|graphical\s+scope"
                r")",
                re.MULTILINE,
            ),
        ),
        # 12. METHODOLOGY / APPROACH
        (
            "Methodology",
            re.compile(
                r"(?i)(?:^|[\s#\*\-])"
                r"(?:"
                r"project\s+approach\s+(?:and|&)\s+methodology"
                r"|approach\s+(?:and|&)\s+methodology"
                r"|project\s+methodology"
                r"|implementation\s+(?:approach|methodology)"
                r"|delivery\s+(?:approach|methodology)"
                r"|transform\s+methodology"
                r"|project\s+phases?"
                r"|assess(?:\s+phase)?"
                r"|design(?:\s+phase)?"
                r"|construct(?:\s+phase)?"
                r"|implementation\s+(?:&|and)\s+go[\s\-_]*live"
                r"|operate\s+(?:&|and)\s+review"
                r")",
                re.MULTILINE,
            ),
        ),
        # 13. GOVERNANCE / REPORTING
        (
            "Governance",
            re.compile(
                r"(?i)(?:^|[\s#\*\-])"
                r"(?:"
                r"progress\s+reporting\s+(?:and|&)\s+issue\s+resolution"
                r"|project\s+status\s+report"
                r"|escalation\s+matrix"
                r"|level\s+of\s+escalation"
                r"|project\s+governance"
                r"|steering\s+committee"
                r"|governance\s+structure"
                r"|project\s+organisation\s+structure"
                r"|reporting\s+cadence"
                r"|(?:your|our|client|vendor|[a-z0-9_\-]+)?\s*project\s+manager"
                r"|(?:your|our|client|vendor|[a-z0-9_\-]+)?\s*project\s+director"
                r"|team\s+leaders?"
                r"|on\s+[a-z0-9\s'\.\-_]+\s+side:?"
                r")",
                re.MULTILINE,
            ),
        ),
        # 14. RESOURCES / TEAM / EXPENSES
        (
            "Resources",
            re.compile(
                r"(?i)(?:^|[\s#\*\-])"
                r"(?:"
                r"resource\s+planning(?:\s+for\s+development\s+team)?"
                r"|staffing\s+plan"
                r"|team\s+structure"
                r"|project\s+team\s+structure"
                r"|reimbursement\s+of\s+out[\s\-]of[\s\-]pocket\s+expenses"
                r"|travel\s+(?:and|&)\s+living\s+expenses"
                r")",
                re.MULTILINE,
            ),
        ),
        # 15. SUPPORT / HYPERCARE
        (
            "Support",
            re.compile(
                r"(?i)(?:^|[\s#\*\-])"
                r"(?:"
                r"hyper[\s\-_]*care(?:\s+support)?"
                r"|post[\s\-]go[\s\-]live\s+support"
                r"|production\s+support"
                r"|warranty\s+(?:period|support)"
                r"|defect\s+support"
                r")",
                re.MULTILINE,
            ),
        ),
        # 16. ACCEPTANCE / UAT
        (
            "Acceptance",
            re.compile(
                r"(?i)(?:^|[\s#\*\-])"
                r"(?:"
                r"project\s+acceptance\s+criteria"
                r"|acceptance\s+criteria"
                r"|user\s+acceptance\s+testing(?:\s+sign[\s\-]off)?"
                r"|acceptance\s+(?:and|&)\s+sign[\s\-]off"
                r"|acknowledgement\s+(?:and|&)\s+acceptance"
                r"|terms\s+of\s+business"
                r"|confirmation\s+of\s+agreement"
                r")",
                re.MULTILINE,
            ),
        ),
        # 17. GENERAL / PREAMBLE / CONTRACT TERMS
        (
            "General",
            re.compile(
                r"(?i)(?:^|[\s#\*\-])"
                r"(?:"
                r"letter\s+of\s+engagement"
                r"|engagement\s+letter"
                r"|executive\s+summary"
                r"|introduction"
                r"|background"
                r"|preamble"
                r"|contract\s+(?:will\s+be\s+valid|validity|term|period)"
                r"|validity\s+(?:period|of\s+contract)"
                r"|term\s+and\s+termination"
                r")",
                re.MULTILINE,
            ),
        ),
    ]

    FUNCTIONAL_SCOPE_SECTIONS = frozenset({
        "In Scope", "Scope of Work", "Deliverables", "Responsibilities",
        "Milestones", "Recurring Commitments", "Functional Scope", "Technical Scope",
        "Methodology", "Governance", "Support", "Acceptance",
    })

    @classmethod
    def get_defined_sections(cls) -> List[str]:
        """Returns the full list of all supported section categories."""
        return list(cls.DEFINED_SECTIONS)

    @classmethod
    def is_structural_heading(cls, line: str) -> Tuple[bool, str, float]:
        """
        Determines whether a given raw line represents a legitimate document structural heading.
        Returns:
            (is_heading: bool, clean_heading_text: str, confidence: float)
            Confidence:
                1.00: Explicit markdown header (#) or numbered bold heading (1. **Heading**)
                0.95: Standalone bold title (**Title**) or recognized table header row
                0.90: Subsection module divider (* Functional Scope:, * **Admin View:**)
                0.00: Ordinary body text, bullet, or sentence (never a heading)
        """
        clean = line.strip()
        if not clean:
            return False, "", 0.0

        # 1. Markdown headings (# Heading, ## Heading, ### Heading)
        if clean.startswith("#"):
            h_text = clean.lstrip("#").strip()
            if h_text and len(h_text) < 140:
                return True, h_text, 1.0

        # 2. Table header rows with bold title cells: | **Role** | **Description** |
        if clean.startswith("|") and "**" in clean:
            cells = [c.strip().replace("*", "") for c in clean.split("|") if c.strip()]
            structural_table_headers = {
                "project phases", "deliverables", "role", "description of responsibilities",
                "milestones as per which invoices for fees shall be raised",
                "level of escalation", "sl. no", "milestones"
            }
            if any(any(hdr in c.lower() for hdr in structural_table_headers) for c in cells):
                return True, clean, 0.95

        # 3. Explicit module view division markers (e.g., * Functional Scope:, * **Admin View:**, * **User or Employee View:**)
        if re.match(r"^\*\s+(?:\*{1,3}[^*]+\*{1,3}:?|Functional Scope:?)$", clean, re.IGNORECASE):
            h_text = re.sub(r"^\*\s+", "", clean).replace("*", "").strip(":").strip()
            return True, h_text, 0.90

        # 3b. Bulleted section titles ending with a colon: e.g. * Technical Scope:, * AssGptions and dependencies:, * Assumptions:
        m_bullet_title = re.match(r"^\*\s+([A-Za-z\s/&]{3,60}):$", clean)
        if m_bullet_title:
            h_text = m_bullet_title.group(1).strip()
            return True, h_text, 0.90

        # 4. Numbered bold headings (e.g., 1. **Scope of Work**, * 1. **Key Assumptions**)
        m_num_bold = re.match(r"^(?:\*\s*)?(?:\d+[\.\)]|\*{1,2}\d+[\.\)])\s*\*{1,3}([^*]+)\*{1,3}:?$", clean)
        if m_num_bold:
            h_text = m_num_bold.group(1).strip()
            if 2 < len(h_text) < 140:
                return True, h_text, 1.0

        # 4b. Prefix bold numbered headings (e.g., **2 Scope of our services**, **4. Progress reporting...**)
        m_bold_num = re.match(r"^\*{1,3}\s*\d+[\.\d\)]*\s*([^*]+)\*{1,3}:?$", clean)
        if m_bold_num:
            h_text = m_bold_num.group(1).strip()
            if 2 < len(h_text) < 140:
                return True, h_text, 1.0

        # 5. Standalone bold/italic headings (e.g., ***Responsibility: GL***, **Letter of Engagement**, **Steering Committee**)
        m_bold = re.match(r"^\*{1,3}([^*]{3,90})\*{1,3}:?$", clean)
        if m_bold:
            h_text = m_bold.group(1).strip()
            if 2 < len(h_text) < 90:
                return True, h_text, 0.95

        # 5b. Standalone contractual term / validity statements (e.g. Contract will be valid till 30th April 2027)
        if re.match(r"^(?:(?:\*+\s*)?(?:Contract|Agreement)\s+(?:will\s+be\s+valid\s+till|validity\s+period|term\s+and\s+termination)\b.*)$", clean, re.IGNORECASE):
            return True, clean, 0.95

        # 6. Appendix / Section titles
        if re.match(r"^(?:Appendix|Schedule|Annexure|Exhibit)\s*[\d\w\-_]+", clean, re.IGNORECASE):
            return True, clean, 0.90

        # Reject any ordinary bullet point or numbered item as body text
        # (e.g. "* Display the organizational resource links...", "- Allow user...", "1. Ordinary body step")
        if re.match(r"^(?:[\*\-\+\•]\s+|\d+\.\s+)", clean):
            return False, "", 0.0

        return False, "", 0.0

    @classmethod
    def match_heading_to_section(cls, heading_text: str) -> Optional[Tuple[str, float]]:
        """
        Matches a verified structural heading against the 16 target sections.
        Returns (section_name, confidence) or None.
        """
        clean_header = re.sub(r"^[#\*\-\s\d\.\ :]+", "", heading_text).strip()
        
        # Explicit handling for specific EL contact / signatory sections and appendices
        h_norm = re.sub(r"[\ufffd\u2018\u2019\u201c\u201d]", "'", heading_text)
        h_norm = re.sub(r"[\u2013\u2014]", "-", h_norm).lower().strip()
        # Contact notice blocks under "Designated Contact Persons for Parties to the Agreement":
        # Contain postal addresses, phone numbers, and email contacts for legal notices — administrative terms belonging to "General"
        if re.search(r"\bon\s+[a-z0-9\s'\.\-_]+\s+side:?", h_norm):
            return "General", 1.0
        # Appendix 1: "Project Organization and Roles & Responsibilities:"
        if re.search(r"\bappendix\s*[\-\s]*1\b", h_norm):
            return "Responsibilities", 1.0

        for section_name, pattern in cls.SECTION_PATTERNS:
            if pattern.search(heading_text) or (clean_header and pattern.search(clean_header)):
                return section_name, 1.0

        # Structural fallback: out vs in scope heuristics
        h_lower = heading_text.lower()
        out_idx = h_lower.find("out")
        scope_idx = h_lower.find("scope")
        if out_idx != -1 and scope_idx != -1 and out_idx < scope_idx:
            return "Out of Scope", 0.95
        if "in scope" in h_lower or "in-scope" in h_lower:
            return "Scope of Work", 0.95

        return None

    @classmethod
    def detect_sections_with_stats(cls, chunks: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
        """
        Iterates over document chunks and tags them with the currently active section.
        Maintains structural section hierarchy and only switches section on genuine structural headings.
        Returns:
            (new_chunks, stats)
        """
        current_section = "General"
        current_confidence = 0.50
        current_inferred = True
        current_section_source = "Document Start"
        section_hierarchy: List[str] = ["General"]

        new_chunks = []
        detected_sections_set = set()
        unmapped_headings: List[Dict[str, Any]] = []

        for chunk_idx, chunk in enumerate(chunks):
            text = chunk.get("text", "")
            chunk_page = chunk.get("page_number")
            metadata = chunk.get("metadata") or {}

            # Check markdown metadata first if present
            meta_section = None
            for meta_val in metadata.values():
                if meta_val and isinstance(meta_val, str):
                    clean_meta = meta_val.strip()
                    matched = cls.match_heading_to_section(clean_meta)
                    if matched:
                        meta_section, current_confidence = matched
                        current_inferred = False
                        current_section_source = clean_meta
                        break
            if meta_section:
                current_section = meta_section
                section_hierarchy = [current_section]
                detected_sections_set.add(current_section)

            lines = text.split("\n")
            current_subchunk_lines = []
            subchunk_idx = 0

            for line in lines:
                is_heading, heading_text, heading_conf = cls.is_structural_heading(line)
                
                if is_heading:
                    matched = cls.match_heading_to_section(heading_text)
                    if matched:
                        matched_section, match_conf = matched
                        
                        # Transition if section changes OR if a distinct structural heading begins
                        if matched_section != current_section or heading_text != current_section_source:
                            # Save previous subchunk before transition
                            if current_subchunk_lines:
                                sub_text = "\n".join(current_subchunk_lines).strip()
                                if sub_text:
                                    parent_sec = section_hierarchy[0] if len(section_hierarchy) > 1 else None
                                    new_chunks.append({
                                        "chunk_index": chunk.get("chunk_index", chunk_idx),
                                        "segment_index": subchunk_idx,
                                        "page_number": chunk_page,
                                        "text": sub_text,
                                        "section": current_section,
                                        "section_confidence": current_confidence,
                                        "is_inferred": current_inferred,
                                        "parent_section": parent_sec,
                                        "section_source": current_section_source,
                                        "section_hierarchy": list(section_hierarchy),
                                    })
                                    subchunk_idx += 1
                                    current_subchunk_lines = []

                            # Update section state
                            current_section = matched_section
                            current_confidence = min(heading_conf, match_conf)
                            current_inferred = False
                            current_section_source = heading_text
                            
                            # Update hierarchy
                            if matched_section in ("Functional Scope", "Technical Scope") and "Scope of Work" in section_hierarchy:
                                section_hierarchy = ["Scope of Work", matched_section]
                            elif matched_section in ("Client Responsibilities", "Responsibilities") and "Responsibilities" in section_hierarchy:
                                section_hierarchy = ["Responsibilities", matched_section]
                            else:
                                section_hierarchy = [matched_section]
                            detected_sections_set.add(current_section)

                    else:
                        # Unmapped structural heading: safe fallback to "General", preserving original heading
                        unmapped_headings.append({
                            "heading": heading_text,
                            "chunk_index": chunk.get("chunk_index", chunk_idx),
                            "confidence": heading_conf,
                        })
                        fallback_sec = "General"
                        if (fallback_sec != current_section or heading_text != current_section_source) and heading_conf >= 0.85:
                            if current_subchunk_lines:
                                sub_text = "\n".join(current_subchunk_lines).strip()
                                if sub_text:
                                    parent_sec = section_hierarchy[0] if len(section_hierarchy) > 1 else None
                                    new_chunks.append({
                                        "chunk_index": chunk.get("chunk_index", chunk_idx),
                                        "segment_index": subchunk_idx,
                                        "page_number": chunk_page,
                                        "text": sub_text,
                                        "section": current_section,
                                        "section_confidence": current_confidence,
                                        "is_inferred": current_inferred,
                                        "parent_section": parent_sec,
                                        "section_source": current_section_source,
                                        "section_hierarchy": list(section_hierarchy),
                                    })
                                    subchunk_idx += 1
                                    current_subchunk_lines = []
                            current_section = fallback_sec
                            current_confidence = 0.50
                            current_inferred = True
                            current_section_source = heading_text
                            section_hierarchy = [fallback_sec]
                            detected_sections_set.add(current_section)
                        elif heading_conf >= 0.85:
                            current_section_source = heading_text

                current_subchunk_lines.append(line)

            # Append the remainder of the chunk
            if current_subchunk_lines:
                sub_text = "\n".join(current_subchunk_lines).strip()
                if sub_text:
                    parent_sec = section_hierarchy[0] if len(section_hierarchy) > 1 else None
                    new_chunks.append({
                        "chunk_index": chunk.get("chunk_index", chunk_idx),
                        "segment_index": subchunk_idx,
                        "page_number": chunk_page,
                        "text": sub_text,
                        "section": current_section,
                        "section_confidence": current_confidence,
                        "is_inferred": current_inferred,
                        "parent_section": parent_sec,
                        "section_source": current_section_source,
                        "section_hierarchy": list(section_hierarchy),
                    })
                    subchunk_idx += 1

        # Populate detected_sections_set comprehensively from all emitted segments
        detected_sections_set = {c["section"] for c in new_chunks if c.get("section")}
        if not detected_sections_set:
            detected_sections_set = {"General"}

        stats = {
            "categories_defined": len(cls.DEFINED_SECTIONS),
            "categories_detected": len(detected_sections_set),
            "detected_sections": sorted(list(detected_sections_set)),
            "all_defined_sections": list(cls.DEFINED_SECTIONS),
            "unmapped_headings": unmapped_headings,
        }
        return new_chunks, stats

    @classmethod
    def detect_sections(cls, chunks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Backward-compatible detect_sections returning list of tagged chunk segments.
        """
        new_chunks, _ = cls.detect_sections_with_stats(chunks)
        return new_chunks
