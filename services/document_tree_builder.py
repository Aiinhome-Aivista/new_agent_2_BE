import re
from typing import List, Dict, Any

class DocumentTreeBuilder:
    @staticmethod
    def detect_document_type(blocks: List[Dict[str, Any]]) -> str:
        """Phase 0: Classify the document based on early text."""
        # Check the first few blocks (e.g. first page) for keywords
        intro_text = " ".join([b.get("text", "") for b in blocks[:20]])
        intro_text = intro_text.lower()
        
        if "statement of work" in intro_text or "sow" in intro_text:
            return "SOW"
        elif "master services agreement" in intro_text or "msa" in intro_text:
            return "MSA"
        elif "engagement letter" in intro_text:
            return "ENGAGEMENT_LETTER"
        elif "proposal" in intro_text:
            return "PROPOSAL"
        elif "contract" in intro_text or "agreement" in intro_text:
            return "CONTRACT"
            
        return "UNKNOWN"

    @staticmethod
    def build_tree(blocks: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Phase 1: Build a hierarchical Document Tree from flat layout blocks."""
        doc_type = DocumentTreeBuilder.detect_document_type(blocks)
        
        sections = []
        current_section = None
        
        for block in blocks:
            b_type = block.get("block_type")
            text = block.get("text", "").strip()
            
            if not text and b_type != "table":
                continue
                
            if b_type == "heading":
                # Save previous section if exists
                if current_section and current_section["content_blocks"]:
                    sections.append(current_section)
                    
                # Start new section
                current_section = {
                    "section_name": text,
                    "level": 1,  # Can be expanded for nested subheadings later
                    "content_blocks": []
                }
            else:
                # If no heading was found yet, create a default Introduction section
                if not current_section:
                    current_section = {
                        "section_name": "Introduction",
                        "level": 1,
                        "content_blocks": []
                    }
                
                # Append to current section
                clean_block = {
                    "type": b_type,
                    "text": text,
                    "page_number": block.get("page_number"),
                    "chunk_index": block.get("chunk_index")
                }
                
                if b_type == "table":
                    clean_block["headers"] = block.get("headers", [])
                    clean_block["rows"] = block.get("rows", [])
                    
                current_section["content_blocks"].append(clean_block)
                
        # Append the last section
        if current_section and current_section["content_blocks"]:
            sections.append(current_section)
            
        return {
            "document_type": doc_type,
            "sections": sections
        }
