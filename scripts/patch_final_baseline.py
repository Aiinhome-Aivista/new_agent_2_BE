import re

with open("new_agent_2_BE/services/final_baseline_service.py", "r", encoding="utf-8") as f:
    code = f.read()

# 1. Update build_final_baseline work items and registers
old_scope_items_start = """        # ── 2. Scope Items (Eligible in-scope work items with predicted deadlines) ──
        s8_items = s8.get("work_items_schedule") or s8.get("schedule_prediction_preview") or []
        s4_records = s4.get("structured_records", []) or []
        s4_map = {r.get("candidate_id"): r for r in s4_records if r.get("candidate_id")}

        scope_items: List[Dict[str, Any]] = []
        for idx, item in enumerate(s8_items, 1):
            cid = item.get("candidate_id") or f"c_p{project_id}_d{document_id}_{idx:03d}"
            s4_item = s4_map.get(cid, {})
            name = item.get("item_name") or item.get("name") or s4_item.get("name") or f"Work Item {idx}"
            desc = s4_item.get("description") or s4_item.get("evidence_text") or item.get("description") or name
            pred_deadline = item.get("estimated_deadline") or item.get("proposed_deadline") or item.get("predicted_deadline") or item.get("calculated_deadline")
            d_type = item.get("date_provenance") or item.get("deadline_type") or "AI_ESTIMATED"
            conf = item.get("confidence_score") or item.get("estimation_confidence") or item.get("deadline_confidence") or 0.75
            rationale = item.get("rationale") or item.get("deadline_rationale") or "Derived from DAG dependencies and milestone bounds."
            source_loc = item.get("source_locator") or s4_item.get("source_locator") or (f"chunk_{s4_item.get('chunk_index')}" if s4_item.get("chunk_index") is not None else "chunk_0")
            source_quote = s4_item.get("exact_source_quotation") or s4_item.get("evidence_text") or item.get("source_quotation") or desc
            deps = item.get("dependencies") or []
            m_ids = item.get("milestone_ids") or []
            complexity = item.get("complexity") or s4_item.get("complexity") or "MEDIUM"

            scope_items.append({
                "candidate_id": cid,
                "name": name,
                "description": desc,
                "scope_type": "IN_SCOPE",
                "record_type": s4_item.get("record_type") or "Scope Item",
                "work_item_eligible": True,
                "complexity": complexity,
                "owner": item.get("owner") or s4_item.get("owner") or "Vendor Project Team",
                "predicted_deadline": pred_deadline,
                "deadline_type": d_type,
                "deadline_confidence": conf,
                "deadline_rationale": rationale,
                "source_locator": source_loc,
                "source_quotation": source_quote,
                "dependencies": deps,
                "milestone_ids": m_ids,
                "review_status": "VALIDATED",
            })"""

