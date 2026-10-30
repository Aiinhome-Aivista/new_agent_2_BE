with open("new_agent_2_BE/repositories/baseline_repository.py", "r", encoding="utf-8") as f:
    code = f.read()

old_block = """        # ── Unified Schema v2.0 fields ──
        baseline["schema_version"] = "2.0.0"
        baseline["field_semantics"] = {
            "all_records": "Complete canonical scope items ledger (reconciled)",
            "work_items": "Eligible capacity-consuming work items with AI-predicted deadlines",
            "out_of_scope_records": "Contractually excluded items with change control requirement",
            "pending_review_records": "Ambiguous/uncertain items gated for review"
        }
        baseline["all_records"] = top_level_items
        baseline["work_items"] = [
            it for it in top_level_items 
            if it.get("estimated_deadline") is not None or it.get("deadline") is not None
        ]
        baseline["out_of_scope_records"] = [
            it for it in top_level_items if it.get("scope_type") == "OUT_OF_SCOPE"
        ]
        baseline["pending_review_records"] = [
            it for it in top_level_items if it.get("scope_type") == "UNCERTAIN"
        ]
        baseline["payment_milestones"] = [
            m for m in milestones 
            if any(k in (m.get("name") or "").lower() for k in ("60%", "30%", "10%", "implement", "sign-off", "hypercare", "payment"))
        ]
        baseline["project_phases"] = [
            m for m in milestones if m not in baseline["payment_milestones"]
        ]
        return baseline"""

new_block = """        # ── Unified Schema v2.0 fields ──
        baseline["schema_version"] = "2.0.0"
        baseline["field_semantics"] = {
            "all_records": "Complete canonical scope items ledger (reconciled)",
            "work_items": "Eligible capacity-consuming work items with AI-predicted deadlines",
            "out_of_scope_records": "Contractually excluded items with change control requirement",
            "pending_review_records": "Ambiguous/uncertain items gated for review"
        }
        baseline["all_records"] = top_level_items
        baseline["work_items"] = [
            it for it in top_level_items 
            if it.get("parsed_plan_assumptions", {}).get("work_item_eligible") is True
            or (it.get("scope_type") == "IN_SCOPE" and (it.get("estimated_deadline") is not None or it.get("deadline") is not None) and it.get("category") != "MILESTONE")
        ]
        baseline["out_of_scope_records"] = [
            it for it in top_level_items if it.get("scope_type") == "OUT_OF_SCOPE"
        ]
        baseline["pending_review_records"] = [
            it for it in top_level_items if it.get("scope_type") == "UNCERTAIN"
        ]
        baseline["governance_obligations"] = [
            it for it in top_level_items
            if it.get("scope_type") == "IN_SCOPE" and (
                it.get("category") == "GOVERNANCE" or
                it.get("parsed_plan_assumptions", {}).get("record_type") in ("Governance Obligation", "Vendor Responsibility") or
                any(k in (it.get("name") or "").lower() for k in ("steering", "committee", "powers to decide", "fee negotiation"))
            )
        ]
        baseline["contextual_records"] = [
            it for it in top_level_items
            if it.get("scope_type") == "IN_SCOPE"
            and it not in baseline["work_items"]
            and it not in baseline["governance_obligations"]
            and it.get("category") != "MILESTONE"
        ]
        baseline["payment_milestones"] = [
            m for m in milestones 
            if any(k in (m.get("name") or "").lower() for k in ("60%", "30%", "10%", "implement", "sign-off", "hypercare", "payment"))
        ]
        baseline["project_phases"] = [
            m for m in milestones if m not in baseline["payment_milestones"]
        ]

        # Enrich missing planning registers from authoritative final artifact / Step 9 logs
        try:
            from services.final_baseline_service import FinalBaselineService
            doc_id = baseline.get("source_document_id")
            art_res = FinalBaselineService.load_validated_final_baseline(project_id, doc_id)
            art = art_res.get("artifact", {}) if art_res.get("status") in ("READY", "SUCCESS") else {}
            baseline["customer_dependencies"] = art.get("customer_dependencies") or []
            baseline["client_responsibilities"] = art.get("client_responsibilities") or []
            baseline["commitments"] = art.get("commitments") or []
            baseline["contractual_risks"] = art.get("contractual_risks") or []
            baseline["document_quality_findings"] = art.get("document_quality_findings") or []
            baseline["schedule_warnings"] = art.get("schedule_warnings") or []
        except Exception:
            baseline["customer_dependencies"] = []
            baseline["client_responsibilities"] = []
            baseline["commitments"] = []
            baseline["contractual_risks"] = []
            baseline["document_quality_findings"] = []
            baseline["schedule_warnings"] = []

        return baseline"""

old_approve = """    @staticmethod
    def approve_baseline(db: mysql.connector.connection.MySQLConnection, baseline_id: int, user_id: int) -> None:
        cursor = db.cursor()
        cursor.execute("UPDATE scope_baselines SET status = 'APPROVED', approved_by = %s, approved_at = NOW() WHERE id = %s", (user_id, baseline_id))
        cursor.close()"""

new_approve = """    @staticmethod
    def approve_baseline(db: mysql.connector.connection.MySQLConnection, baseline_id: int, user_id: int, allow_override: bool = False) -> None:
        cursor = db.cursor(dictionary=True)
        cursor.execute("SELECT COUNT(*) as count FROM scope_items WHERE baseline_id = %s AND scope_type = 'UNCERTAIN'", (baseline_id,))
        row = cursor.fetchone()
        uncertain_count = row.get("count", 0) if row else 0
        if uncertain_count > 0 and not allow_override:
            cursor.close()
            raise ValueError(f"Baseline approval blocked: {uncertain_count} scope item(s) remain PENDING_REVIEW. Review gates must be resolved before approval.")
        cursor.execute("UPDATE scope_baselines SET status = 'APPROVED', approved_by = %s, approved_at = NOW() WHERE id = %s", (user_id, baseline_id))
        cursor.close()"""

code_norm = code.replace("\r\n", "\n")
old_block_norm = old_block.replace("\r\n", "\n")
old_app_norm = old_approve.replace("\r\n", "\n")

if old_block_norm in code_norm:
    code_norm = code_norm.replace(old_block_norm, new_block)
    print("Baseline details replaced successfully!")
else:
    print("Could not find old_block!")

if old_app_norm in code_norm:
    code_norm = code_norm.replace(old_app_norm, new_approve)
    print("Approve baseline replaced successfully!")
else:
    print("Could not find old_app_norm!")

with open("new_agent_2_BE/repositories/baseline_repository.py", "w", encoding="utf-8") as f:
    f.write(code_norm)

print("baseline_repository.py patch complete!")

