import os
import sys
import re

base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, base_dir)

from services.document_service import DocumentService
from services.milestone_dependency_service import MilestoneDependencyService

doc_path = os.path.abspath(os.path.join(os.path.dirname(base_dir), "demo_EL.docx"))
full_md = DocumentService.convert_to_markdown(doc_path)

# Extract Payment Milestones from Table 3 text
lines = full_md.split("\n")
current_module = "General"
payment_milestones = []

for line in lines:
    line_clean = line.strip()
    if not line_clean:
        continue
    # Detect module headings in or near payment tables
    m_head = re.search(r"vConnect\s+Portal\s+Implementation:?\s*([A-Za-z\s]+)", line_clean, re.IGNORECASE)
    if m_head:
        mod_name = m_head.group(1).strip(" |#*-\t")
        if mod_name and "total" not in mod_name.lower():
            current_module = mod_name
    
    # Check for payment milestone row: e.g. | 1. | Implement & UAT Deployment (60% of milestone fees) on 12th February 2027 | 11,10,000 |
    if re.search(r"\b(Implement|UAT Sign-Off|Hyper\s*care)\b", line_clean, re.IGNORECASE) and re.search(r"\d{1,2}(?:st|nd|rd|th)?\s+[A-Za-z]+\s+\d{4}", line_clean):
        # Extract percentage
        pct_m = re.search(r"\((\d+)%\s+of\s+milestone\s+fees\)", line_clean, re.IGNORECASE)
        pct = int(pct_m.group(1)) if pct_m else None
        
        # Extract date
        dt_m = re.search(r"on\s+(\d{1,2}(?:st|nd|rd|th)?\s+[A-Za-z]+\s+\d{4})", line_clean, re.IGNORECASE)
        dt_str = dt_m.group(1) if dt_m else None
        from services.deadline_inference_service import DeadlineInferenceService
        iso_date = DeadlineInferenceService.parse_date_string(dt_str) if dt_str else None
        
        # Extract amount
        amt_m = re.findall(r"\|\s*([\d,]+)\s*\|", line_clean)
        amount = amt_m[-1].replace(",", "") if amt_m else None
        
        # Clean event name
        clean_name = line_clean.split("|")[2].strip() if len(line_clean.split("|")) >= 3 else line_clean
        # Remove date and percentage from name
        event_label = re.sub(r"\(\d+%\s+of\s+milestone\s+fees\)", "", clean_name)
        event_label = re.sub(r"on\s+\d{1,2}(?:st|nd|rd|th)?\s+[A-Za-z]+\s+\d{4}", "", event_label).strip(" -*#\t")
        
        full_name = f"{current_module}: {event_label}"
        payment_milestones.append({
            "module": current_module,
            "event_label": event_label,
            "name": full_name,
            "percentage": pct,
            "planned_date": iso_date,
            "amount": amount,
            "currency": "INR",
            "source_quotation": line_clean.strip(" |"),
            "date_provenance": "CONTRACT_CONFIRMED",
        })

print(f"Extracted {len(payment_milestones)} payment milestones:")
for idx, pm in enumerate(payment_milestones, 1):
    print(f"[{idx}] {pm['name']} | Date: {pm['planned_date']} | Pct: {pm['percentage']}% | Amt: {pm['amount']}")

# Build module DAG edges
edges = []
mod1_ms = [i + 1 for i, m in enumerate(payment_milestones) if m["module"] == "Employee Collaboration"]
mod2_ms = [i + 1 for i, m in enumerate(payment_milestones) if m["module"] == "Internal Job Posting"]

for i in range(len(mod1_ms) - 1):
    edges.append((mod1_ms[i], mod1_ms[i + 1]))
for i in range(len(mod2_ms) - 1):
    edges.append((mod2_ms[i], mod2_ms[i + 1]))

print("DAG edges:", edges)
MilestoneDependencyService.validate_dag(edges)
print("DAG Validation PASSED! No cycles, no self-loops!")

