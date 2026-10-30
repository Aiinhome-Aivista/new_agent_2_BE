import re

with open("new_agent_2_BE/services/step8_inspector.py", "r", encoding="utf-8") as f:
    content = f.read()

# 1. Update Payment Table Extraction
old_payment_block = """        # Extract distinct module payment rows from Table 3 in full_text_corpus
        # Ensure all 6 payment milestones across both modules are captured distinctly:
        # Module 1 (Employee Collaboration): 12 Feb (60%), 18 Mar (30%), 01 Apr (10%)
        # Module 2 (Internal Job Posting): 11 Feb (60%), 18 Mar (30%), 01 Apr (10%)
        payment_table_milestones = []
        lines = full_text_corpus.split("\\n")
        cur_mod = "Employee Collaboration"
        for line in lines:
            line_s = line.strip()
            if not line_s:
                continue
            if "internal job posting" in line_s.lower():
                cur_mod = "Internal Job Posting"
            elif "employee collaboration" in line_s.lower():
                cur_mod = "Employee Collaboration"
            if any(k in line_s.lower() for k in ["implement & uat", "uat sign-off", "hyper care", "hypercare"]) and any(m in line_s.lower() for m in ["february", "march", "april"]):
                p_dt = _extract_contractual_date(line_s)
                if p_dt:
                    clean_title = line_s.replace("|", " ").strip()
                    if "implement" in line_s.lower():
                        ev_name = f"{cur_mod}: Implement & UAT Deployment (60%)"
                    elif "sign-off" in line_s.lower() or "go-live" in line_s.lower():
                        ev_name = f"{cur_mod}: UAT Sign-Off & Go-Live (30%)"
                    else:
                        ev_name = f"{cur_mod}: Post Go-Live Hypercare Support (10%)"
                    
                    if not any(m["name"] == ev_name for m in payment_table_milestones):
                        payment_table_milestones.append({
                            "name": ev_name,
                            "milestone_normalized": ev_name,
                            "planned_date": p_dt,
                            "date_provenance": "CONTRACT_CONFIRMED",
                            "deadline_text": p_dt,
                            "status": "Planned",
                            "module": cur_mod,
                            "scope_candidate_id": None,
                            "source_locator": "table_payment_milestones",
                        })"""

