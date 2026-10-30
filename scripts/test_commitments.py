import os
import sys
import re

base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, base_dir)

from services.document_service import DocumentService

doc_path = os.path.abspath(os.path.join(os.path.dirname(base_dir), "demo_EL.docx"))
full_md = DocumentService.convert_to_markdown(doc_path)

m_sla = re.search(
    r"(Once a deliverable has been submitted[^\n]+?deemed accepted\.)",
    full_md,
    re.IGNORECASE | re.DOTALL
)
if m_sla:
    print("MATCHED SLA VERBATIM:")
    print(m_sla.group(1).strip())
else:
    print("SLA not matched, checking 'feedback from client'")
    for line in full_md.split("\n"):
        if "feedback from client" in line.lower():
            print("Found line:", line)
