import sys
import os
import json
from datetime import datetime, date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.database import get_db_connection, db_transaction
from services.final_baseline_service import FinalBaselineService

def test_persistence():
    backend_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    logs_dir = os.path.join(backend_root, "logs")
    artifact_path = os.path.join(logs_dir, "final_baselines", "baseline_final_p124_d262.json")
    
    with open(artifact_path, "r", encoding="utf-8") as f:
        artifact = json.load(f)
        
    s4_path = os.path.join(logs_dir, "step4_inspection_p124_d262.json")
    with open(s4_path, "r", encoding="utf-8") as f:
        s4 = json.load(f)
    s4_records = s4.get("structured_records", [])
    print(f"Loaded artifact ({len(artifact['scope_items'])} work items) and {len(s4_records)} structured records.")

    conn = get_db_connection()
    try:
        project_id = 124
        document_id = 262
        
        # Test transaction
        with db_transaction(conn, dictionary=True) as cur:
            # 1. Scope baseline draft
            cur.execute("SELECT id, version, status FROM scope_baselines WHERE project_id = %s ORDER BY version DESC LIMIT 1", (project_id,))
            existing = cur.fetchone()
            
            if existing and existing["status"] == "DRAFT":
                baseline_id = existing["id"]
                version = existing["version"]
                print(f"Reusing existing DRAFT baseline id={baseline_id}, version={version}")
                # Clean child records for idempotency
                cur.execute("DELETE FROM milestone_dependencies WHERE project_id = %s", (project_id,))
                cur.execute("DELETE FROM scope_milestone_mapping WHERE scope_item_id IN (SELECT id FROM scope_items WHERE baseline_id = %s)", (baseline_id,))
                cur.execute("DELETE FROM project_milestones WHERE baseline_id = %s", (baseline_id,))
                cur.execute("DELETE FROM scope_items WHERE baseline_id = %s", (baseline_id,))
            else:
                max_v = 0
                if existing:
                    max_v = existing.get("version") or 0
                version = max_v + 1
                cur.execute(
                    "INSERT INTO scope_baselines (project_id, version, status, source_document_id, parser_version, layout_version, extractor_version, llm_prompt_version) "
                    "VALUES (%s, %s, 'DRAFT', %s, 'v2', 'v2', 'deep_scan_v2', 'gemini-2.0')",
                    (project_id, version, document_id)
                )
                baseline_id = cur.lastrowid
                print(f"Created new DRAFT baseline id={baseline_id}, version={version}")

            # 2. Insert 8 Milestones
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
            print(f"Inserted {len(milestone_dict)} project milestones.")

            # 3. Insert 7 Contractual DAG Edges
            # Step 8 contract DAG sequence:
            # 1 -> 4, 2 -> 5, 3 -> 5, 5 -> 6, 6 -> 7, 7 -> 8, 4 -> 8
            dag_edges = [
                (1, 4), (2, 5), (3, 5), (5, 6), (6, 7), (7, 8), (4, 8)
            ]
            for p_seq, c_seq in dag_edges:
                if p_seq in milestone_dict and c_seq in milestone_dict:
                    cur.execute(
                        "INSERT INTO milestone_dependencies (project_id, parent_milestone_id, child_milestone_id, dependency_type) "
                        "VALUES (%s, %s, %s, 'FINISH_TO_START')",
                        (project_id, milestone_dict[p_seq], milestone_dict[c_seq])
                    )
            print(f"Inserted {len(dag_edges)} milestone dependencies.")

            # 4. Insert all 237 canonical records
            work_items_map = {item["candidate_id"]: item for item in artifact.get("scope_items", [])}
            oos_map = {item["candidate_id"]: item for item in artifact.get("out_of_scope_items", [])}
            pr_map = {item["candidate_id"]: item for item in artifact.get("pending_review_items", [])}

            saved_count = 0
            for r in s4_records:
                cid = r.get("candidate_id")
                name = (r.get("name") or "Scope Item")[:490]
                desc = r.get("description") or r.get("raw_text") or name
                source_sec = (r.get("section") or "General")[:245]
                source_loc = r.get("source_locator") or f"chunk_{r.get('chunk_index', 0)}"
                evidence = r.get("exact_source_quotation") or r.get("evidence_text") or r.get("raw_text") or name
                conf = float(r.get("confidence") or 0.85)

                if cid in pr_map or r.get("scope_status") == "UNCERTAIN" or cid in ("c_p124_d262_0097", "c_p124_d262_0098", "c_p124_d262_0146"):
                    scope_type = "UNCERTAIN"
                    category = "FUNCTIONAL"
                    approval_status = "PENDING_REVIEW"
                    review_reason = pr_map.get(cid, {}).get("review_reason") or r.get("manual_review_reason") or "Contractual review required"
                    reason_oos = None
                elif cid in oos_map or r.get("scope_status") == "OUT_OF_SCOPE":
                    scope_type = "OUT_OF_SCOPE"
                    category = "OUT_OF_SCOPE"
                    approval_status = "EXCLUDED"
                    review_reason = None
                    reason_oos = oos_map.get(cid, {}).get("reason_for_exclusion") or "Contractual exclusion clause"
                else:
                    scope_type = "IN_SCOPE"
                    if cid in work_items_map:
                        rtype = work_items_map[cid].get("record_type", "")
                        if "DELIVERABLE" in rtype.upper():
                            category = "DELIVERABLE"
                        elif "TECH" in rtype.upper():
                            category = "TECHNICAL"
                        elif "MILESTONE" in rtype.upper():
                            category = "MILESTONE"
                        else:
                            category = "FUNCTIONAL"
                        approval_status = "PENDING_REVIEW"
                    else:
                        category = "FUNCTIONAL"
                        approval_status = "VALIDATED"
                    review_reason = None
                    reason_oos = None

                # AI deadline / planning fields
                if cid in work_items_map:
                    wi = work_items_map[cid]
                    pred_deadline = wi.get("predicted_deadline")
                    pred_start = wi.get("predicted_start_date") or "2027-01-22"
                    d_type = wi.get("deadline_type") or "AI_ESTIMATED"
                    est_conf = str(wi.get("deadline_confidence") or "0.75")
                    basis = (wi.get("deadline_rationale") or "Capacity-based estimation")[:500]
                    effort = float(wi.get("estimated_effort") or (0.5 if wi.get("complexity") == "SIMPLE" else (2.0 if wi.get("complexity") == "COMPLEX" else 1.0)))
                    comp = wi.get("complexity") or "MEDIUM"
                    deps = wi.get("dependencies") or []
                    m_ids = wi.get("milestone_ids") or []
                else:
                    pred_deadline = None
                    pred_start = None
                    d_type = None
                    est_conf = None
                    basis = None
                    effort = None
                    comp = None
                    deps = []
                    m_ids = []

                assumptions_json = json.dumps({
                    "candidate_id": cid,
                    "record_type": r.get("record_type") or "Scope Item",
                    "work_item_eligible": cid in work_items_map,
                    "complexity": comp,
                    "source_locator": source_loc,
                    "source_quotation": evidence,
                    "dependencies": deps,
                    "milestone_ids": m_ids,
                    "review_status": approval_status,
                    "review_reason": review_reason,
                    "reason_for_exclusion": reason_oos,
                })

                sql = """
                    INSERT INTO scope_items (
                        baseline_id, project_id, name, scope_item_normalized, description, scope_type,
                        source_document_id, source_section, evidence_text, confidence,
                        status_change_tag, deadline, deadline_normalized,
                        category, completion_status,
                        estimated_start_date, estimated_deadline, date_provenance,
                        estimation_confidence, estimation_basis, plan_assumptions,
                        estimated_effort_likely, baseline_approval_status
                    ) VALUES (
                        %s, %s, %s, %s, %s, %s,
                        %s, %s, %s, %s,
                        %s, %s, %s,
                        %s, %s,
                        %s, %s, %s,
                        %s, %s, %s,
                        %s, %s
                    )
                """
                cur.execute(sql, (
                    baseline_id, project_id, name, name, desc, scope_type,
                    document_id, source_sec, evidence, conf,
                    f"CID:{cid}", pred_deadline, pred_deadline,
                    category, "ACTIVE",
                    pred_start, pred_deadline, d_type,
                    est_conf, basis, assumptions_json,
                    effort, approval_status
                ))
                saved_count += 1
            print(f"Inserted {saved_count} scope items into database.")

            # 5. Update project monitoring status
            cur.execute("UPDATE projects SET monitoring_status = 'BASELINE_PENDING_REVIEW' WHERE id = %s", (project_id,))

        print("Transaction committed successfully!")

        # 6. Readback verification
        cur_read = conn.cursor(dictionary=True)
        cur_read.execute("SELECT * FROM scope_baselines WHERE id = %s", (baseline_id,))
        b_row = cur_read.fetchone()
        print("Persisted Baseline:", b_row)

        cur_read.execute("SELECT count(id) as total, scope_type FROM scope_items WHERE baseline_id = %s GROUP BY scope_type", (baseline_id,))
        type_counts = cur_read.fetchall()
        print("Scope Items by Type:", type_counts)

        cur_read.execute("SELECT count(id) as work_items FROM scope_items WHERE baseline_id = %s AND estimated_deadline IS NOT NULL", (baseline_id,))
        wi_count = cur_read.fetchone()
        print("Work Items with AI Deadlines:", wi_count)

        cur_read.execute("SELECT count(id) as ms_count FROM project_milestones WHERE baseline_id = %s", (baseline_id,))
        ms_res = cur_read.fetchone()
        print("Milestones count:", ms_res)

        cur_read.execute("SELECT count(id) as dep_count FROM milestone_dependencies WHERE project_id = %s", (project_id,))
        dep_res = cur_read.fetchone()
        print("Milestone Dependencies count:", dep_res)

        cur_read.close()
    finally:
        conn.close()

if __name__ == "__main__":
    test_persistence()
