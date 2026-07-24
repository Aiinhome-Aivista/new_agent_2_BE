import os
import sys

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.document_service import DocumentService
from services.scope_section_detector import ScopeSectionDetector
from services.scope_candidate_extractor import ScopeCandidateExtractor

def main():
    doc_path = r"c:\Users\ADMIN\Desktop\Agent-2\test-1_pdfs\01_Engagement_Letter_EL.pdf"
    ext = ".pdf"
    
    print("1. Parsing document...")
    chunks = DocumentService.parse_document(doc_path, ext)
    print(f"Total chunks parsed: {len(chunks)}")
    
    print("2. Detecting sections...")
    chunks_with_sections = ScopeSectionDetector.detect_sections(chunks)
    
    section_counts = {}
    for c in chunks_with_sections:
        s = c.get("section")
        section_counts[s] = section_counts.get(s, 0) + 1
    print(f"Section distribution: {section_counts}")
    
    print("3. Extracting candidates...")
    candidates = ScopeCandidateExtractor.extract_candidates(chunks_with_sections, 1)
    print(f"Total candidates extracted: {len(candidates)}")
    for c in candidates:
        print(f" - {c['name']} (Section: {c['section']})")

if __name__ == "__main__":
    main()
