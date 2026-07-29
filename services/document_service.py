import os
# pyrefly: ignore [missing-import]
import fitz # PyMuPDF
import docx
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
            text = page.get_text("text")
            if text.strip():
                chunks = chunk_text(text)
                for chunk in chunks:
                    all_chunks.append({
                        "page_number": page_num + 1,
                        "text": chunk,
                        "chunk_index": chunk_idx
                    })
                    chunk_idx += 1
        return all_chunks

    @staticmethod
    def process_docx(file_path: str) -> List[Dict[str, Any]]:
        doc = Document(file_path)
        full_text = []
        for element in doc.element.body:
            if element.tag.endswith('p'):
                para = docx.text.paragraph.Paragraph(element, doc)
                if para.text.strip():
                    full_text.append(para.text)
            elif element.tag.endswith('tbl'):
                table = docx.table.Table(element, doc)
                for row in table.rows:
                    row_text = "\t".join([cell.text.strip().replace("\n", " ") for cell in row.cells])
                    if row_text.strip():
                        full_text.append(row_text)
                        
        chunks = chunk_text("\n".join(full_text))
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