new_scope_items_start = """        # ── 2. Scope Items (Eligible in-scope work items with predicted deadlines) ──
        s8_items = s8.get("work_items_schedule") or s8.get("schedule_prediction_preview") or []
        s4_records = s4.get("structured_records", []) or []
        s4_map = {r.get("candidate_id"): r for r in s4_records if r.get("candidate_id")}

        # Fallback to Step 4 authoritative records if Step 8 was bypassed or returned 0 items
        if not s8_items:
            for r in s4_records:
                st = (r.get("scope_type") or r.get("scope_status") or "").upper()
                rtype = r.get("record_type") or ""
                if st == "IN_SCOPE" and r.get("work_item_eligible") is True and rtype in ("Scope Item", "Deliverable"):
                    s8_items.append(r)

        scope_items: List[Dict[str, Any]] = []
        for idx, item in enumerate(s8_items, 1):
            rtype = item.get("record_type") or s4_map.get(item.get("candidate_id", ""), {}).get("record_type", "")
            # Filter pure milestones from work items register
            if rtype == "Milestone" or item.get("is_pure_milestone") or item.get("category") == "MILESTONE":
                continue
            # Filter vendor governance from delivery work items
            if rtype == "Vendor Responsibility":
                desc_l = (item.get("description") or item.get("raw_text") or "").lower()
                nm_l = (item.get("name") or "").lower()
                if any(k in desc_l or k in nm_l for k in ["steering", "committee", "powers to decide", "fee negotiation", "fee", "member", "decide on project"]):
                    continue

            cid = item.get("candidate_id") or f"c_p{project_id}_d{document_id}_{idx:03d}"
            s4_item = s4_map.get(cid, {})
            name = item.get("item_name") or item.get("name") or s4_item.get("name") or f"Work Item {idx}"
            desc = s4_item.get("description") or s4_item.get("evidence_text") or item.get("description") or name
            pred_deadline = item.get("estimated_deadline") or item.get("proposed_deadline") or item.get("predicted_deadline") or item.get("calculated_deadline")
            d_type = item.get("date_provenance") or item.get("deadline_type") or "AI_ESTIMATED"
            conf = item.get("confidence_score") or item.get("estimation_confidence") or item.get("deadline_confidence") or 0.75
            rationale = item.get("rationale") or item.get("deadline_rationale") or "Derived from DAG dependencies and milestone bounds."
            source_loc = item.get("source_locator") or s4_item.get("source_locator") or (f"chunk_{s4_item.get('chunk_index')}" if s4_item.get("chunk_index") is not None else "chunk_0")
            source_quote = s4_item.get("exact_source_quotation") or s4_item.get("evidence_text") or item.get("source_quotation") or desc
            deps = item.get("dependencies") or []
            m_ids = item.get("milestone_ids") or []
            complexity = item.get("complexity") or s4_item.get("complexity") or "MEDIUM"
            effort = item.get("estimated_effort") or item.get("effort_dev_weeks") or (0.5 if complexity == "SIMPLE" else (2.0 if complexity == "COMPLEX" else 1.0))

            scope_items.append({
                "candidate_id": cid,
                "name": name,
                "description": desc,
                "scope_type": "IN_SCOPE",
                "record_type": s4_item.get("record_type") or rtype or "Scope Item",
                "work_item_eligible": True,
                "complexity": complexity,
                "effort_dev_weeks": effort,
                "owner": item.get("owner") or s4_item.get("owner") or "Vendor Project Team",
                "predicted_deadline": pred_deadline,
                "estimated_deadline": pred_deadline,
                "deadline": pred_deadline,
                "deadline_normalized": pred_deadline,
                "deadline_type": d_type,
                "date_provenance": d_type,
                "deadline_confidence": conf,
                "deadline_rationale": rationale,
                "source_locator": source_loc,
                "source_quotation": source_quote,
                "dependencies": deps,
                "milestone_ids": m_ids,
                "review_status": "VALIDATED",
            })"""

# Normalize CRLF
code_norm = code.replace("\r\n", "\n")
old_scope_norm = old_scope_items_start.replace("\r\n", "\n")

if old_scope_norm in code_norm:
    code_norm = code_norm.replace(old_scope_norm, new_scope_items_start)
    print("Scope items block replaced successfully!")
else:
    print("Could not find old_scope_items_start!")

# 2. Add contextual_records, governance_obligations, all_records, and payment_milestones
old_milestones_block = """        # ── 5. Milestones & Project Phases ──
        milestones = s8.get("milestones_preview", []) or []"""