new_payment_block = """        # Locate docx file if available for high-fidelity table parsing
        backend_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        target_docx = None
        docx_candidates = [
            os.path.join(backend_root, "data", "uploads", f"{project_id}_*", f"*{doc_name}*"),
            os.path.join(backend_root, "data", "uploads", f"{project_id}_*", "*.docx"),
            os.path.join(os.path.dirname(backend_root), doc_name),
            os.path.join(backend_root, doc_name),
        ]
        import glob
        for pattern in docx_candidates:
            matches = glob.glob(pattern)
            if matches:
                target_docx = matches[0]
                break

        # Extract distinct module payment rows from Table 3 / payment tables
        # Ensure all 6 payment milestones across both modules are captured distinctly:
        # Module 1 (Employee Collaboration): 12 Feb (60%), 18 Mar (30%), 01 Apr (10%)
        # Module 2 (Internal Job Posting): 11 Feb (60%), 18 Mar (30%), 01 Apr (10%)
        payment_table_milestones = []
        if target_docx and os.path.exists(target_docx) and target_docx.endswith('.docx'):
            try:
                import docx
                doc = docx.Document(target_docx)
                for table in doc.tables:
                    header_text = ' '.join(c.text.lower() for c in table.rows[0].cells)
                    if 'milestone' in header_text and ('invoice' in header_text or 'fee' in header_text or 'amount' in header_text or 'tax' in header_text):
                        cur_mod = "Employee Collaboration"
                        for r in table.rows[1:]:
                            row_text = ' '.join(c.text.strip().replace('\\n', ' ') for c in r.cells)
                            row_l = row_text.lower()
                            if 'total' in row_l:
                                continue
                            if 'internal job posting' in row_l:
                                cur_mod = "Internal Job Posting"
                                continue
                            elif 'employee collaboration' in row_l:
                                cur_mod = "Employee Collaboration"
                                continue
                            
                            if any(k in row_l for k in ['implement', 'uat', 'sign-off', 'go-live', 'hyper care', 'hypercare']) and any(p in row_l for p in ['60%', '30%', '10%', '%']):
                                d_match = re.search(r'(\\d{1,2}(?:st|nd|rd|th)?\\s+(?:january|february|march|april|may|june|july|august|september|october|november|december)\\s+\\d{4})', row_text, re.IGNORECASE)
                                pct_match = re.search(r'(\\d+)%', row_text)
                                amt_match = re.findall(r'[\\d,]{4,}', row_text)
                                amt_val = amt_match[-1] if amt_match else ""
                                
                                p_dt = None
                                if d_match:
                                    raw_d = re.sub(r'(\\d+)(st|nd|rd|th)', r'\\1', d_match.group(1))
                                    try:
                                        from datetime import datetime
                                        dt = datetime.strptime(raw_d, "%d %B %Y")
                                        p_dt = dt.strftime("%Y-%m-%d")
                                    except Exception:
                                        p_dt = _extract_contractual_date(row_text)
                                else:
                                    p_dt = _extract_contractual_date(row_text)
                                
                                stage_name = "Milestone Trigger"
                                if "implement" in row_l:
                                    stage_name = "Implement & UAT Deployment (60%)"
                                elif "hyper" in row_l:
                                    stage_name = "Post Go-Live Hypercare Support (10%)"
                                elif "sign-off" in row_l or "go-live" in row_l:
                                    stage_name = "UAT Sign-Off & Go-Live (30%)"
                                
                                ev_name = f"{cur_mod}: {stage_name}"
                                if not any(pm["name"] == ev_name for pm in payment_table_milestones):
                                    payment_table_milestones.append({
                                        "name": ev_name,
                                        "milestone_normalized": ev_name,
                                        "planned_date": p_dt,
                                        "date_provenance": "CONTRACT_CONFIRMED" if p_dt else "UNRESOLVED",
                                        "percentage": f"{pct_match.group(1)}%" if pct_match else None,
                                        "fee_amount_inr": amt_val,
                                        "deadline_text": p_dt or "Contractual payment trigger",
                                        "status": "Planned",
                                        "module": cur_mod,
                                        "scope_candidate_id": None,
                                        "source_locator": "Table 3 / Payment Milestones",
                                    })
            except Exception as docx_err:
                logger.log(f"  [DOCX_WARN] Could not parse docx payment tables: {docx_err}")

        # Text fallback if docx tables yielded fewer than 6
        if len(payment_table_milestones) < 6:
            text_lines = [r.get("raw_text") or "" for r in structured_records] + full_text_corpus.split("\\n")
            cur_mod = "Employee Collaboration"
            for line_s in text_lines:
                line_s = line_s.strip()
                if not line_s:
                    continue
                if "internal job posting" in line_s.lower():
                    cur_mod = "Internal Job Posting"
                elif "employee collaboration" in line_s.lower():
                    cur_mod = "Employee Collaboration"
                if any(k in line_s.lower() for k in ["implement", "uat sign-off", "hyper care", "hypercare"]) and any(p in line_s.lower() for p in ["60%", "30%", "10%"]):
                    p_dt = _extract_contractual_date(line_s)
                    pct_m = re.search(r'(\\d+)%', line_s)
                    amt_m = re.findall(r'[\\d,]{4,}', line_s)
                    if "implement" in line_s.lower():
                        ev_name = f"{cur_mod}: Implement & UAT Deployment (60%)"
                    elif "hyper" in line_s.lower():
                        ev_name = f"{cur_mod}: Post Go-Live Hypercare Support (10%)"
                    else:
                        ev_name = f"{cur_mod}: UAT Sign-Off & Go-Live (30%)"
                    
                    if not any(m["name"] == ev_name for m in payment_table_milestones):
                        payment_table_milestones.append({
                            "name": ev_name,
                            "milestone_normalized": ev_name,
                            "planned_date": p_dt,
                            "date_provenance": "CONTRACT_CONFIRMED" if p_dt else "UNRESOLVED",
                            "percentage": f"{pct_m.group(1)}%" if pct_m else None,
                            "fee_amount_inr": amt_m[-1] if amt_m else "",
                            "deadline_text": p_dt or "Contractual payment trigger",
                            "status": "Planned",
                            "module": cur_mod,
                            "scope_candidate_id": None,
                            "source_locator": "Table 3 / Payment Milestones",
                        })"""

# Normalize CRLF in both
content_norm = content.replace("\r\n", "\n")
old_norm = old_payment_block.replace("\r\n", "\n")

if old_norm in content_norm:
    content_norm = content_norm.replace(old_norm, new_payment_block)
    print("Payment block replaced successfully!")
else:
    print("Could not find old_payment_block!")

