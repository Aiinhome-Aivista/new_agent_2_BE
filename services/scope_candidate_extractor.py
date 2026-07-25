import re

class ScopeCandidateExtractor:
    """
    Extracts candidate scope items from chunks using deterministic rules (bullets, numbering, short sentences).
    """
    
    # Sections from which we want to extract candidates
    TARGET_SECTIONS = {
        "Scope of Work", "Deliverables", "Responsibilities", 
        "Client Responsibilities", "Out of Scope", "Assumptions", "Dependencies"
    }

    HEADING_BLACKLIST = {
        "project scope", "out of scope", "assumptions", "change control", 
        "deliverables", "milestones", "acceptance", "definitions", 
        "responsibilities", "appendix", "revision history", 
        "client responsibilities", "dependencies", "scope of work",
        "governance", "commercial terms", "signature page", 
        "business objectives", "executive summary", "project background", 
        "roles & responsibilities", "acceptance criteria"
    }

    @classmethod
    def _is_heading(cls, text: str) -> bool:
        lower_text = text.lower().strip()
        if lower_text in cls.HEADING_BLACKLIST:
            return True
        if lower_text.startswith("section "):
            return True
        # Strip punctuation and check again
        cleaned = re.sub(r'[^\w\s]', '', lower_text).strip()
        if cleaned in cls.HEADING_BLACKLIST:
            return True
        return False

    @classmethod
    def _process_and_add(cls, candidates: list, text: str, chunk: dict, document_id: int):
        # Issue 2: Split multiple obligations
        parts = re.split(r'\.\s+|;\s+', text)
        for part in parts:
            part = part.strip()
            if part.endswith('.') or part.endswith(';'):
                part = part[:-1].strip()
            
            if len(part) < 5 or cls._is_heading(part):
                continue
                
            candidates.append(cls._create_candidate(part, chunk, document_id))

    @classmethod
    def extract_candidates(cls, chunks: list[dict], document_id: int) -> list[dict]:
        candidates = []
        
        for chunk in chunks:
            section = chunk.get("section", "General")
            if section not in cls.TARGET_SECTIONS:
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
                    if candidate_text:
                        cls._process_and_add(candidates, candidate_text, chunk, document_id)
                        continue
                
                # Rule 2: Numbered lists (e.g., 1., 1.1, a))
                if re.match(r'^([0-9]+(\.[0-9]+)*\.|[a-zA-Z]\))\s+', line):
                    candidate_text = re.sub(r'^([0-9]+(\.[0-9]+)*\.|[a-zA-Z]\))\s+', '', line).strip()
                    if candidate_text:
                        cls._process_and_add(candidates, candidate_text, chunk, document_id)
                        continue
                        
                # Rule 3: Table rows with dates at the end (e.g., M1  Discovery  15 Jul)
                table_match = re.search(r'^(.*?)\s{2,}([0-9]{1,2}\s+[A-Za-z]+(?:\s+[0-9]{2,4})?|[0-9]{4}-[0-9]{2}-[0-9]{2}|[0-9]{1,2}/[0-9]{1,2}/[0-9]{2,4})$', line)
                if table_match:
                    item_text = table_match.group(1).strip()
                    date_text = table_match.group(2).strip()
                    if len(item_text) > 5 and not cls._is_heading(item_text):
                        reconstructed = f"{item_text} by {date_text}"
                        cls._process_and_add(candidates, reconstructed, chunk, document_id)
                    continue
                        
                # Rule 4: Short standalone sentences
                if 10 < len(line) < 150 and not line.endswith(':'):
                    cls._process_and_add(candidates, line, chunk, document_id)

        return candidates
        
    @staticmethod
    def _create_candidate(text: str, chunk: dict, document_id: int) -> dict:
        # Determine a short name (first few words or up to first punctuation)
        # e.g., "Web Portal Design - We will build the portal" -> "Web Portal Design"
        name_match = re.split(r'[:\.]|\s-\s', text, 1)
        name = name_match[0].strip() if name_match[0] else text[:50]
        if len(name) > 60:
            name = text[:60].rsplit(' ', 1)[0] + "..."
            
        return {
            "name": name,
            "description": text,
            "page_number": chunk.get("page_number"),
            "section": chunk.get("section"),
            "chunk_index": chunk.get("chunk_index"),
            "raw_text": text,
            "document_id": document_id
        }