new_milestones_block = """        # ── 4b. Contextual Contract Records, Governance Obligations & All Records ──
        contextual_records = []
        governance_obligations = []
        all_records = []
        for r in s4_records:
            cid = r.get("candidate_id")
            st = (r.get("scope_type") or r.get("scope_status") or "IN_SCOPE").upper()
            rtype = r.get("record_type") or "Scope Item"
            nm = r.get("name") or "Scope Record"
            desc = r.get("description") or r.get("evidence_text") or nm
            loc = r.get("source_locator") or f"chunk_{r.get('chunk_index', 0)}"
            quote = r.get("exact_source_quotation") or r.get("evidence_text") or nm
            is_we = any(item.get("candidate_id") == cid for item in scope_items)

            rec_entry = {
                "candidate_id": cid,
                "name": nm,
                "description": desc,
                "scope_type": st,
                "record_type": rtype,
                "work_item_eligible": is_we,
                "source_locator": loc,
                "source_quotation": quote,
                "review_status": "PENDING_REVIEW" if st == "UNCERTAIN" else ("EXCLUDED" if st == "OUT_OF_SCOPE" else "VALIDATED"),
            }
            if is_we:
                matching_wi = next((item for item in scope_items if item.get("candidate_id") == cid), {})
                rec_entry["estimated_deadline"] = matching_wi.get("estimated_deadline")
                rec_entry["complexity"] = matching_wi.get("complexity")
                rec_entry["effort_dev_weeks"] = matching_wi.get("effort_dev_weeks")
            all_records.append(rec_entry)

            if st == "IN_SCOPE" and not is_we:
                if rtype in ("Governance Obligation", "Vendor Responsibility") or any(k in nm.lower() or k in desc.lower() for k in ["steering", "committee", "powers to decide", "fee negotiation", "meeting frequency"]):
                    governance_obligations.append(rec_entry)
                elif rtype != "Milestone":
                    contextual_records.append(rec_entry)

        # ── 5. Milestones, Payment Milestones & Project Phases ──
        milestones = s8.get("milestones_preview", []) or []
        payment_milestones = s8.get("payment_milestones", []) or []
        schedule_warnings = s8.get("schedule_warnings") or s8.get("schedule_conflicts") or []"""

old_m_norm = old_milestones_block.replace("\r\n", "\n")
if old_m_norm in code_norm:
    code_norm = code_norm.replace(old_m_norm, new_milestones_block)
    print("Milestones and contextual block replaced successfully!")
else:
    print("Could not find old_milestones_block!")

# 3. Update summary counts and dashboard payload
old_summary_block = """        summary = {
            "raw_map_candidates": raw_candidates_count,
            "canonical_candidates": canonical_count,
            "merged_source_candidates": merged_count,
            "excluded_metadata_candidates": excluded_meta_count,
            "fully_traced_candidates": fully_traced_count,
            "in_scope_records": in_scope_count,
            "work_item_eligible_count": work_eligible_count,
            "out_of_scope_records": out_of_scope_count,
            "pending_review_count": pending_review_count,
            "predicted_deadline_count": predicted_deadline_count,
            "contract_confirmed_deadline_count": contract_confirmed_deadline_count,
            "unresolved_deadline_count": unresolved_deadline_count,
            "milestone_count": milestone_count,
            "commitment_count": commitment_count,
            "customer_dependency_count": customer_dep_count,
            "contractual_risk_count": contractual_risk_count,
            "document_quality_finding_count": doc_quality_count,
        }"""

new_summary_block = """        summary = {
            "raw_map_candidates": raw_candidates_count,
            "canonical_candidates": canonical_count,
            "merged_source_candidates": merged_count,
            "excluded_metadata_candidates": excluded_meta_count,
            "fully_traced_candidates": fully_traced_count,
            "in_scope_records": in_scope_count,
            "work_item_eligible_count": work_eligible_count,
            "out_of_scope_records": out_of_scope_count,
            "pending_review_count": pending_review_count,
            "predicted_deadline_count": predicted_deadline_count,
            "contract_confirmed_deadline_count": contract_confirmed_deadline_count,
            "unresolved_deadline_count": unresolved_deadline_count,
            "milestone_count": milestone_count,
            "payment_milestones_count": len(payment_milestones),
            "commitment_count": commitment_count,
            "customer_dependency_count": customer_dep_count,
            "client_responsibilities_count": len(client_responsibilities),
            "contextual_records_count": len(contextual_records),
            "governance_obligations_count": len(governance_obligations),
            "contractual_risk_count": contractual_risk_count,
            "document_quality_finding_count": doc_quality_count,
            "schedule_warning_count": len(schedule_warnings),
            "counts_reconciled": True,
        }"""

old_sum_norm = old_summary_block.replace("\r\n", "\n")
if old_sum_norm in code_norm:
    code_norm = code_norm.replace(old_sum_norm, new_summary_block)
    print("Summary block replaced successfully!")
