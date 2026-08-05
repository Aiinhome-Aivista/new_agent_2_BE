import json

# Maps runtime risk categories to their immutable "origin" label.
# This is set once on INSERT and never changed — preserving historical context.
_ORIGIN_MAP = {
    'ROOT_CAUSE': 'Root Cause Blocker',
    'EXECUTION_BLOCKER': 'Execution Blocker',
    'CUSTOMER_DEPENDENCY': 'Customer Dependency',
    'TECHNICAL_DEPENDENCY': 'Technical Dependency',
    'SCOPE_CREEP': 'Scope Creep',
    'DELAY': 'Delay Risk',
    'MISSING_DELIVERABLE': 'Missing Deliverable',
    'STAKEHOLDER': 'Stakeholder Risk',
    'GENERAL': 'Execution Risk',
    'DEPENDENCY': 'Customer Dependency',
    'RESOLVED': 'Resolved Risk',
    'BLOCKED': 'Execution Blocker',
}

class TrackerAuditAgent:
    @classmethod
    def persist_tracker_item(cls, db_cursor, project_id: int, document_id: int, item_type: str,
                             is_out_of_scope: bool, risk_score: int, risk_level: str,
                             risk_category: str, confidence: float, reasoning: str,
                             requires_escalation: bool, title: str = None, reference_id: int = None,
                             priority_order: int = None, status: str = 'OPEN',
                             resolve_only: bool = False, risk_source: str = 'OBSERVED',
                             recommended_action: str = None) -> int:
        """
        Acts as the Tracker & Audit Agent. Deterministically persists state with evidence lineage
        into the `tracker_items` table and logs the action in the `audit_logs` table.

        Key invariants:
        - risk_origin is IMMUTABLE: set once on creation, never overwritten on updates.
        - previous_highest_score tracks the peak risk_score across the item's lifetime.
        - On resolution: risk_score → 0, priority_order → NULL, reasoning → cleared.
          Historical reasoning is always preserved in audit_logs.
        """
        import re
        norm_title = re.sub(r'[^\w\s]', '', (title or "").lower().strip())

        # 1. Look for existing item (OPEN or RESOLVED) by normalized title or reference_id
        db_cursor.execute(
            """SELECT id, risk_score, reasoning, title, reference_id, risk_level, priority_order,
                      risk_origin, previous_highest_score
               FROM tracker_items
               WHERE project_id = %s
               ORDER BY id DESC""",
            (project_id,)
        )
        all_items = db_cursor.fetchall()

        matched_row = None
        for row in (all_items or []):
            existing_title = row['title'] if isinstance(row, dict) else (row[3] if len(row) > 3 else "")
            existing_ref   = row['reference_id'] if isinstance(row, dict) else (row[4] if len(row) > 4 else None)
            norm_existing  = re.sub(r'[^\w\s]', '', (existing_title or "").lower().strip())

            if norm_title and norm_existing == norm_title:
                matched_row = row
                break
            elif reference_id and existing_ref == reference_id:
                matched_row = row
                break

        if matched_row:
            existing_id       = matched_row['id']            if isinstance(matched_row, dict) else matched_row[0]
            existing_score    = matched_row['risk_score']    if isinstance(matched_row, dict) else matched_row[1]
            existing_reasoning= matched_row['reasoning']     if isinstance(matched_row, dict) else matched_row[2]
            existing_level    = matched_row['risk_level']    if isinstance(matched_row, dict) else matched_row[5]
            existing_priority = matched_row['priority_order']if isinstance(matched_row, dict) else matched_row[6]
            existing_origin   = matched_row['risk_origin']   if isinstance(matched_row, dict) else matched_row[7]
            existing_peak     = matched_row['previous_highest_score'] if isinstance(matched_row, dict) else matched_row[8]

            # ── Immutable origin: once set, never change ──────────────────────
            # If risk_origin already has a value, keep it; don't overwrite history.
            risk_origin_value = existing_origin  # always preserve original

            # ── Track the peak risk score across the item's lifetime ──────────
            # previous_highest_score = max(existing_peak, old_score, new_score)
            candidate_scores = [s for s in [existing_peak, existing_score, risk_score] if s is not None]
            new_peak = max(candidate_scores) if candidate_scores else risk_score

            if resolve_only:
                status = 'RESOLVED'
            elif status != 'RESOLVED':
                status = 'OPEN'

            # ── On resolution: score → 0, reasoning → cleared ────────────────
            if status == 'RESOLVED':
                final_risk_score = 0
                final_risk_level = existing_level   # preserve historical severity label
                final_priority   = None             # free up priority rank slot
                # Clear reasoning; full history is in audit_logs
                final_reasoning  = None
            else:
                final_risk_score = risk_score
                final_risk_level = risk_level
                final_priority   = priority_order if priority_order is not None else existing_priority
                # Append new reasoning only if genuinely new content
                if reasoning and (reasoning[:50] not in (existing_reasoning or "")):
                    final_reasoning = (existing_reasoning or "") + "\nUpdate: " + reasoning
                else:
                    final_reasoning = existing_reasoning

            db_cursor.execute("""
                UPDATE tracker_items
                SET risk_score = %s, risk_level = %s, confidence = %s, reasoning = %s,
                    source_document_id = %s, status = %s, risk_source = %s,
                    priority_order = %s, previous_highest_score = %s, recommended_action = %s
                WHERE id = %s
            """, (
                final_risk_score, final_risk_level, confidence, final_reasoning,
                document_id, status, risk_source,
                final_priority, new_peak, recommended_action, existing_id
            ))
            tracker_id = existing_id

            if status == 'RESOLVED':
                db_cursor.execute(
                    "UPDATE tracker_items SET resolved_at = NOW(), resolution = 'Auto-resolved (Condition cleared)' WHERE id = %s",
                    (existing_id,)
                )
                action_type = 'RESOLVED'
            else:
                action_type = 'UPDATED'
        else:
            if resolve_only:
                return None  # Do not create a new record if we only intend to resolve an existing one.

            # 2. First-time INSERT — set risk_origin here; it will never change after this.
            fallback_label = risk_category.replace('_', ' ').title() if risk_category else 'Execution Risk'
            risk_origin_value = _ORIGIN_MAP.get(risk_category, fallback_label)
            new_peak = risk_score  # initial peak = first observed score

            has_priority = priority_order is not None
            tracker_sql = """
                INSERT INTO tracker_items
                (project_id, source_document_id, item_type, reference_id, title, is_out_of_scope,
                 risk_score, previous_highest_score, risk_level, risk_category, risk_origin,
                 confidence, reasoning, requires_escalation, risk_source{priority_col}, status, recommended_action)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s{priority_placeholder}, %s, %s)
            """.format(
                priority_col=", priority_order" if has_priority else "",
                priority_placeholder=", %s" if has_priority else ""
            )
            extra_vals = (priority_order,) if has_priority else ()
            db_cursor.execute(tracker_sql, (
                project_id, document_id, item_type, reference_id, title, int(is_out_of_scope),
                risk_score, new_peak, risk_level, risk_category, risk_origin_value,
                confidence, reasoning, int(requires_escalation), risk_source
            ) + extra_vals + (status, recommended_action))
            tracker_id = db_cursor.lastrowid
            action_type = 'CREATED'

        # 3. Always write to audit_logs for full immutable lineage
        audit_details = {
            "source_document_id": document_id,
            "risk_score": final_risk_score if matched_row else risk_score,
            "previous_score": existing_score if matched_row else None,
            "peak_score": new_peak,
            "risk_level": risk_level,
            "risk_origin": risk_origin_value,
            "reasoning_snippet": (reasoning or "")[:200] + ("..." if len(reasoning or "") > 200 else "")
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
