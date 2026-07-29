import re

class DeliverableExtractor:
    """
    Specialized extractor for deliverable items from document chunks.
    Extracts from 'Deliverables' sections and table rows with deadline patterns.
    """
    
    DELIVERABLE_SECTIONS = {"Deliverables", "Scope of Work", "Responsibilities"}
    
    OWNER_PATTERNS = [
        r"(?i)(?:owned?\s+by|assigned\s+to|responsible\s*:\s*|lead\s*:\s*|owner\s*:\s*)([A-Z][a-z]+(?:\s+[A-Z][a-z]+)*)",
        r"(?i)([A-Z][a-z]+(?:\s+[A-Z][a-z]+)*)\s+(?:will deliver|is responsible|owns)",
    ]
    
    HEADING_SKIP = {
        "deliverables", "key deliverables", "project deliverables",
        "service deliverables", "milestones and deliverables",
        "milestones & deliverables", "expected deliverables",
        "outputs", "work products"
    }

    @classmethod
    def extract(cls, chunks: list[dict], document_id: int) -> list[dict]:
        """
        Extracts deliverable items from chunks tagged as 'Deliverables' section.
        Also processes table_row block types for structured deliverable data.
        Returns: [{name, description, deadline, owner, source_page, source_section}]
        """
        deliverables = []
        
        for chunk in chunks:
            section = chunk.get("section", "General")
            block_type = chunk.get("block_type", "paragraph")
            
            # Only extract from Deliverables section
            if section != "Deliverables":
                continue
                
            text = chunk.get("text", "")
            page = chunk.get("page_number")
            
            # Strategy 1: Table rows (structured data with | delimiters)
            if block_type == "table_row" or "|" in text:
                parts = [p.strip() for p in text.split("|") if p.strip()]
                if len(parts) >= 2:
                    name = parts[0]
                    description = parts[1] if len(parts) > 1 else name
                    deadline = None
                    owner = None
                    
                    # Look for date-like values in remaining parts
                    for part in parts[2:]:
                        date_match = re.search(
                            r'(\d{1,2}[\s/\-]\w+[\s/\-]\d{2,4}|\d{4}-\d{2}-\d{2}|Q[1-4]\s+\d{4})',
                            part
                        )
                        if date_match:
                            deadline = date_match.group(1)
                        elif re.match(r'^[A-Z][a-z]', part) and len(part) < 40:
                            owner = part
                    
                    if name.lower().strip() not in cls.HEADING_SKIP and len(name) > 3:
                        deliverables.append({
                            "name": name[:60],
                            "description": description,
                            "deadline": deadline,
                            "owner": owner,
                            "source_page": page,
                            "source_section": section
                        })
                continue
            
            # Strategy 2: Bullet/numbered lists
            lines = text.split("\n")
            for line in lines:
                line = line.strip()
                if not line or len(line) < 5:
                    continue
                
                # Skip section headings
                if line.lower().strip().rstrip('.:') in cls.HEADING_SKIP:
                    continue
                    
                candidate_text = None
                
                # Bullet points
                bullet_match = re.match(r'^[\-\•\*\u2022\u25CF\u25CB○●◆►▪]\s+(.+)', line)
                if bullet_match:
                    candidate_text = bullet_match.group(1).strip()
                
                # Numbered lists
                if not candidate_text:
                    num_match = re.match(r'^([0-9]+(\.[0-9]+)*[\.\)]|[a-zA-Z][\.\)])\s+(.+)', line)
                    if num_match:
                        candidate_text = num_match.group(3).strip()
                
                # Short standalone sentences (< 150 chars, not ending with ':')
                if not candidate_text and 10 < len(line) < 150 and not line.endswith(':'):
                    candidate_text = line
                
                if not candidate_text:
                    continue
                
                # Extract deadline from the text
                deadline = None
                deadline_match = re.search(
                    r'(?i)(?:by|on|before|due|target)\s+(\d{1,2}\s+\w+\s+\d{4}|\d{4}-\d{2}-\d{2}|\d{1,2}/\d{1,2}/\d{2,4}|Q[1-4]\s+\d{4})',
                    candidate_text
                )
                if deadline_match:
                    deadline = deadline_match.group(1)
                
                # Extract owner from the text
                owner = None
                for pattern in cls.OWNER_PATTERNS:
                    owner_match = re.search(pattern, candidate_text)
                    if owner_match:
                        owner = owner_match.group(1).strip()
                        break
                
                # Generate name (first part before punctuation or first 60 chars)
                name_parts = re.split(r'[:\.]|\s-\s', candidate_text, 1)
                name = name_parts[0].strip() if name_parts[0] else candidate_text[:50]
                if len(name) > 60:
                    name = candidate_text[:60].rsplit(' ', 1)[0] + "..."
                
                deliverables.append({
                    "name": name,
                    "description": candidate_text,
                    "deadline": deadline,
                    "owner": owner,
                    "source_page": page,
                    "source_section": section
                })
        
        return deliverables