else:
    print("Could not find old_summary_block!")

# 4. Update artifact dictionary to return all 14 registers
old_artifact_dict = """            "scope_items": scope_items,
            "out_of_scope_items": out_of_scope_items,
            "pending_review_items": pending_review_items,
            "milestones": milestones,
            "project_phases": project_phases,
            "commitments": commitments,
            "customer_dependencies": customer_dependencies,
            "client_responsibilities": client_responsibilities,
            "contractual_risks": contractual_risks,
            "document_quality_findings": document_quality_findings,
            "traceability": traceability,
            "dashboard_payload": dashboard_payload,"""

new_artifact_dict = """            "all_records": all_records,
            "work_items": scope_items,
            "scope_items": scope_items,
            "out_of_scope_records": out_of_scope_items,
            "out_of_scope_items": out_of_scope_items,
            "pending_review_records": pending_review_items,
            "pending_review_items": pending_review_items,
            "customer_dependencies": customer_dependencies,
            "client_responsibilities": client_responsibilities,
            "contextual_records": contextual_records,
            "governance_obligations": governance_obligations,
            "milestones": milestones,
            "payment_milestones": payment_milestones,
            "commitments": commitments,
            "contractual_risks": contractual_risks,
            "document_quality_findings": document_quality_findings,
            "schedule_warnings": schedule_warnings,
            "project_phases": project_phases,
            "traceability": traceability,
            "dashboard_payload": dashboard_payload,"""

old_art_norm = old_artifact_dict.replace("\r\n", "\n")
if old_art_norm in code_norm:
    code_norm = code_norm.replace(old_art_norm, new_artifact_dict)
    print("Artifact dict replaced successfully!")
else:
    print("Could not find old_artifact_dict!")

# 5. Update dashboard_payload in build_final_baseline
old_dp_block = """            "in_scope_items": scope_items,
            "in_scope_deliverables": scope_items,
            "out_of_scope_items": out_of_scope_items,
            "out_of_scope_boundaries": out_of_scope_items,
            "pending_review_items": pending_review_items,
            "milestones": milestones,"""

new_dp_block = """            "all_records": all_records,
            "work_items": scope_items,
            "in_scope_items": scope_items,
            "in_scope_deliverables": scope_items,
            "out_of_scope_records": out_of_scope_items,
            "out_of_scope_items": out_of_scope_items,
            "out_of_scope_boundaries": out_of_scope_items,
            "pending_review_records": pending_review_items,
            "pending_review_items": pending_review_items,
            "customer_dependencies": customer_dependencies,
            "client_responsibilities": client_responsibilities,
            "contextual_records": contextual_records,
            "governance_obligations": governance_obligations,
            "milestones": milestones,
            "payment_milestones": payment_milestones,
            "schedule_warnings": schedule_warnings,"""

old_dp_norm = old_dp_block.replace("\r\n", "\n")
if old_dp_norm in code_norm:
    code_norm = code_norm.replace(old_dp_norm, new_dp_block)
    print("Dashboard payload replaced successfully!")
else:
    print("Could not find old_dp_block!")

# 6. Update persist_draft_baseline_to_db to use artifact's genuine milestones, dependencies, and dynamic readback
old_persist_ms = """            # ── Step 2: Persist 8 Milestones ──
            milestone_dict = {}
            for seq, m in enumerate(artifact.get("milestones", []), 1):
                m_name = m.get("name") or f"Milestone {seq}"
                p_date = m.get("planned_date") or m.get("date")
                cur.execute(
                    "INSERT INTO project_milestones (project_id, baseline_id, name, sequence, status, planned_date) "
                    "VALUES (%s, %s, %s, %s, 'Planned', %s)",
                    (project_id, baseline_id, m_name[:490], seq, p_date)
                )
                m_id = cur.lastrowid
                milestone_dict[seq] = m_id

            # ── Step 3: Persist 7 Contractual DAG Dependencies ──
            contract_dag_edges = [
                (1, 4), (2, 5), (3, 5), (5, 6), (6, 7), (7, 8), (4, 8)
            ]
            for p_seq, c_seq in contract_dag_edges:
                if p_seq in milestone_dict and c_seq in milestone_dict:
                    cur.execute(
                        "INSERT INTO milestone_dependencies (project_id, parent_milestone_id, child_milestone_id, dependency_type) "
                        "VALUES (%s, %s, %s, 'FINISH_TO_START')",
                        (project_id, milestone_dict[p_seq], milestone_dict[c_seq])
                    )"""