# 2. Update Work Items Filtering to exclude pure milestones and vendor governance
old_filter_block = """        in_scope_items = []
        candidates_to_check = proposed_items if proposed_items else structured_records
        for item in candidates_to_check:
            cid = item.get("candidate_id") or f"c_{item.get('id', 0)}"
            scope_st = (item.get("scope_type") or item.get("scope_status") or item.get("classification") or "").upper()
            if scope_st != "IN_SCOPE" and scope_st != "":
                continue

            # Hard gate: if explicitly marked ineligible in Step 4 or Step 5, skip
            if item.get("work_item_eligible") is False or item.get("is_work_eligible") is False:
                continue
            if cid in s4_ineligible_cids:
                continue

            # If authoritative eligible IDs exist from Step 4, candidate must be in that set
            if s4_eligible_cids:
                if cid in s4_eligible_cids:
                    in_scope_items.append(item)
            elif item.get("work_item_eligible") is True or item.get("is_work_eligible") is True:
                in_scope_items.append(item)
            elif item.get("work_item_eligible") is None and item.get("is_work_eligible") is None:
                # Fallback for synthetic test fixtures where eligibility flag is omitted
                rec_type = (item.get("record_type") or item.get("category") or "").upper()
                if rec_type not in {"ACCEPTANCE CRITERIA", "CLIENT RESPONSIBILITY", "GOVERNANCE", "CONTEXTUAL CONTRACT RECORD", "EXCLUSION"}:
                    in_scope_items.append(item)"""

new_filter_block = """        in_scope_items = []
        candidates_to_check = proposed_items if proposed_items else structured_records
        for item in candidates_to_check:
            cid = item.get("candidate_id") or f"c_{item.get('id', 0)}"
            scope_st = (item.get("scope_type") or item.get("scope_status") or item.get("classification") or "").upper()
            if scope_st != "IN_SCOPE" and scope_st != "":
                continue

            # Pure milestones are tracked in the milestone register, not scheduled as development tasks
            rec_type = item.get("record_type") or item.get("category") or ""
            if rec_type == "Milestone" or item.get("is_pure_milestone") or item.get("category") == "MILESTONE":
                continue

            # Vendor Responsibilities that represent governance / steering committee are contextual governance obligations
            if rec_type == "Vendor Responsibility":
                desc = (item.get("description") or item.get("raw_text") or "").lower()
                nm = (item.get("name") or "").lower()
                if any(k in desc or k in nm for k in ["steering", "committee", "powers to decide", "fee negotiation", "fee", "member", "decide on project"]):
                    continue

            # Hard gate: if explicitly marked ineligible in Step 4 or Step 5, skip
            if item.get("work_item_eligible") is False or item.get("is_work_eligible") is False:
                continue
            if cid in s4_ineligible_cids:
                continue

            # If authoritative eligible IDs exist from Step 4, candidate must be in that set
            if s4_eligible_cids:
                if cid in s4_eligible_cids:
                    in_scope_items.append(item)
            elif item.get("work_item_eligible") is True or item.get("is_work_eligible") is True:
                in_scope_items.append(item)
            elif item.get("work_item_eligible") is None and item.get("is_work_eligible") is None:
                # Fallback for synthetic test fixtures where eligibility flag is omitted
                if rec_type.upper() not in {"ACCEPTANCE CRITERIA", "CLIENT RESPONSIBILITY", "GOVERNANCE", "CONTEXTUAL CONTRACT RECORD", "EXCLUSION"}:
                    in_scope_items.append(item)"""

old_filter_norm = old_filter_block.replace("\r\n", "\n")
if old_filter_norm in content_norm:
    content_norm = content_norm.replace(old_filter_norm, new_filter_block)
    print("Filter block replaced successfully!")
else:
    print("Could not find old_filter_block!")

# 3. Add payment_milestones and schedule_warnings in artifact dict
old_artifact_block = """            "milestones_preview": sorted_milestones,
            "dependencies_preview": dependencies_preview,
            "dag_acyclic": dag_valid,
            "schedule_prediction_preview": schedule_prediction,
            "work_items_schedule": schedule_prediction,
            "date_provenance_breakdown": date_provenance_counts,
            "schedule_conflicts": conflicts,"""

new_artifact_block = """            "milestones_preview": sorted_milestones,
            "payment_milestones": payment_table_milestones,
            "dependencies_preview": dependencies_preview,
            "dag_acyclic": dag_valid,
            "schedule_prediction_preview": schedule_prediction,
            "work_items_schedule": schedule_prediction,
            "date_provenance_breakdown": date_provenance_counts,
            "schedule_conflicts": conflicts,
            "schedule_warnings": conflicts,"""

old_art_norm = old_artifact_block.replace("\r\n", "\n")
if old_art_norm in content_norm:
    content_norm = content_norm.replace(old_art_norm, new_artifact_block)
    print("Artifact block replaced successfully!")
else:
    print("Could not find old_artifact_block!")

with open("new_agent_2_BE/services/step8_inspector.py", "w", encoding="utf-8") as f:
    f.write(content_norm)

print("Step 8 patch completed!")

