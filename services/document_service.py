import os
import re
from typing import List, Dict, Any
from core.config import settings

# pyrefly: ignore [missing-import]
import fitz # PyMuPDF
import docx
from docx import Document

# pyrefly: ignore [missing-import]
from langchain_text_splitters import RecursiveCharacterTextSplitter, MarkdownHeaderTextSplitter

# Optional import of Microsoft MarkItDown for universal document to Markdown conversion
try:
    # pyrefly: ignore [missing-import]
    from markitdown import MarkItDown
    MARKITDOWN_AVAILABLE = True
except ImportError:
    MARKITDOWN_AVAILABLE = False

# Lazy flag for docling to avoid 15-20s top-level PyTorch import penalty
DOCLING_AVAILABLE = None

def chunk_text(text: str) -> List[str]:
    text_splitter = RecursiveCharacterTextSplitter(
        chunk_size=settings.CHUNK_SIZE,
        chunk_overlap=settings.CHUNK_OVERLAP,
        separators=["\n\n", "\n", ". ", " ", ""]
    )
    return text_splitter.split_text(text)


class DocumentService:
    _docling_converter = None
    _markitdown_converter = None

    @classmethod
    def get_markitdown_converter(cls):
        """Lazy initializer for Microsoft MarkItDown converter."""
        if not MARKITDOWN_AVAILABLE:
            return None
        if cls._markitdown_converter is None:
            try:
                cls._markitdown_converter = MarkItDown()
            except Exception as e:
                print(f"Warning: Failed to initialize MarkItDown ({e}).")
                cls._markitdown_converter = None
        return cls._markitdown_converter

    @classmethod
    def get_docling_converter(cls):
        """Lazy initializer for Docling DocumentConverter with TableFormer enabled."""
        global DOCLING_AVAILABLE
        if DOCLING_AVAILABLE is False:
            return None

        if cls._docling_converter is None:
            try:
                from docling.document_converter import DocumentConverter, PdfFormatOption
                from docling.datamodel.pipeline_options import PdfPipelineOptions, TableFormerMode
                DOCLING_AVAILABLE = True
                pipeline_options = PdfPipelineOptions()
                pipeline_options.do_table_structure = True
                pipeline_options.table_structure_options.mode = TableFormerMode.ACCURATE
                cls._docling_converter = DocumentConverter(
                    format_options={
                        "pdf": PdfFormatOption(pipeline_options=pipeline_options)
                    }
                )
            except ImportError:
                DOCLING_AVAILABLE = False
                return None
            except Exception as e:
                print(f"Warning: Failed to initialize Docling pipeline with custom options ({e}). Falling back to default DocumentConverter.")
                try:
                    from docling.document_converter import DocumentConverter
                    cls._docling_converter = DocumentConverter()
                    DOCLING_AVAILABLE = True
                except Exception as ex:
                    print(f"Error initializing default Docling converter: {ex}")
                    DOCLING_AVAILABLE = False
                    cls._docling_converter = None
        return cls._docling_converter

    @staticmethod
    def clean_contract_text(text: str) -> str:
        """Strips out mathematical junk and standard legal boilerplate to save LLM tokens."""
        text = re.sub(r'\n{3,}', '\n\n', text)
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

    @classmethod
    def convert_to_markdown(cls, file_path: str, ext: str = None) -> str:
        """
        Converts a document (PDF, DOCX, TXT) into structured Markdown format using MarkItDown.
        Falls back to Docling/PyMuPDF (for PDF) or python-docx (for DOCX) if MarkItDown
        encounters an error or returns empty content (e.g. scanned PDFs).
        Also writes an intermediate .md file for debugging and auditing if the directory is writable.
        """
        if ext is None:
            ext = os.path.splitext(file_path)[1].lower()
        else:
            ext = ext.lower()

        markdown_text = ""
        converter = cls.get_markitdown_converter()

        if converter and ext in ['.pdf', '.docx']:
            try:
                print(f"[DocumentService] Converting {ext} with MarkItDown: {os.path.basename(file_path)}")
                conv_result = converter.convert(file_path)
                if conv_result and conv_result.text_content:
                    markdown_text = conv_result.text_content.strip()
            except Exception as e:
                print(f"[DocumentService] MarkItDown conversion error on {file_path}: {e}")
                markdown_text = ""

        # Fallback if MarkItDown extracted insufficient content (e.g., scanned PDF or format error)
        if not markdown_text or len(markdown_text.strip()) < 50:
            if ext == '.pdf':
                print(f"[DocumentService] MarkItDown produced insufficient text for PDF. Running Docling/PyMuPDF fallback.")
                docling = cls.get_docling_converter()
                if docling:
                    try:
                        conv = docling.convert(file_path)
                        markdown_text = conv.document.export_to_markdown()
                    except Exception as ex:
                        print(f"[DocumentService] Docling fallback error: {ex}")
                if not markdown_text:
                    fallback_chunks = cls.process_pdf_fallback(file_path)
                    markdown_text = "\n\n".join([c["text"] for c in fallback_chunks if c.get("text")])
            elif ext == '.docx':
                print(f"[DocumentService] Running python-docx fallback for DOCX.")
                fallback_chunks = cls.process_docx(file_path)
                markdown_text = "\n\n".join([c["text"] for c in fallback_chunks if c.get("text")])
            elif ext == '.txt':
                fallback_chunks = cls.process_txt(file_path)
                markdown_text = "\n\n".join([c["text"] for c in fallback_chunks if c.get("text")])

        # Strip standard boilerplate
        cleaned_markdown = cls.clean_contract_text(markdown_text)

        # Attempt to save intermediate .md file alongside original file for caching/inspection
        try:
            base_dir = os.path.dirname(file_path)
            base_name = os.path.splitext(os.path.basename(file_path))[0]
            if base_dir and os.path.exists(base_dir):
                md_path = os.path.join(base_dir, f"{base_name}.md")
                with open(md_path, "w", encoding="utf-8") as f:
                    f.write(cleaned_markdown)
        except Exception:
            pass

        return cleaned_markdown

    @classmethod
    def process_markdown_into_chunks(cls, markdown_text: str) -> List[Dict[str, Any]]:
        """
        Splits Markdown text using header-aware splitting (H1, H2, H3),
        preserving header context in each chunk for ScopeSectionDetector.
        """
        if not markdown_text or not markdown_text.strip():
            return []

        headers_to_split_on = [("#", "H1"), ("##", "H2"), ("###", "H3")]
        try:
            md_splitter = MarkdownHeaderTextSplitter(headers_to_split_on=headers_to_split_on)
            header_splits = md_splitter.split_text(markdown_text)

            all_chunks = []
            chunk_idx = 0
            for split in header_splits:
                # Prepend header titles back to chunk text so section detectors find section headings
                header_titles = [f"# {v}" for k, v in split.metadata.items() if v]
                header_prefix = "\n".join(header_titles) if header_titles else ""

                sub_chunks = chunk_text(split.page_content)
                for chunk in sub_chunks:
                    full_text = f"{header_prefix}\n\n{chunk}" if header_prefix else chunk
                    all_chunks.append({
                        "page_number": None,
                        "text": full_text.strip(),
                        "chunk_index": chunk_idx,
                        "metadata": split.metadata
                    })
                    chunk_idx += 1
            if all_chunks:
                return all_chunks
        except Exception as e:
            print(f"Notice: Header-aware markdown splitting fallback to standard chunking: {e}")

        chunks = chunk_text(markdown_text)
        return [{"page_number": None, "text": chunk, "chunk_index": idx} for idx, chunk in enumerate(chunks)]

    @staticmethod
    def process_txt(file_path: str) -> List[Dict[str, Any]]:
        with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
            text = f.read()
        chunks = chunk_text(text)
        return [{"page_number": None, "text": chunk, "chunk_index": idx} for idx, chunk in enumerate(chunks)]

    @classmethod
    def process_pdf_docling(cls, file_path: str) -> List[Dict[str, Any]]:
        """Parses PDF using IBM Docling into structured Markdown with layout & table awareness."""
        converter = cls.get_docling_converter()
        if not converter:
            raise RuntimeError("Docling is not installed or initialized.")

        conv_result = converter.convert(file_path)
        markdown_text = conv_result.document.export_to_markdown()

        if not markdown_text or not markdown_text.strip():
            raise ValueError("Docling extracted empty text.")

        return cls.process_markdown_into_chunks(markdown_text)

    @staticmethod
    def process_pdf_fallback(file_path: str) -> List[Dict[str, Any]]:
        """Fallback PDF parser using PyMuPDF (fitz) with table detection."""
        doc = fitz.open(file_path)
        all_chunks = []
        chunk_idx = 0

        for page_num in range(len(doc)):
            page = doc.load_page(page_num)

            # Extract tables as markdown grids if present
            table_mds = []
            try:
                tabs = page.find_tables()
                if tabs and tabs.tables:
                    for tab in tabs.tables:
                        table_mds.append(tab.to_markdown())
            except Exception:
                pass

            text = page.get_text("text") or ""
            if table_mds:
                text += "\n\n### Extracted Tables:\n" + "\n\n".join(table_mds)

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

    @classmethod
    def process_pdf(cls, file_path: str) -> List[Dict[str, Any]]:
        """Main PDF processing entry point using MarkItDown with Docling -> PyMuPDF fallback."""
        return cls.parse_document(file_path, '.pdf')

    @staticmethod
    def process_docx(file_path: str) -> List[Dict[str, Any]]:
        """Fallback DOCX parser using python-docx."""
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

    @classmethod
    def parse_document(cls, file_path: str, ext: str) -> List[Dict[str, Any]]:
        """
        Main document parsing entry point.
        Converts file (PDF, DOCX, TXT) into clean Markdown via MarkItDown (with intelligent fallbacks),
        then chunks into header-aware segments for baseline extraction and RAG indexing.
        """
        ext_lower = ext.lower()
        if ext_lower not in ['.pdf', '.docx', '.txt']:
            raise ValueError(f"Unsupported extension: {ext}")

        markdown_text = cls.convert_to_markdown(file_path, ext_lower)
        return cls.process_markdown_into_chunks(markdown_text)

    @classmethod
    def extract_full_text(cls, file_path: str, ext: str) -> str:
        """Extracts complete formatted document markdown as a single string."""
        return cls.convert_to_markdown(file_path, ext)