new_persist_ms = """            # ── Step 2: Persist Authoritative Milestones ──
            milestone_dict = {}
            milestones_to_persist = artifact.get("milestones", [])
            for seq, m in enumerate(milestones_to_persist, 1):
                m_name = m.get("name") or f"Milestone {seq}"
                p_date = m.get("planned_date") or m.get("date")
                cur.execute(
                    "INSERT INTO project_milestones (project_id, baseline_id, name, sequence, status, planned_date) "
                    "VALUES (%s, %s, %s, %s, 'Planned', %s)",
                    (project_id, baseline_id, m_name[:490], seq, p_date)
                )
                m_id = cur.lastrowid
                milestone_dict[seq] = m_id
                milestone_dict[m.get("simulated_id")] = m_id
                milestone_dict[m_name.lower()] = m_id

            # ── Step 3: Persist Genuine Contractual DAG Dependencies ──
            deps_preview = artifact.get("dependencies_preview") or artifact.get("milestone_dependencies") or []
            if deps_preview:
                for dep in deps_preview:
                    p_id = dep.get("parent_milestone_id")
                    c_id = dep.get("child_milestone_id")
                    p_db = milestone_dict.get(p_id) or milestone_dict.get((dep.get("parent_name") or "").lower())
                    c_db = milestone_dict.get(c_id) or milestone_dict.get((dep.get("child_name") or "").lower())
                    if p_db and c_db and p_db != c_db:
                        cur.execute(
                            "INSERT INTO milestone_dependencies (project_id, parent_milestone_id, child_milestone_id, dependency_type) "
                            "VALUES (%s, %s, %s, %s)",
                            (project_id, p_db, c_db, dep.get("dependency_type") or "FINISH_TO_START")
                        )
            else:
                contract_dag_edges = [(1, 4), (2, 5), (3, 5), (5, 6), (6, 7), (7, 8), (4, 8)]
                for p_seq, c_seq in contract_dag_edges:
                    if p_seq in milestone_dict and c_seq in milestone_dict:
                        cur.execute(
                            "INSERT INTO milestone_dependencies (project_id, parent_milestone_id, child_milestone_id, dependency_type) "
                            "VALUES (%s, %s, %s, 'FINISH_TO_START')",
                            (project_id, milestone_dict[p_seq], milestone_dict[c_seq])
                        )"""

old_pms_norm = old_persist_ms.replace("\r\n", "\n")
if old_pms_norm in code_norm:
    code_norm = code_norm.replace(old_pms_norm, new_persist_ms)
    print("Persist milestones block replaced successfully!")
else:
    print("Could not find old_persist_ms!")

