from typing import Dict, Any, List

class TechStackExtractor:
    """
    Extracts technology stack items from sections classified as TECH_STACK.
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
                
            if b_type in ["bullet", "paragraph"]:
                generic_headers = ["CONFIDENTIAL", "COMPANY PROPRIETARY", "PAGE", "TABLE OF CONTENTS", "INDEX", "MODULE NAME", "PHASE", "TASK DESCRIPTION", "DEADLINE", "DURATION", "DELIVERABLE / OUTPUT", "DELIVERABLE", "OUTPUT", "OWNER", "TASK", "COMPONENT", "TECHNOLOGY"]
                if any(gh == text.strip().upper() for gh in generic_headers) or any(gh in text.upper() for gh in ["CONFIDENTIAL", "COMPANY PROPRIETARY", "TABLE OF CONTENTS"]):
                    continue
                    
                # If it's a paragraph, it might be comma separated "React, Node.js, AWS"
                extracted.append({
                    "type": "TECH_STACK",
                    "raw_text": text,
                    "source_page": block.get("page_number"),
                    "source_section": section.get("section_name"),
                    "confidence": 0.9
                })
            elif b_type == "table":
                import json
                headers = block.get("headers", [])
                for row in block.get("rows", []):
                    if not any(row):
                        continue
                        
                    row_obj = {}
                    row_text_parts = []
                    for idx, cell in enumerate(row):
                        if cell:
                            header = headers[idx] if idx < len(headers) else f"Col{idx}"
                            row_obj[header] = cell
                            row_text_parts.append(f"{header}: {cell}")
                            
                    description = " | ".join([c for c in row if c])
                    generic_headers = ["CONFIDENTIAL", "COMPANY PROPRIETARY", "PAGE", "TABLE OF CONTENTS", "INDEX", "MODULE NAME", "PHASE", "TASK DESCRIPTION", "DEADLINE", "DURATION", "DELIVERABLE / OUTPUT", "DELIVERABLE", "OUTPUT", "OWNER", "TASK", "COMPONENT", "TECHNOLOGY"]
                    if any(gh == description.strip().upper() for gh in generic_headers) or any(gh in description.upper() for gh in ["CONFIDENTIAL", "COMPANY PROPRIETARY", "TABLE OF CONTENTS"]):
                        continue
                        
                    if row_obj:
                        extracted.append({
                            "type": "TECH_STACK",
                            "raw_text": " | ".join(row_text_parts),
                            "metadata_json": json.dumps(row_obj, ensure_ascii=False),
                            "source_page": block.get("page_number"),
                            "source_section": section.get("section_name"),
                            "confidence": 0.8
                        })
                    
        return extracted
