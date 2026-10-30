import os
import sys

base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, base_dir)

from services.document_service import DocumentService
from services.deadline_inference_service import DeadlineInferenceService

doc_path = os.path.abspath(os.path.join(os.path.dirname(base_dir), "demo_EL.docx"))
full_md = DocumentService.convert_to_markdown(doc_path)
facts = DeadlineInferenceService.extract_timeline_facts(full_md)
print("Contract dates:", {k: v for k, v in facts.items() if "date" in k or "weeks" in k})
print(f"Total milestone anchors found: {len(facts['milestone_anchors'])}")
for a in facts["milestone_anchors"]:
    print("Anchor:", a)

