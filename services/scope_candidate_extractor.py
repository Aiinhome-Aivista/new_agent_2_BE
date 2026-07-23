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
                        candidates.append(cls._create_candidate(candidate_text, chunk, document_id))
                        continue
                
                # Rule 2: Numbered lists (e.g., 1., 1.1, a))
                if re.match(r'^([0-9]+(\.[0-9]+)*\.|[a-zA-Z]\))\s+', line):
                    candidate_text = re.sub(r'^([0-9]+(\.[0-9]+)*\.|[a-zA-Z]\))\s+', '', line).strip()
                    if candidate_text:
                        candidates.append(cls._create_candidate(candidate_text, chunk, document_id))
                        continue
                        
                # Rule 3: Short standalone sentences (likely headings or key points if in a targeted section)
                # But only if it's not too long and not ending in a colon
                if 10 < len(line) < 150 and not line.endswith(':'):
                    # Check if it looks like a sentence (starts with capital, ends with punctuation or just short phrase)
                    candidates.append(cls._create_candidate(line, chunk, document_id))

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
