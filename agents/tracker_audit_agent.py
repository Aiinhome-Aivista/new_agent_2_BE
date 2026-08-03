import json

class TrackerAuditAgent:
    @classmethod
    def persist_tracker_item(cls, db_cursor, project_id: int, document_id: int, item_type: str, 
                             is_out_of_scope: bool, risk_score: int, risk_level: str, 
                             risk_category: str, confidence: float, reasoning: str, 
                             requires_escalation: bool, title: str = None, reference_id: int = None,
                             priority_order: int = None, status: str = 'OPEN',
                             resolve_only: bool = False) -> int:
        """
        Acts as the Tracker & Audit Agent. Deterministically persists state with evidence lineage
        into the `tracker_items` table and logs the action in the `audit_logs` table.
        """
        # 1. Check for existing OPEN item for deduplication (Prefer title match to avoid duplicate activities creating duplicate tracker items)
        existing_id = None
        import re
        norm_title = re.sub(r'[^\w\s]', '', (title or "").lower().strip())
        
        db_cursor.execute(
            """SELECT id, risk_score, reasoning, title, reference_id FROM tracker_items
               WHERE project_id = %s AND status = 'OPEN'
               ORDER BY id DESC""",
            (project_id,)
        )
        all_open = db_cursor.fetchall()
        
        # Find best match by title first, then by reference_id
        for row in (all_open or []):
            existing_title = row['title'] if isinstance(row, dict) else (row[3] if len(row) > 3 else "")
            existing_ref = row['reference_id'] if isinstance(row, dict) else (row[4] if len(row) > 4 else None)
            
            norm_existing = re.sub(r'[^\w\s]', '', (existing_title or "").lower().strip())
            
            if norm_title and norm_existing == norm_title:
                db_cursor.execute(
                    "SELECT id, risk_score, reasoning FROM tracker_items WHERE id = %s",
                    (row['id'] if isinstance(row, dict) else row[0],)
                )
                break
            elif reference_id and existing_ref == reference_id:
                db_cursor.execute(
                    "SELECT id, risk_score, reasoning FROM tracker_items WHERE id = %s",
                    (row['id'] if isinstance(row, dict) else row[0],)
                )
                break
        else:
            db_cursor.execute("SELECT NULL, NULL, NULL WHERE FALSE")  # no match found

            
        existing = db_cursor.fetchone()
        
        if existing:
            existing_id = existing['id'] if isinstance(existing, dict) else existing[0]
            existing_score = existing['risk_score'] if isinstance(existing, dict) else existing[1]
            existing_reasoning = existing['reasoning'] if isinstance(existing, dict) else existing[2]
            
            # Check source document ID to know if this is a re-evaluation or a new update
            db_cursor.execute("SELECT source_document_id FROM tracker_items WHERE id = %s", (existing_id,))
            existing_doc = db_cursor.fetchone()
            existing_doc_id = existing_doc['source_document_id'] if isinstance(existing_doc, dict) else (existing_doc[0] if existing_doc else None)

            # --- Rule 6: Per-Entity Version Protection & Conflict Detection ---
            db_cursor.execute("SELECT uploaded_at FROM documents WHERE id = %s", (document_id,))
            incoming_doc_row = db_cursor.fetchone()
            incoming_date = incoming_doc_row['uploaded_at'] if isinstance(incoming_doc_row, dict) else (incoming_doc_row[0] if incoming_doc_row else None)

            if existing_doc_id:
                db_cursor.execute("SELECT uploaded_at FROM documents WHERE id = %s", (existing_doc_id,))
                existing_doc_row = db_cursor.fetchone()
                existing_date = existing_doc_row['uploaded_at'] if isinstance(existing_doc_row, dict) else (existing_doc_row[0] if existing_doc_row else None)
            else:
                existing_date = None
                
            if existing_date and incoming_date:
                # Same-day conflict detection
                if incoming_date.date() == existing_date.date() and str(existing_doc_id) != str(document_id):
                    # We check current status in the DB
                    db_cursor.execute("SELECT status FROM tracker_items WHERE id = %s", (existing_id,))
                    curr_st = db_cursor.fetchone()
                    curr_status = curr_st['status'] if isinstance(curr_st, dict) else curr_st[0]
                    if curr_status != status:
                        requires_escalation = True
                        reasoning = f"[CONFLICT DETECTED: Attempted to change status from {curr_status} to {status} on the same day]\n" + reasoning
                        status = curr_status
                elif incoming_date < existing_date:
                    return existing_id # Ignore stale update completely
            # ----------------------------------------------------------------

            if str(existing_doc_id) == str(document_id):
                # Same document (re-evaluation): Overwrite reasoning and score
                new_reasoning = reasoning
                final_risk_score = risk_score
            else:
                # New document: Append reasoning, take new score (allow score to go down)
                new_reasoning = existing_reasoning
                if reasoning and reasoning not in existing_reasoning:
                    new_reasoning = existing_reasoning + f"\n\n[Update]: {reasoning}"
                final_risk_score = risk_score
                
            # Update the existing record — always refresh category + score + reasoning
            update_sql = """
                UPDATE tracker_items 
                SET source_document_id = %s, risk_score = %s, risk_level = %s,
                    risk_category = %s, confidence = %s, reasoning = %s, status = %s
                    {priority_clause}
                WHERE id = %s
            """.format(priority_clause=", priority_order = %s" if priority_order is not None else "")
            
            if priority_order is not None:
                db_cursor.execute(update_sql, (document_id, final_risk_score, risk_level, risk_category, confidence, new_reasoning, status, priority_order, existing_id))
            else:
                db_cursor.execute(update_sql, (document_id, final_risk_score, risk_level, risk_category, confidence, new_reasoning, status, existing_id))
            tracker_id = existing_id
            
            if status == 'RESOLVED':
                # Also set resolved_at / resolution if it's resolved
                db_cursor.execute("UPDATE tracker_items SET resolved_at = NOW(), resolution = 'Auto-resolved (Condition cleared)' WHERE id = %s", (existing_id,))
                action_type = 'RESOLVED'
            else:
                action_type = 'UPDATED'
        else:
            if resolve_only:
                return None  # Do not create a new record if we only intend to resolve an existing one.
                
            # 2. Insert new tracker_items
            has_priority = priority_order is not None
            tracker_sql = """
                INSERT INTO tracker_items 
                (project_id, source_document_id, item_type, reference_id, title, is_out_of_scope, risk_score, 
                 risk_level, risk_category, confidence, reasoning, requires_escalation{priority_col}, status)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s{priority_placeholder}, 'OPEN')
            """.format(
                priority_col=", priority_order" if has_priority else "",
                priority_placeholder=", %s" if has_priority else ""
            )
            extra_vals = (priority_order,) if has_priority else ()
            db_cursor.execute(tracker_sql, (
                project_id, document_id, item_type, reference_id, title, int(is_out_of_scope), risk_score,
                risk_level, risk_category, confidence, reasoning, int(requires_escalation)
            ) + extra_vals)
            tracker_id = db_cursor.lastrowid
            action_type = 'CREATED'
        
        # 3. Insert into audit_logs to maintain lineage
        audit_details = {
            "source_document_id": document_id,
            "risk_score": final_risk_score if existing else risk_score,
            "previous_score": existing_score if existing else None,
            "risk_level": risk_level,
            "reasoning": reasoning[:200] + "..." if len(reasoning) > 200 else reasoning
        }
        
        audit_sql = """
            INSERT INTO audit_logs
            (project_id, agent_name, action, entity_type, entity_id, details_json)
            VALUES (%s, 'TrackerAuditAgent', %s, 'TRACKER_ITEM', %s, %s)
        """
        try:
            db_cursor.execute(audit_sql, (project_id, action_type, tracker_id, json.dumps(audit_details)))
        except Exception as e:
            print(f"Warning: Failed to insert audit log: {e}")
            
        return tracker_id
