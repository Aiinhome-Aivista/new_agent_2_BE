import re
from typing import Dict, Any, List

class MilestoneExtractor:
    """
    Specialized extractor for milestone items from Document Tree sections.
    """

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
                    milestone_name = row[0][:60] if row[0] else "Unknown Milestone"
                    deadline = None
                    owner = None
                    deliverable = None
                    duration = None
                    phase = None
                    
                    # Reject generic table headers
                    generic_headers = ["CONFIDENTIAL", "COMPANY PROPRIETARY", "PAGE", "TABLE OF CONTENTS", "INDEX", "MODULE NAME", "PHASE", "TASK DESCRIPTION", "DEADLINE", "DURATION", "DELIVERABLE / OUTPUT", "DELIVERABLE", "OUTPUT", "OWNER", "TASK"]
                    if any(gh == description.strip().upper() for gh in generic_headers) or any(gh in description.upper() for gh in ["CONFIDENTIAL", "COMPANY PROPRIETARY", "TABLE OF CONTENTS"]):
                        continue
                    
                    # Try to map columns based on headers
                    for idx, h in enumerate(headers):
                        if idx >= len(row):
                            break
                        if "date" in h or "deadline" in h or "timeline" in h or "due" in h:
                            deadline = row[idx]
                        elif "owner" in h or "responsibility" in h or "who" in h:
                            owner = row[idx]
                        elif "deliverable" in h or "output" in h:
                            deliverable = row[idx]
                        elif "duration" in h or "days" in h or "weeks" in h:
                            duration = row[idx]
                        elif "phase" in h or "stage" in h:
                            phase = row[idx]
                            
                    if not deadline:
                        # Fallback regex in the combined text
                        date_match = re.search(
                            r'(\d{1,2}[\s/\-]\w+[\s/\-]\d{2,4}|\d{4}-\d{2}-\d{2}|Q[1-4]\s+\d{4}|\d+\s+(?:Days|Weeks|Months))',
                            description, re.IGNORECASE
                        )
                        if date_match:
                            deadline = date_match.group(1)
                            
                    # Construct richer metadata
                    rich_metadata = row_obj.copy()
                    if owner and "owner" not in [k.lower() for k in rich_metadata.keys()]: rich_metadata["owner"] = owner
                    if phase and "phase" not in [k.lower() for k in rich_metadata.keys()]: rich_metadata["phase"] = phase
                    if duration and "duration" not in [k.lower() for k in rich_metadata.keys()]: rich_metadata["duration"] = duration
                            
                    extracted.append({
                        "type": "MILESTONE",
                        "raw_text": description,
                        "milestone_name": milestone_name,
                        "deadline": deadline,
                        "metadata_json": json.dumps(rich_metadata, ensure_ascii=False),
                        "source_page": block.get("page_number"),
                        "source_section": section.get("section_name"),
                        "confidence": 0.9 if deadline else 0.7
                    })
            elif b_type == "bullet" or b_type == "paragraph":
                # Reject generic headers
                generic_headers = ["CONFIDENTIAL", "COMPANY PROPRIETARY", "PAGE", "TABLE OF CONTENTS", "INDEX", "MODULE NAME", "PHASE", "TASK DESCRIPTION", "DEADLINE", "DURATION", "DELIVERABLE / OUTPUT", "DELIVERABLE", "OUTPUT", "OWNER", "TASK"]
                if any(gh == text.strip().upper() for gh in generic_headers) or any(gh in text.upper() for gh in ["CONFIDENTIAL", "COMPANY PROPRIETARY", "TABLE OF CONTENTS"]):
                    continue
                    
                # Look for explicit milestone patterns
                if "milestone" in text.lower() or "phase" in text.lower():
                    date_match = re.search(
                        r'(?i)(?:by|on|before|due|target)\s+(\d{1,2}\s+\w+\s+\d{4}|\d{4}-\d{2}-\d{2}|\d{1,2}/\d{1,2}/\d{2,4}|Q[1-4]\s+\d{4})',
                        text
                    )
                    extracted.append({
                        "type": "MILESTONE",
                        "raw_text": text,
                        "milestone_name": text[:50] + "..." if len(text) > 50 else text,
                        "deadline": date_match.group(1) if date_match else None,
                        "source_page": block.get("page_number"),
                        "source_section": section.get("section_name"),
                        "confidence": 0.8
                    })
                    
        return extracted
