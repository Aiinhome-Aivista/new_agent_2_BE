import re, os
from datetime import datetime

def extract_payment_milestones(docx_path=None, full_text=""):
    payment_milestones = []
    
    # 1. Try reading directly from docx if available
    if docx_path and os.path.exists(docx_path) and docx_path.endswith('.docx'):
        try:
            import docx
            doc = docx.Document(docx_path)
            for table in doc.tables:
                header_text = ' '.join(c.text.lower() for c in table.rows[0].cells)
                if 'milestone' in header_text and ('invoice' in header_text or 'fee' in header_text or 'amount' in header_text or 'tax' in header_text):
                    cur_mod = "Employee Collaboration"
                    for r in table.rows[1:]:
                        row_text = ' '.join(c.text.strip().replace('\n', ' ') for c in r.cells)
                        row_l = row_text.lower()
                        if 'total' in row_l:
                            continue
                        if 'internal job posting' in row_l:
                            cur_mod = "Internal Job Posting"
                            continue
                        elif 'employee collaboration' in row_l:
                            cur_mod = "Employee Collaboration"
                            continue
                        
                        # Row with milestone percentage or phase
                        if any(k in row_l for k in ['implement', 'uat', 'sign-off', 'go-live', 'hyper care', 'hypercare']) and any(p in row_l for p in ['60%', '30%', '10%', '%']):
                            d_match = re.search(r'(\d{1,2}(?:st|nd|rd|th)?\s+(?:january|february|march|april|may|june|july|august|september|october|november|december)\s+\d{4})', row_text, re.IGNORECASE)
                            pct_match = re.search(r'(\d+)%', row_text)
                            amt_match = re.findall(r'[\d,]{4,}', row_text)
                            amt_val = amt_match[-1] if amt_match else ""
                            
                            date_norm = None
                            if d_match:
                                raw_d = re.sub(r'(\d+)(st|nd|rd|th)', r'\1', d_match.group(1))
                                try:
                                    dt = datetime.strptime(raw_d, "%d %B %Y")
                                    date_norm = dt.strftime("%Y-%m-%d")
                                except Exception:
                                    pass
                            
                            stage_name = "Milestone Trigger"
                            if "implement" in row_l:
                                stage_name = "Implement & UAT Deployment (60%)"
                            elif "hyper" in row_l:
                                stage_name = "Post Go-Live Hypercare Support (10%)"
                            elif "sign-off" in row_l or "go-live" in row_l:
                                stage_name = "UAT Sign-Off & Go-Live (30%)"
                            
                            name = f"{cur_mod}: {stage_name}"
                            if not any(pm["name"] == name for pm in payment_milestones):
                                payment_milestones.append({
                                    "name": name,
                                    "milestone_normalized": name,
                                    "module": cur_mod,
                                    "stage": stage_name,
                                    "percentage": f"{pct_match.group(1)}%" if pct_match else None,
                                    "planned_date": date_norm,
                                    "date_provenance": "CONTRACT_CONFIRMED" if date_norm else "UNRESOLVED",
                                    "fee_amount_inr": amt_val,
                                    "source_locator": "Table 3 / Payment Milestones",
                                })
        except Exception as e:
            print("Error parsing docx tables:", e)
            
    # 2. If docx wasn't available or didn't yield all rows, parse from text
    if len(payment_milestones) < 6 and full_text:
        lines = full_text.split("\n")
        cur_mod = "Employee Collaboration"
        for line in lines:
            line_s = line.strip()
            line_l = line_s.lower()
            if "internal job posting" in line_l:
                cur_mod = "Internal Job Posting"
            elif "employee collaboration" in line_l:
                cur_mod = "Employee Collaboration"
            if any(k in line_l for k in ["implement", "uat sign-off", "sign-off", "hyper care", "hypercare"]) and any(p in line_l for p in ["60%", "30%", "10%"]):
                d_match = re.search(r'(\d{1,2}(?:st|nd|rd|th)?\s+(?:january|february|march|april|may|june|july|august|september|october|november|december)\s+\d{4})', line_s, re.IGNORECASE)
                pct_match = re.search(r'(\d+)%', line_s)
                amt_match = re.findall(r'[\d,]{4,}', line_s)
                amt_val = amt_match[-1] if amt_match else ""
                
                date_norm = None
                if d_match:
                    raw_d = re.sub(r'(\d+)(st|nd|rd|th)', r'\1', d_match.group(1))
                    try:
                        dt = datetime.strptime(raw_d, "%d %B %Y")
                        date_norm = dt.strftime("%Y-%m-%d")
                    except Exception:
                        pass
                
                stage_name = "Milestone Trigger"
                if "implement" in line_l:
                    stage_name = "Implement & UAT Deployment (60%)"
                elif "hyper" in line_l:
                    stage_name = "Post Go-Live Hypercare Support (10%)"
                elif "sign-off" in line_l or "go-live" in line_l:
                    stage_name = "UAT Sign-Off & Go-Live (30%)"
                
                name = f"{cur_mod}: {stage_name}"
                if not any(pm["name"] == name for pm in payment_milestones):
                    payment_milestones.append({
                        "name": name,
                        "milestone_normalized": name,
                        "module": cur_mod,
                        "stage": stage_name,
                        "percentage": f"{pct_match.group(1)}%" if pct_match else None,
                        "planned_date": date_norm,
                        "date_provenance": "CONTRACT_CONFIRMED" if date_norm else "UNRESOLVED",
                        "fee_amount_inr": amt_val,
                        "source_locator": "Table 3 / Payment Milestones",
                    })

    return payment_milestones

res = extract_payment_milestones(docx_path="new_agent_2_BE/data/uploads/126_test-84/demo_EL_6ea5c474.docx")
print(f"Extracted {len(res)} payment milestones:")
for idx, r in enumerate(res, 1):
    print(f"{idx}. {r['name']} | Date: {r['planned_date']} | %: {r['percentage']} | INR: {r['fee_amount_inr']}")
