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
        "project milestones & timeline"
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
        for h in ["change request process", "change control process", "formal change request"]:
            if h in lower_text:
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
                        reconstructed = f"{item_text} - by {date_text}"
                        
                        # Bypass _process_and_add to directly tag pure milestone properties
                        cand = cls._create_candidate(reconstructed, chunk, document_id)
                        cand["is_pure_milestone"] = True
                        
                        # Infer status
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
