from typing import Dict, Any, List

class FunctionalScopeExtractor:
    """
    Extracts functional scope items from sections classified as FUNCTIONAL_SCOPE.
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
                # For tables, we create a JSON object for metadata_json
                headers = block.get("headers", [])
                import json
                for row in block.get("rows", []):
                    # Simple heuristic: ignore empty rows
                    if not any(row):
                        continue
                    
                    row_obj = {}
                    row_text_parts = []
                    for idx, cell in enumerate(row):
                        if cell:
                            header = headers[idx] if idx < len(headers) else f"Col{idx}"
                            row_obj[header] = cell
                            row_text_parts.append(f"{header}: {cell}")
                    
                    if row_obj:
                        extracted.append({
                            "type": "FUNCTIONAL_SCOPE",
                            "raw_text": " | ".join(row_text_parts),
                            "metadata_json": json.dumps(row_obj, ensure_ascii=False),
                            "source_page": block.get("page_number"),
                            "source_section": section.get("section_name"),
                            "confidence": 0.8
                        })
            
            elif b_type == "bullet":
                # Bulleted items are typically atomic modules/features
                extracted.append({
                    "type": "FUNCTIONAL_SCOPE",
                    "raw_text": text,
                    "source_page": block.get("page_number"),
                    "source_section": section.get("section_name"),
                    "confidence": 0.95  # High confidence for bullets
                })
            elif b_type == "paragraph":
                import re
                
                # Validation: reject generic headers
                generic_headers = ["CONFIDENTIAL", "COMPANY PROPRIETARY", "PAGE", "TABLE OF CONTENTS", "INDEX", "MODULE NAME", "PHASE", "TASK DESCRIPTION", "DEADLINE", "DURATION", "DELIVERABLE / OUTPUT"]
                if any(gh in text.upper() for gh in generic_headers) or len(text) < 15:
                    continue
                
                # Check for numbered list inside paragraph text e.g. "1. Feature A\n2. Feature B"
                import re
                lines = text.split('\n')
                atomic_features = []
                for line in lines:
                    line = line.strip()
                    if not line:
                        continue
                    if re.match(r'^[\d\w][\.\)]\s+', line) or re.match(r'^[-*]\s+', line):
                        atomic_features.append((line, 0.9)) # Treat as bullet
                        continue
                    
                    # Split sentences for atomic features instead of large summaries
                    sentences = re.split(r'(?<=[.!?])\s+', line)
                    for sentence in sentences:
                        sentence = sentence.strip()
                        if len(sentence) < 15:
                            continue
                            
                        # Check for module/portal keywords
                        is_module = bool(re.search(r'(?i)(portal|module|authentication|dashboard|system|platform|management)', sentence))
                        # Check for action verbs
                        has_verb = bool(re.search(r'(?i)\b(develop|implement|build|create|integrate|design|setup|configure|support|allow|enable|provide)\b', sentence))
                        
                        if len(sentence.split()) < 15 and is_module:
                            confidence = 0.8
                        elif has_verb:
                            confidence = 0.7
                        elif len(sentence.split()) > 30:
                            confidence = 0.4
                        else:
                            confidence = 0.5
                            
                        atomic_features.append((sentence, confidence))
                
                for feature_text, conf in atomic_features:
                    extracted.append({
                        "type": "FUNCTIONAL_SCOPE",
                        "raw_text": feature_text,
                        "source_page": block.get("page_number"),
                        "source_section": section.get("section_name"),
                        "confidence": conf
                    })
                    
        return extracted
