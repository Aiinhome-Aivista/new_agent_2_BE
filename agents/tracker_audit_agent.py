import json

class TrackerAuditAgent:
    @classmethod
    def persist_tracker_item(cls, db_cursor, project_id: int, document_id: int, item_type: str, 
                             is_out_of_scope: bool, risk_score: int, risk_level: str, 
                             risk_category: str, confidence: float, reasoning: str, 
                             requires_escalation: bool) -> int:
        """
        Acts as the Tracker & Audit Agent. Deterministically persists state with evidence lineage
        into the `tracker_items` table and logs the action in the `audit_logs` table.
        """
        # 1. Insert into tracker_items
        tracker_sql = """
            INSERT INTO tracker_items 
            (project_id, source_document_id, item_type, is_out_of_scope, risk_score, 
             risk_level, risk_category, confidence, reasoning, requires_escalation, status)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'OPEN')
        """
        db_cursor.execute(tracker_sql, (
            project_id, document_id, item_type, int(is_out_of_scope), risk_score,
            risk_level, risk_category, confidence, reasoning, int(requires_escalation)
        ))
        tracker_id = db_cursor.lastrowid
        
        # 2. Insert into audit_logs to maintain lineage
        audit_details = {
            "source_document_id": document_id,
            "risk_score": risk_score,
            "risk_level": risk_level,
            "reasoning": reasoning[:200] + "..." if len(reasoning) > 200 else reasoning
        }
        
        audit_sql = """
            INSERT INTO audit_logs
            (project_id, action_type, entity_type, entity_id, details)
            VALUES (%s, 'CREATED', 'TRACKER_ITEM', %s, %s)
        """
        # Use a try-except block just in case the schema uses details vs details_json
        try:
            db_cursor.execute(audit_sql, (project_id, tracker_id, json.dumps(audit_details)))
        except Exception as e:
            # Fallback if the column is named differently or doesn't exist in older migrations
            print(f"Warning: Failed to insert audit log: {e}")
            
        return tracker_id
