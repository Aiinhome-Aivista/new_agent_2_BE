import json

class TrackerAuditAgent:
    @classmethod
    def persist_tracker_item(cls, db_cursor, project_id: int, document_id: int, item_type: str, 
                             is_out_of_scope: bool, risk_score: int, risk_level: str, 
                             risk_category: str, confidence: float, reasoning: str, 
                             requires_escalation: bool, title: str = None, reference_id: int = None) -> int:
        """
        Acts as the Tracker & Audit Agent. Deterministically persists state with evidence lineage
        into the `tracker_items` table and logs the action in the `audit_logs` table.
        """
        # 1. Check for existing OPEN item for deduplication
        existing_id = None
        if reference_id:
            db_cursor.execute(
                "SELECT id, risk_score, reasoning FROM tracker_items WHERE project_id = %s AND reference_id = %s AND status = 'OPEN' ORDER BY id DESC LIMIT 1",
                (project_id, reference_id)
            )
        elif title:
            # Normalize the title for matching: lowercase + strip punctuation
            # This prevents "VPN Connectivity" and "VPN Connectivity." from creating two records.
            import re
            norm_title = re.sub(r'[^\w\s]', '', title.lower().strip())
            db_cursor.execute(
                """SELECT id, risk_score, reasoning, title FROM tracker_items
                   WHERE project_id = %s AND status = 'OPEN'
                   ORDER BY id DESC""",
                (project_id,)
            )
            all_open = db_cursor.fetchall()
            # Manual normalized comparison — find best match
            for row in (all_open or []):
                existing_title = row['title'] if isinstance(row, dict) else (row[3] if len(row) > 3 else "")
                norm_existing = re.sub(r'[^\w\s]', '', (existing_title or "").lower().strip())
                if norm_existing == norm_title:
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
            
            # Append new reasoning if different
            new_reasoning = existing_reasoning
            if reasoning and reasoning not in existing_reasoning:
                new_reasoning = existing_reasoning + f"\n\n[Update]: {reasoning}"
                
            # Take the max risk score
            final_risk_score = max(existing_score, risk_score)
            
            # Update the existing record
            update_sql = """
                UPDATE tracker_items 
                SET source_document_id = %s, risk_score = %s, risk_level = %s, confidence = %s, reasoning = %s
                WHERE id = %s
            """
            db_cursor.execute(update_sql, (document_id, final_risk_score, risk_level, confidence, new_reasoning, existing_id))
            tracker_id = existing_id
            action_type = 'UPDATED'
        else:
            # 2. Insert new tracker_items
            tracker_sql = """
                INSERT INTO tracker_items 
                (project_id, source_document_id, item_type, reference_id, title, is_out_of_scope, risk_score, 
                 risk_level, risk_category, confidence, reasoning, requires_escalation, status)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'OPEN')
            """
            db_cursor.execute(tracker_sql, (
                project_id, document_id, item_type, reference_id, title, int(is_out_of_scope), risk_score,
                risk_level, risk_category, confidence, reasoning, int(requires_escalation)
            ))
            tracker_id = db_cursor.lastrowid
            action_type = 'CREATED'
        
        # 3. Insert into audit_logs to maintain lineage
        audit_details = {
            "source_document_id": document_id,
            "risk_score": risk_score,
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