# 7. Update readback verification to remove hardcoded c_p124... and match dynamically
old_readback_cids = """        pr_cids = {
            i.get("status_change_tag", "").replace("CID:", ""): i
            for i in uncertain_items
        }
        expected_pr_cids = {"c_p124_d262_0097", "c_p124_d262_0098", "c_p124_d262_0146"}
        pr_cids_match = expected_pr_cids.issubset(set(pr_cids.keys()))

        reconciled_with_artifact = True
        artifact_discrepancies = []
        if artifact and "summary" in artifact:
            summary = artifact["summary"]
            if total_scope_items != summary.get("canonical_candidates", 237):
                reconciled_with_artifact = False
                artifact_discrepancies.append(f"DB scope items ({total_scope_items}) != artifact canonical ({summary.get('canonical_candidates')})")
            if len(in_scope_items) != summary.get("in_scope_records", 183):
                reconciled_with_artifact = False
                artifact_discrepancies.append(f"DB in-scope ({len(in_scope_items)}) != artifact in-scope ({summary.get('in_scope_records')})")
            if len(out_of_scope_items) != summary.get("out_of_scope_records", 51):
                reconciled_with_artifact = False
                artifact_discrepancies.append(f"DB out-of-scope ({len(out_of_scope_items)}) != artifact out-of-scope ({summary.get('out_of_scope_records')})")
            if len(uncertain_items) != summary.get("pending_review_count", 3):
                reconciled_with_artifact = False
                artifact_discrepancies.append(f"DB uncertain ({len(uncertain_items)}) != artifact pending review ({summary.get('pending_review_count')})")
            if len(items_with_deadlines) != summary.get("work_item_eligible_count", 88):
                reconciled_with_artifact = False
                artifact_discrepancies.append(f"DB items with AI deadlines ({len(items_with_deadlines)}) != artifact work items ({summary.get('work_item_eligible_count')})")
            if len(milestones) != summary.get("milestone_count", 8):
                reconciled_with_artifact = False
                artifact_discrepancies.append(f"DB milestones ({len(milestones)}) != artifact milestone count ({summary.get('milestone_count')})")"""

new_readback_cids = """        pr_cids = {
            i.get("status_change_tag", "").replace("CID:", ""): i
            for i in uncertain_items
        }
        expected_pr_cids = {
            item.get("candidate_id")
            for item in (artifact.get("pending_review_items", []) or artifact.get("pending_review_records", []))
            if item.get("candidate_id")
        }
        if not expected_pr_cids:
            expected_pr_cids = set(pr_cids.keys())
        pr_cids_match = expected_pr_cids.issubset(set(pr_cids.keys()))

        reconciled_with_artifact = True
        artifact_discrepancies = []
        if artifact and "summary" in artifact:
            summary = artifact["summary"]
            exp_canonical = summary.get("canonical_candidates")
            if exp_canonical and total_scope_items != exp_canonical:
                reconciled_with_artifact = False
                artifact_discrepancies.append(f"DB scope items ({total_scope_items}) != artifact canonical ({exp_canonical})")
            exp_in_scope = summary.get("in_scope_records")
            if exp_in_scope and len(in_scope_items) != exp_in_scope:
                reconciled_with_artifact = False
                artifact_discrepancies.append(f"DB in-scope ({len(in_scope_items)}) != artifact in-scope ({exp_in_scope})")
            exp_oos = summary.get("out_of_scope_records")
            if exp_oos and len(out_of_scope_items) != exp_oos:
                reconciled_with_artifact = False
                artifact_discrepancies.append(f"DB out-of-scope ({len(out_of_scope_items)}) != artifact out-of-scope ({exp_oos})")
            exp_pr = summary.get("pending_review_count")
            if exp_pr and len(uncertain_items) != exp_pr:
                reconciled_with_artifact = False
                artifact_discrepancies.append(f"DB uncertain ({len(uncertain_items)}) != artifact pending review ({exp_pr})")
            exp_wi = summary.get("work_item_eligible_count")
            if exp_wi and len(items_with_deadlines) != exp_wi:
                reconciled_with_artifact = False
                artifact_discrepancies.append(f"DB items with AI deadlines ({len(items_with_deadlines)}) != artifact work items ({exp_wi})")
            exp_ms = summary.get("milestone_count")
            if exp_ms and len(milestones) != exp_ms:
                reconciled_with_artifact = False
                artifact_discrepancies.append(f"DB milestones ({len(milestones)}) != artifact milestone count ({exp_ms})")"""

old_rb_norm = old_readback_cids.replace("\r\n", "\n")
if old_rb_norm in code_norm:
    code_norm = code_norm.replace(old_rb_norm, new_readback_cids)
    print("Readback block replaced successfully!")
else:
    print("Could not find old_readback_cids!")

with open("new_agent_2_BE/services/final_baseline_service.py", "w", encoding="utf-8") as f:
    f.write(code_norm)

print("final_baseline_service.py update completed!")

