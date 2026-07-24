import os
import sys

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from services.document_service import DocumentService

def main():
    doc_path = r"c:\Users\ADMIN\Desktop\Agent-2\test-1_pdfs\01_Engagement_Letter_EL.pdf"
    ext = ".pdf"
    
    chunks = DocumentService.parse_document(doc_path, ext)
    for i, chunk in enumerate(chunks):
        print(f"--- CHUNK {i} ---")
        print(chunk["text"])

if __name__ == "__main__":
    main()
