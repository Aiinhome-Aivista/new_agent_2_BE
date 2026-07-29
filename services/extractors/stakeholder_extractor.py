import re
from typing import Dict, Any, List

class StakeholderExtractor:
    """
    Extracts stakeholders and user roles from STAKEHOLDERS or ACTORS sections.
    """
    @classmethod
    def extract(cls, section: Dict[str, Any]) -> List[Dict[str, Any]]:
        extracted = []
        blocks = section.get("content_blocks", [])
        
        semantic_type = section.get("semantic_type", "STAKEHOLDERS")
        entity_type = "ACTOR" if semantic_type == "ACTORS" else "STAKEHOLDER"
        
        for block in blocks:
            b_type = block.get("type")
            text = block.get("text", "").strip()
            
            if not text and b_type != "table":
                continue
                
            if b_type == "table":
                headers = [h.lower() for h in block.get("headers", [])]
                for row in block.get("rows", []):
                    if not any(row):
                        continue
                    
                    name = row[0][:60] if row[0] else "Unknown"
                    description = " | ".join([c for c in row if c])
                    email = None
                    role = None
                    
                    # Try to map columns based on headers
                    for idx, h in enumerate(headers):
                        if idx >= len(row):
                            break
                        if "email" in h or "contact" in h:
                            email_match = re.search(r'[\w\.-]+@[\w\.-]+\.\w+', row[idx])
                            if email_match:
                                email = email_match.group(0)
                        elif "role" in h or "title" in h or "position" in h:
                            role = row[idx]
                            
                    # Note: We output it as a normal entity which will be picked up by baseline.py
                    extracted.append({
                        "type": entity_type,
                        "raw_text": description,
                        "name": name,
                        "description": description,
                        "email": email,
                        "role": role,
                        "source_page": block.get("page_number"),
                        "source_section": section.get("section_name"),
                        "confidence": 0.85
                    })
            elif b_type in ["bullet", "paragraph"]:
                if len(text) < 10:
                    continue
                
                # Try to extract roles/names from text
                name_parts = re.split(r'[:\.\-]', text, 1)
                name = name_parts[0].strip() if name_parts[0] else text[:50]
                if len(name) > 60:
                    name = text[:60].rsplit(' ', 1)[0] + "..."
                    
                email = None
                email_match = re.search(r'[\w\.-]+@[\w\.-]+\.\w+', text)
                if email_match:
                    email = email_match.group(0)
                    
                extracted.append({
                    "type": entity_type,
                    "raw_text": text,
                    "name": name,
                    "description": text,
                    "email": email,
                    "source_page": block.get("page_number"),
                    "source_section": section.get("section_name"),
                    "confidence": 0.8
                })
                
        return extracted
