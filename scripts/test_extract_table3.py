import sys, os, json, re
sys.path.insert(0, 'new_agent_2_BE')

def extract_payment_milestones_from_doc(docx_path):
    import docx
    doc = docx.Document(docx_path)
    payment_rows = []
    # Check all tables for payment milestones table (has 'invoices for fees' or 'Amount excl Tax')
    for table in doc.tables:
        header_text = ' '.join(c.text.lower() for c in table.rows[0].cells)
        if 'milestone' in header_text and ('invoice' in header_text or 'fee' in header_text or 'tax' in header_text):
            cur_module = "General"
            for r in table.rows[1:]:
                row_text = ' '.join(c.text.strip().replace('\n', ' ') for c in r.cells)
                row_l = row_text.lower()
                if 'total' in row_l:
                    continue
                if 'internal job posting' in row_l:
                    cur_module = "Internal Job Posting"
                    continue
                elif 'employee collaboration' in row_l:
                    cur_module = "Employee Collaboration"
                    continue
                
                # Check for milestone row (has percentage or implement/sign-off/hypercare and date)
                if any(k in row_l for k in ['implement', 'uat', 'sign-off', 'go-live', 'hyper care', 'hypercare']):
                    # extract date
                    d_match = re.search(r'(\d{1,2}(?:st|nd|rd|th)?\s+(?:january|february|march|april|may|june|july|august|september|october|november|december)\s+\d{4})', row_text, re.IGNORECASE)
                    pct_match = re.search(r'(\d+%)', row_text)
                    amt_cell = r.cells[-1].text.strip() if len(r.cells) >= 3 else ""
                    
                    # Convert date to YYYY-MM-DD
                    date_norm = None
                    if d_match:
                        from datetime import datetime
                        raw_d = re.sub(r'(\d+)(st|nd|rd|th)', r'\1', d_match.group(1))
                        try:
                            dt = datetime.strptime(raw_d, "%d %B %Y")
                            date_norm = dt.strftime("%Y-%m-%d")
                        except Exception:
                            pass
                    
                    stage_name = "Milestone Trigger"
                    if "implement" in row_l:
                        stage_name = "Implement & UAT Deployment (60%)"
                    elif "sign-off" in row_l or "go-live" in row_l:
                        stage_name = "UAT Sign-Off & Go-Live (30%)"
                    elif "hyper" in row_l:
                        stage_name = "Post Go-Live Hypercare Support (10%)"
                    
                    name = f"{cur_module}: {stage_name}"
                    payment_rows.append({
                        "name": name,
                        "milestone_normalized": name,
                        "module": cur_module,
                        "stage": stage_name,
                        "percentage": pct_match.group(1) if pct_match else None,
                        "planned_date": date_norm,
                        "date_provenance": "CONTRACT_CONFIRMED" if date_norm else "UNRESOLVED",
                        "fee_amount_inr": amt_cell,
                        "source_locator": "Table 3 / Payment Milestones",
                    })
    return payment_rows

res = extract_payment_milestones_from_doc('new_agent_2_BE/data/uploads/126_test-84/demo_EL_6ea5c474.docx')
print(f"Extracted {len(res)} payment milestones:")
for p in res:
    print(p)

