import os
# pyrefly: ignore [missing-import]
import fitz # PyMuPDF
from docx import Document
from typing import List, Dict, Any
from core.config import settings
import uuid
# pyrefly: ignore [missing-import]
from langchain_text_splitters import RecursiveCharacterTextSplitter

def chunk_text(text: str) -> List[str]:
    text_splitter = RecursiveCharacterTextSplitter(
        chunk_size=settings.CHUNK_SIZE,
        chunk_overlap=settings.CHUNK_OVERLAP,
        separators=["\n\n", "\n", ". ", " ", ""]
    )
    
    return text_splitter.split_text(text)

import re

class DocumentService:
    @staticmethod
    def clean_contract_text(text: str) -> str:
        """Strips out mathematical junk and standard legal boilerplate to save LLM tokens."""
        # Remove massive whitespace blocks
        text = re.sub(r'\n{3,}', '\n\n', text)
        
        # Remove standard legal boilerplate sections (simplified regex for demo purposes)
        boilerplate_patterns = [
            r"(?i)(Limitation of Liability[\s\S]{100,500}?(?=\n\n|\Z))",
            r"(?i)(Governing Law[\s\S]{50,300}?(?=\n\n|\Z))",
            r"(?i)(Severability[\s\S]{50,300}?(?=\n\n|\Z))",
            r"(?i)(Force Majeure[\s\S]{50,300}?(?=\n\n|\Z))",
            r"(?i)(Table of Contents[\s\S]{50,1000}?(?=\n\n[A-Z]))"
        ]
        
        for pattern in boilerplate_patterns:
            text = re.sub(pattern, "", text)
            
        return text.strip()

    @staticmethod
    def process_txt(file_path: str) -> List[Dict[str, Any]]:
        with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
            text = f.read()
        
        chunks = chunk_text(text)
        return [{"page_number": None, "text": chunk, "chunk_index": idx} for idx, chunk in enumerate(chunks)]

    @staticmethod
    def process_pdf(file_path: str) -> List[Dict[str, Any]]:
        doc = fitz.open(file_path)
        all_chunks = []
        chunk_idx = 0
        
        for page_num in range(len(doc)):
            page = doc.load_page(page_num)
            
            # Step 1: Extract tables first (PyMuPDF 1.23.0+)
            table_texts = set()
            try:
                tables = page.find_tables()
                for table in tables:
                    extracted_rows = table.extract()
                    if not extracted_rows:
                        continue
                    
                    # Assume first row is header if not empty
                    headers = [str(c).strip() if c else "" for c in extracted_rows[0]]
                    rows = []
                    for row in extracted_rows[1:]:
                        cells = [str(c).strip() if c else "" for c in row]
                        if any(cells):
                            rows.append(cells)
                            row_text = " | ".join(cells)
                            table_texts.add(row_text)
                            
                    all_chunks.append({
                        "page_number": page_num + 1,
                        "chunk_index": chunk_idx,
                        "block_type": "table",
                        "headers": headers,
                        "rows": rows,
                        "text": str(headers) + " " + str(rows) # fallback for legacy
                    })
                    chunk_idx += 1
            except Exception:
                pass  # find_tables() not available in older PyMuPDF versions
            
            # Step 2: Extract text blocks with font metadata
            try:
                page_dict = page.get_text("dict", flags=fitz.TEXT_PRESERVE_WHITESPACE)
                blocks = page_dict.get("blocks", [])
                
                # Calculate median font size for heading detection
                all_sizes = []
                for block in blocks:
                    if block.get("type") == 0:  # text block
                        for line in block.get("lines", []):
                            for span in line.get("spans", []):
                                if span.get("text", "").strip():
                                    all_sizes.append(span["size"])
                
                median_size = sorted(all_sizes)[len(all_sizes) // 2] if all_sizes else 12.0
                heading_threshold = median_size * 1.15  # 15% larger = heading
                
                for block in blocks:
                    if block.get("type") != 0:  # skip image blocks
                        continue
                    
                    block_lines = []
                    block_type = "paragraph"
                    
                    for line in block.get("lines", []):
                        line_text = ""
                        is_bold = False
                        is_large = False
                        
                        for span in line.get("spans", []):
                            span_text = span.get("text", "")
                            line_text += span_text
                            
                            # Detect heading characteristics
                            if span.get("size", 12) >= heading_threshold:
                                is_large = True
                            flags = span.get("flags", 0)
                            if flags & 2 ** 4:  # bold flag
                                is_bold = True
                        
                        line_text = line_text.strip()
                        if not line_text:
                            continue
                        
                        # Skip lines already captured as table rows
                        if any(line_text in tt for tt in table_texts):
                            continue
                            
                        # Classify the line
                        is_upper = line_text.isupper() and len(line_text) > 4
                        is_numbered_heading = bool(re.match(r'^(?:Phase \d+|Step \d+|Task \d+)[-:\s]', line_text, re.IGNORECASE))
                        
                        if (is_large or is_bold or is_upper or is_numbered_heading) and len(line_text) < 120:
                            block_type = "heading"
                        elif block_type != "heading":
                            if re.match(r'^[\-\•\*\u2022\u25CF\u25CB\u25AA\u25A0○●◆►▪]\s+', line_text):
                                block_type = "bullet"
                            elif re.match(r'^[0-9]+(\.[0-9]+)*[\.\)]\s+', line_text):
                                block_type = "bullet"
                            elif re.match(r'^[a-zA-Z][\.\)]\s+', line_text):
                                block_type = "bullet"
                        
                        block_lines.append(line_text)
                    
                    combined = "\n".join(block_lines).strip()
                    if not combined:
                        continue
                    
                    # For long blocks, chunk them; for short ones, keep as-is
                    if len(combined) > settings.CHUNK_SIZE:
                        sub_chunks = chunk_text(combined)
                        for sub in sub_chunks:
                            all_chunks.append({
                                "page_number": page_num + 1,
                                "text": sub,
                                "chunk_index": chunk_idx,
                                "block_type": block_type
                            })
                            chunk_idx += 1
                    else:
                        all_chunks.append({
                            "page_number": page_num + 1,
                            "text": combined,
                            "chunk_index": chunk_idx,
                            "block_type": block_type
                        })
                        chunk_idx += 1
                        
            except Exception as e:
                # Fallback: legacy plain text extraction if structured fails
                print(f"Structured PDF extraction failed on page {page_num + 1}, falling back to plain text: {e}")
                text = page.get_text("text")
                if text.strip():
                    sub_chunks = chunk_text(text)
                    for sub in sub_chunks:
                        all_chunks.append({
                            "page_number": page_num + 1,
                            "text": sub,
                            "chunk_index": chunk_idx,
                            "block_type": "paragraph"
                        })
                        chunk_idx += 1
        
        # Safety: If structured extraction produced nothing, do a full legacy pass
        if not all_chunks:
            chunk_idx = 0
            for page_num in range(len(doc)):
                page = doc.load_page(page_num)
                text = page.get_text("text")
                if text.strip():
                    sub_chunks = chunk_text(text)
                    for sub in sub_chunks:
                        all_chunks.append({
                            "page_number": page_num + 1,
                            "text": sub,
                            "chunk_index": chunk_idx,
                            "block_type": "paragraph"
                        })
                        chunk_idx += 1
        
        return all_chunks

    @staticmethod
    def process_docx(file_path: str) -> List[Dict[str, Any]]:
        doc = Document(file_path)
        full_text = "\n".join([para.text for para in doc.paragraphs if para.text.strip()])
        chunks = chunk_text(full_text)
        return [{"page_number": None, "text": chunk, "chunk_index": idx} for idx, chunk in enumerate(chunks)]

    @staticmethod
    def parse_document(file_path: str, ext: str) -> List[Dict[str, Any]]:
        if ext == '.pdf':
            return DocumentService.process_pdf(file_path)
        elif ext == '.docx':
            return DocumentService.process_docx(file_path)
        elif ext == '.txt':
            return DocumentService.process_txt(file_path)
        else:
            raise ValueError(f"Unsupported extension: {ext}")
