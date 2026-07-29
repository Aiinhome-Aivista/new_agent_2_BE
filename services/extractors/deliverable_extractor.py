import re
from typing import Dict, Any, List

class DeliverableExtractor:
    """
    Specialized extractor for deliverable items from Document Tree sections.
    """
    
    OWNER_PATTERNS = [
        r"(?i)(?:owned?\s+by|assigned\s+to|responsible\s*:\s*|lead\s*:\s*|owner\s*:\s*)([A-Z][a-z]+(?:\s+[A-Z][a-z]+)*)",
        r"(?i)([A-Z][a-z]+(?:\s+[A-Z][a-z]+)*)\s+(?:will deliver|is responsible|owns)",
    ]

    @classmethod
    def extract(cls, section: Dict[str, Any]) -> List[Dict[str, Any]]:
        extracted = []
        blocks = section.get("content_blocks", [])
        
        for block in blocks:
            b_type = block.get("type")
            text = block.get("text", "").strip()
            
            if not text and b_type != "table":
                continue
                
            if b_type == "table":
                headers = [h.lower().strip() for h in block.get("headers", [])]
                import json
                for row in block.get("rows", []):
                    if not any(row):
                        continue
                    
                    row_obj = {}
                    for idx, cell in enumerate(row):
                        if cell:
                            header = block.get("headers", [])[idx] if idx < len(block.get("headers", [])) else f"Col{idx}"
                            row_obj[header] = cell
                            
                    description = " | ".join([c for c in row if c])
                    
                    # Reject generic table headers
                    generic_headers = ["CONFIDENTIAL", "COMPANY PROPRIETARY", "PAGE", "TABLE OF CONTENTS", "INDEX", "MODULE NAME", "PHASE", "TASK DESCRIPTION", "DEADLINE", "DURATION", "DELIVERABLE / OUTPUT", "DELIVERABLE", "OUTPUT", "OWNER"]
                    if any(gh == description.strip().upper() for gh in generic_headers) or any(gh in description.upper() for gh in ["CONFIDENTIAL", "COMPANY PROPRIETARY", "TABLE OF CONTENTS"]):
                        continue
                    name = None
                    deadline = None
                    owner = None
                    
                    # Try to map columns based on headers
                    # First pass for explicit deliverable column
                    for idx, h in enumerate(headers):
                        if idx >= len(row):
                            break
                        if "deliverable" in h or "output" in h:
                            name = row[idx]
                            break
                            
                    # Second pass for other mappings
                    for idx, h in enumerate(headers):
                        if idx >= len(row):
                            break
                        if not name and ("name" in h or "task" in h or "item" in h):
                            name = row[idx]
                        elif "date" in h or "deadline" in h or "timeline" in h or "due" in h:
                            if not deadline: deadline = row[idx]
                        elif "owner" in h or "responsibility" in h or "who" in h:
                            if not owner: owner = row[idx]
                            
                    # Fallbacks
                    if not name:
                        name = row[0][:60] if row[0] else "Unknown Deliverable"
                    else:
                        name = name[:60]
                        
                    if not deadline:
                        date_match = re.search(
                            r'(\d{1,2}[\s/\-]\w+[\s/\-]\d{2,4}|\d{4}-\d{2}-\d{2}|Q[1-4]\s+\d{4}|\d+\s+(?:Days|Weeks|Months))',
                            description, re.IGNORECASE
                        )
                        if date_match:
                            deadline = date_match.group(1)
                            
                    extracted.append({
                        "type": "DELIVERABLE",
                        "raw_text": description,
                        "name": name,
                        "description": description,
                        "deadline": deadline,
                        "owner": owner,
                        "metadata_json": json.dumps(row_obj, ensure_ascii=False),
                        "source_page": block.get("page_number"),
                        "source_section": section.get("section_name"),
                        "confidence": 0.95
                    })
            elif b_type == "bullet" or b_type == "paragraph":
                # Only take substantial text as deliverable candidates
                if len(text) < 5:
                    continue
                
                # Reject generic headers
                generic_headers = ["CONFIDENTIAL", "COMPANY PROPRIETARY", "PAGE", "TABLE OF CONTENTS", "INDEX", "MODULE NAME", "PHASE", "TASK DESCRIPTION", "DEADLINE", "DURATION", "DELIVERABLE / OUTPUT", "DELIVERABLE", "OUTPUT", "OWNER"]
                if any(gh == text.strip().upper() for gh in generic_headers) or any(gh in text.upper() for gh in ["CONFIDENTIAL", "COMPANY PROPRIETARY", "TABLE OF CONTENTS"]):
                    continue
                    
                deadline = None
                deadline_match = re.search(
                    r'(?i)(?:by|on|before|due|target)\s+(\d{1,2}\s+\w+\s+\d{4}|\d{4}-\d{2}-\d{2}|\d{1,2}/\d{1,2}/\d{2,4}|Q[1-4]\s+\d{4})',
                    text
                )
                if deadline_match:
                    deadline = deadline_match.group(1)
                
                owner = None
                for pattern in cls.OWNER_PATTERNS:
                    owner_match = re.search(pattern, text)
                    if owner_match:
                        owner = owner_match.group(1).strip()
                        break
                
                name_parts = re.split(r'[:\.]|\s-\s', text, 1)
                name = name_parts[0].strip() if name_parts[0] else text[:50]
                if len(name) > 60:
                    name = text[:60].rsplit(' ', 1)[0] + "..."
                    
                extracted.append({
                    "type": "DELIVERABLE",
                    "raw_text": text,
                    "name": name,
                    "description": text,
                    "deadline": deadline,
                    "owner": owner,
                    "source_page": block.get("page_number"),
                    "source_section": section.get("section_name"),
                    "confidence": 0.8
                })
                
        return extracted
