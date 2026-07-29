from typing import Dict, Any, List

class ClientDependencyExtractor:
    """
    Extracts client responsibilities from sections classified as CLIENT_DEPENDENCY.
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
                
            if b_type == "bullet":
                extracted.append({
                    "type": "CLIENT_DEPENDENCY",
                    "raw_text": text,
                    "source_page": block.get("page_number"),
                    "source_section": section.get("section_name"),
                    "confidence": 0.9
                })
            elif b_type == "paragraph":
                if len(text.split()) > 4:
                    extracted.append({
                        "type": "CLIENT_DEPENDENCY",
                        "raw_text": text,
                        "source_page": block.get("page_number"),
                        "source_section": section.get("section_name"),
                        "confidence": 0.7
                    })
            elif b_type == "table":
                for row in block.get("rows", []):
                    if not any(row):
                        continue
                    row_text = " | ".join(c for c in row if c)
                    extracted.append({
                        "type": "CLIENT_DEPENDENCY",
                        "raw_text": row_text,
                        "source_page": block.get("page_number"),
                        "source_section": section.get("section_name"),
                        "confidence": 0.8
                    })
                    
        return extracted
