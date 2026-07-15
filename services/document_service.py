import os
import fitz # PyMuPDF
from docx import Document
from typing import List, Dict, Any
from core.config import settings
import uuid

def chunk_text(text: str) -> List[str]:
    # A simple token/character based chunker
    size = settings.CHUNK_SIZE
    overlap = settings.CHUNK_OVERLAP
    chunks = []
    i = 0
    while i < len(text):
        chunks.append(text[i:i + size])
        i += size - overlap
    return [c for c in chunks if c.strip()]

class DocumentService:
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
