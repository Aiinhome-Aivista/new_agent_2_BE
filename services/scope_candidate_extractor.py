import re

class ScopeCandidateExtractor:
    """
    Extracts candidate scope items from chunks using deterministic rules (bullets, numbering, short sentences).
    """
    
    # Sections from which we want to extract candidates
    TARGET_SECTIONS = {
        "Scope of Work", 
        "Deliverables", 
        "Responsibilities", 
        "Client Responsibilities",
        "Out of Scope",
        "Assumptions",
        "Dependencies",
        "Milestones"
    }

    HEADING_BLACKLIST = {
        "project timeline", "timeline", "scope control", "change management",
        "change request process", "change control process", "in scope", "out of scope",
        "project milestones & timeline", "recurring commitments", "recurring commitment",
        "assumptions", "key assumptions", "change control", "project overview",
        "deliverables", "milestones", "scope of work", "governance", "phase",
        "status", "remarks", "client responsibilities", "dependencies"
    }

    @classmethod
    def _is_heading(cls, text: str) -> bool:
        lower_text = text.lower().strip()
        if not lower_text:
            return True

        # Check raw string in blacklist
        if lower_text in cls.HEADING_BLACKLIST:
            return True

        # Strip section numbering (e.g., "6. Recurring Commitments", "Section 1: Project Overview", "4. Assumptions")
        cleaned_prefix = re.sub(
            r'^(?:section\s+[0-9]+[:\.\s]*|[0-9]+(?:\.[0-9]+)*[:\.\s]+|[A-Za-z]\)\s*)',
            '',
            lower_text
        ).strip()

        if cleaned_prefix in cls.HEADING_BLACKLIST:
            return True

        if lower_text.startswith("section "):
            return True

        # Strip punctuation and check again
        cleaned_punct = re.sub(r'[^\w\s]', '', cleaned_prefix).strip()
        if cleaned_punct in cls.HEADING_BLACKLIST:
            return True

        # Table header row detection (e.g. "Phase  Timeline  Status  Remarks")
        if re.search(r'\b(phase|milestone|deliverable)\b.*?\b(timeline|date|status|remarks)\b', lower_text):
            return True

        # Pure parenthetical frequency / occurrence notes (e.g. "(Monthly, from March 2026 through December 2026 — 10 occurrences)")
        if re.match(r'^\s*\(.*?\b(?:occurrences?|monthly|weekly|quarterly|yearly)\b.*?\)\s*$', text, re.IGNORECASE):
            return True

        for h in ["change request process", "change control process", "formal change request"]:
            if h in lower_text:
                return True

        return False

    @staticmethod
    def _split_clauses(text: str) -> list[str]:
        """
        Split a composite sentence into individual deliverable clauses on period/semicolon,
        while protecting parentheses, brackets, and common abbreviations (e.g., i.e., vs., etc.).
        """
        parts = []
        current = []
        depth = 0
        i = 0
        n = len(text)

        while i < n:
            char = text[i]
            if char in '([{':
                depth += 1
                current.append(char)
            elif char in ')]}':
                if depth > 0:
                    depth -= 1
                current.append(char)
            elif char in '.;' and depth == 0:
                prefix = "".join(current).lower()
                # Check for common abbreviations
                if any(prefix.endswith(abbr) for abbr in ['e.g', 'i.e', 'etc', 'vs', 'inc', 'corp', 'no', 'ver', 'dept', 'fig', 'al']):
                    current.append(char)
                elif i + 1 < n and text[i + 1].isdigit():  # e.g., version 1.5
                    current.append(char)
                elif i + 1 < n and text[i + 1] == ' ':
                    # Lookahead to see what follows whitespace
                    j = i + 1
                    while j < n and text[j] == ' ':
                        j += 1
                    if j < n and text[j] == '(':
                        # Parenthetical modifier of the preceding clause (e.g. ". (Monthly, from ...)")
                        current.append(char)
                    else:
                        part_str = "".join(current).strip()
                        if part_str:
                            parts.append(part_str)
                        current = []
                        # Skip following whitespace
                        while i + 1 < n and text[i + 1] == ' ':
                            i += 1
                else:
                    current.append(char)
            else:
                current.append(char)
            i += 1

        last = "".join(current).strip()
        if last:
            parts.append(last)

        return parts

    @classmethod
    def _process_and_add(cls, candidates: list, text: str, chunk: dict, document_id: int):
        parts = cls._split_clauses(text)
        for part in parts:
            part = part.strip()
            if part.endswith('.') or part.endswith(';'):
                part = part[:-1].strip()
            
            if len(part) < 5 or cls._is_heading(part):
                continue
                
            candidates.append(cls._create_candidate(part, chunk, document_id))

    @classmethod
    def _extract_from_chunks(cls, chunks: list[dict], document_id: int, allow_all_sections: bool = False) -> list[dict]:
        candidates = []
        for chunk in chunks:
            section = chunk.get("section", "General")
            if not allow_all_sections and section not in cls.TARGET_SECTIONS:
                continue
                
            text = chunk.get("text", "")
            lines = text.split("\n")
            
            for i, line in enumerate(lines):
                line = line.strip()
                if not line:
                    continue
                    
                # Rule 1: Bullet points
                if re.match(r'^[\-\•\*o]\s+', line):
                    candidate_text = re.sub(r'^[\-\•\*o]\s+', '', line).strip()
                    if candidate_text and not cls._is_heading(candidate_text):
                        cls._process_and_add(candidates, candidate_text, chunk, document_id)
                        continue
                
                # Rule 2: Numbered lists (e.g., 1., 1.1, a))
                if re.match(r'^([0-9]+(\.[0-9]+)*\.|[a-zA-Z]\))\s+', line):
                    candidate_text = re.sub(r'^([0-9]+(\.[0-9]+)*\.|[a-zA-Z]\))\s+', '', line).strip()
                    if candidate_text and not cls._is_heading(candidate_text):
                        cls._process_and_add(candidates, candidate_text, chunk, document_id)
                        continue
                        
                # Rule 3: Table rows with dates (e.g., M1  Discovery  15 Jul)
                table_match = re.search(r'^(.*?)(?:\t|\s{2,})([0-9]{1,2}\s+[A-Za-z]+(?:\s+[0-9]{2,4})?|[0-9]{4}-[0-9]{2}-[0-9]{2}|[0-9]{1,2}/[0-9]{1,2}/[0-9]{2,4})(?:(?:\t|\s{2,})(.*))?$', line)
                if table_match:
                    item_text = table_match.group(1).strip()
                    date_text = table_match.group(2).strip()
                    status_text = table_match.group(3).strip() if table_match.group(3) else "Planned"
                    
                    if len(item_text) > 2 and not cls._is_heading(item_text) and item_text.lower() != 'phase':
                        cand = cls._create_candidate(item_text, chunk, document_id)
                        cand["is_pure_milestone"] = True
                        cand["deadline_text"] = date_text
                        cand["milestone"] = item_text
                        
                        status_word = status_text.split('\t')[0].strip().lower()
                        if status_word in ["completed", "complete", "done"]:
                            cand["milestone_status"] = "Completed"
                        elif status_word.startswith("in progress") or status_word in ["in", "progress", "ongoing"]:
                            cand["milestone_status"] = "In Progress"
                        else:
                            cand["milestone_status"] = "Planned"
                            
                        candidates.append(cand)
                    continue
                        
                # Rule 4: Short standalone sentences
                if 10 < len(line) < 150 and not line.endswith(':') and not cls._is_heading(line):
                    cls._process_and_add(candidates, line, chunk, document_id)

        return candidates

    @classmethod
    def extract_candidates(cls, chunks: list[dict], document_id: int) -> list[dict]:
        # First pass: Target sections only
        candidates = cls._extract_from_chunks(chunks, document_id, allow_all_sections=False)
        
        # Second pass (fallback): If no candidates found, scan all chunks (including "General")
        if not candidates:
            print("Notice: Target section candidate extraction yielded 0 items. Running fallback scan across all chunks...")
            candidates = cls._extract_from_chunks(chunks, document_id, allow_all_sections=True)
            
        return candidates
        
    @staticmethod
    def _create_candidate(text: str, chunk: dict, document_id: int) -> dict:
        # Determine a clean candidate name from the extracted clause / bullet text.
        # Preserve full deliverable descriptions so brackets and words are never prematurely truncated.
        clean_text = text.strip()
        if clean_text.endswith('.') or clean_text.endswith(';'):
            clean_text = clean_text[:-1].strip()

        name = clean_text
        if len(name) > 250:
            name = name[:250].rsplit(' ', 1)[0].strip()
        
        # Balance open parentheses/brackets if cut off
        if name.count('(') > name.count(')'):
            name += ')' * (name.count('(') - name.count(')'))
        if name.count('[') > name.count(']'):
            name += ']' * (name.count('[') - name.count(']'))
            
        return {
            "name": name,
            "description": text,
            "page_number": chunk.get("page_number"),
            "section": chunk.get("section"),
            "chunk_index": chunk.get("chunk_index"),
            "raw_text": text,
            "document_id": document_id
        }

