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
        "roles & responsibilities", "acceptance criteria",
        "scope of services", "scope of engagement", "engagement scope",
        "in scope", "in-scope", "in scope items", "services",
        "key deliverables", "project deliverables", "service deliverables",
        "milestones and deliverables", "milestones & deliverables",
        "outputs", "work products", "work packages",
        "vendor responsibilities", "our responsibilities", "firm responsibilities",
        "vendor obligations", "our obligations", "firm obligations",
        "customer responsibilities", "your responsibilities", "company responsibilities",
        "customer obligations", "your obligations",
        "out-of-scope", "exclusions", "not included", "limitations",
        "items not in scope", "items out of scope", "excluded services",
        "key assumptions", "project assumptions", "commercial assumptions",
        "general assumptions", "preconditions",
        "project dependencies", "key dependencies", "pre-requisites", "prerequisites",
        "key activities", "core activities", "phases", "phases of work",
        "services overview", "service description", "service scope",
        "services to be provided", "roles and responsibilities",
        "table of contents", "terms and conditions", "confidentiality",
        "introduction", "overview", "about", "background",
        "restrictions", "what is not included", "what is not covered",
        "parties to the agreement", "service provider / developer",
        "project overview & technology architecture", "commercial terms & payment milestones",
        "roles & client dependencies", "terms, intellectual property & acceptance",
        "warranty & support", "detailed scope of work",
        "module name | key functional features",
        "phase | task description | start date | deadline | duration | deliverable / output",
        "milestone | trigger / condition | percentage | amount (inr)",
        "frontend interface", "backend service", "database engine", "deployment & cloud",
        "project timeline, milestones & task schedule"
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
    def _is_invalid_candidate(cls, text: str) -> bool:
        """Filters out boilerplate and non-scope lines like 'Date', 'Signature', etc."""
        lower_text = text.lower().strip()
        if len(lower_text) < 8:
            return True
        
        # Exact match blacklists
        boilerplate = {
            "date", "signature", "name", "title", "company", "client", 
            "engagement letter ref no", "reference no", "ref no"
        }
        if lower_text in boilerplate:
            return True
            
        # Prefix blacklists
        for prefix in ["date:", "ref no:", "reference:", "engagement letter:", "page "]:
            if lower_text.startswith(prefix):
                return True
                
        # Substring blacklists (common boilerplate sentences)
        if "this engagement letter" in lower_text and "entered into" in lower_text:
            return True
            
        # Reject broken lines ending with ampersand or common conjunctions
        if lower_text.endswith("&") or lower_text.endswith("and"):
            return True
            
        # Reject table headers or raw rows that contain multiple pipes (unless it's a very specific item, but mostly garbage)
        if lower_text.count('|') >= 2 and ("phase" in lower_text or "milestone" in lower_text or "description" in lower_text or "amount" in lower_text):
            return True
            
        return False

    @classmethod
    def _process_and_add(cls, candidates: list, text: str, chunk: dict, document_id: int):
        if cls._is_invalid_candidate(text):
            return
            
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
        
        # Fallback: If no section headers were detected at all (all chunks are "General"),
        # treat all chunks as potential scope sources to avoid zero extraction.
        all_general = all(chunk.get("section", "General") == "General" for chunk in chunks)
        
        for chunk in chunks:
            if chunk.get("block_type") == "heading":
                continue
                
            section = chunk.get("section", "General")
            if not all_general and section not in cls.TARGET_SECTIONS:
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
                if 15 < len(line) < 250 and not line.endswith(':'):
                    # Must have at least 4 words and contain some action-oriented keyword to be a standalone candidate
                    words = line.split()
                    if len(words) >= 4:
                        action_keywords = {"will", "shall", "develop", "provide", "deliver", "build", "create", "responsible", "must", "ensure", "deploy", "design", "implement", "support", "maintain", "test", "integrate"}
                        if any(kw in line.lower() for kw in action_keywords):
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
