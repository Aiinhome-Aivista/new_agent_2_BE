import json

# Maps runtime risk categories to their immutable "origin" label.
# This is set once on INSERT and never changed — preserving historical context.
_ORIGIN_MAP = {
    'ROOT_CAUSE': 'Root Cause Blocker',
    # FIX 5: Added missing categories from category_assignment_engine & pipeline
    'ROOT_CAUSE_BLOCKER': 'Root Cause Blocker',
    'EXECUTION_BLOCKER': 'Execution Blocker',
    'DIRECT_EXECUTION_BLOCKER': 'Direct Execution Blocker',
    'TRANSITIVE_EXECUTION_BLOCKER': 'Transitive Execution Blocker',
    'CRITICAL_PATH_RISK': 'Critical Path Risk',
    'CUSTOMER_DEPENDENCY': 'Customer Dependency',
    'TECHNICAL_DEPENDENCY': 'Technical Dependency',
    'INTERNAL_DEPENDENCY': 'Internal Dependency',
    'WAITING_DEPENDENCY': 'Waiting on Dependency',
    'IN_PROGRESS_RISK': 'Execution Risk',
    'SCOPE_CREEP': 'Scope Creep',
    'DELAY': 'Delay Risk',
    'MISSING_DELIVERABLE': 'Missing Deliverable',
    'STAKEHOLDER': 'Stakeholder Risk',
    'GENERAL': 'Execution Risk',
    'DEPENDENCY': 'Customer Dependency',
    'RESOLVED': 'Resolved Risk',
    'BLOCKED': 'Execution Blocker',
}

def _embed_owner_in_reasoning(reasoning: str, owner: str) -> str:
    # FIX 1: Owner embedded in reasoning JSON
    # because tracker_items has no top-level owner column.
    # Frontend reads: JSON.parse(reasoning).owner
    if not owner:
        return reasoning
    try:
        import json
        parsed = json.loads(reasoning) if reasoning else {}
        if isinstance(parsed, dict):
            parsed["owner"] = owner
            return json.dumps(parsed)
        else:
            return json.dumps({"text": str(reasoning), "owner": owner})
    except Exception:
        return json.dumps({"text": str(reasoning or ""), "owner": owner})

class TrackerAuditAgent:
    @classmethod
    def persist_tracker_item(cls, db_cursor, project_id: int, document_id: int, item_type: str,
                             is_out_of_scope: bool, risk_score: int, risk_level: str,
                             risk_category: str, confidence: float, reasoning: str,
                             requires_escalation: bool, title: str = None, reference_id: int = None,
                             priority_order: int = None, status: str = 'NOT_STARTED',
                             resolve_only: bool = False, risk_source: str = 'OBSERVED',
                             recommended_action: str = None, execution_priority_score: int = None,
                             # New decoupled fields
                             execution_status: str = None, risk_status: str = None,
                             graph_role: str = None, canonical_id: str = None,
                             risk_severity_score: int = None,
                             owner: str = None) -> int:
        """
        Acts as the Tracker & Audit Agent. Deterministically persists state with evidence lineage
        into the `tracker_items` table and logs the action in the `audit_logs` table.
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
            existing_title = row.get('title', '') if isinstance(row, dict) else (row[3] if len(row) > 3 else "")
            existing_ref   = row.get('reference_id') if isinstance(row, dict) else (row[4] if len(row) > 4 else None)
            norm_existing  = re.sub(r'[^\w\s]', '', (existing_title or "").lower().strip())

            if norm_title and norm_existing == norm_title:
                matched_row = row
                break
            elif reference_id and existing_ref == reference_id:
                matched_row = row
                break
            elif title and existing_title:
                try:
                    from api.routes.baseline import _is_title_match
                    if _is_title_match(title, existing_title):
                        matched_row = row
                        break
                except Exception:
                    pass

        if matched_row:
            existing_id       = matched_row.get('id') if isinstance(matched_row, dict) else matched_row[0]
            existing_score    = matched_row.get('risk_score') if isinstance(matched_row, dict) else (matched_row[1] if len(matched_row) > 1 else None)
            existing_reasoning= matched_row.get('reasoning') if isinstance(matched_row, dict) else (matched_row[2] if len(matched_row) > 2 else None)
            existing_level    = matched_row.get('risk_level', 'LOW') if isinstance(matched_row, dict) else (matched_row[5] if len(matched_row) > 5 else 'LOW')
            existing_priority = matched_row.get('priority_order') if isinstance(matched_row, dict) else (matched_row[6] if len(matched_row) > 6 else None)
            existing_origin   = matched_row.get('risk_origin') if isinstance(matched_row, dict) else (matched_row[7] if len(matched_row) > 7 else None)
            existing_peak     = matched_row.get('previous_highest_score') if isinstance(matched_row, dict) else (matched_row[8] if len(matched_row) > 8 else None)

            risk_origin_value = existing_origin
            candidate_scores = [s for s in [existing_peak, existing_score, risk_score] if s is not None]
            new_peak = max(candidate_scores) if candidate_scores else risk_score

            if resolve_only:
                status = 'RESOLVED'
            elif status != 'RESOLVED':
                status = 'OPEN'

            if status == 'RESOLVED':
                final_risk_score = 0
                final_exec_score = 0
                final_risk_level = existing_level
                final_priority   = None
                
                # BUG 2 FIX: Preserve original owner from existing reasoning JSON on resolution
                preserved_owner = owner
                if not preserved_owner and existing_reasoning:
                    try:
                        p_ex = json.loads(existing_reasoning)
                        if isinstance(p_ex, dict) and p_ex.get("owner"):
                            preserved_owner = p_ex["owner"]
                    except Exception:
                        pass
                
                # Maintain reasoning JSON with preserved owner
                final_reasoning = _embed_owner_in_reasoning(existing_reasoning or reasoning or "Resolved", preserved_owner)
            else:
                final_risk_score = risk_score
                final_exec_score = execution_priority_score if execution_priority_score is not None else risk_score
                final_risk_level = risk_level
                final_priority   = priority_order if priority_order is not None else existing_priority
                if reasoning and (reasoning[:50] not in (existing_reasoning or "")):
                    final_reasoning = (existing_reasoning or "") + "\nUpdate: " + reasoning
                else:
                    final_reasoning = existing_reasoning
                final_reasoning = _embed_owner_in_reasoning(final_reasoning, owner)

            final_exec_status = (execution_status or status or 'NOT_STARTED').upper()
            if final_exec_status in ('UNKNOWN', ''):
                final_exec_status = 'NOT_STARTED'
            final_risk_status = risk_status or 'OPEN'
            final_risk_sev = risk_severity_score if risk_severity_score is not None else risk_score

            db_cursor.execute("""
                UPDATE tracker_items
                SET risk_score = %s, execution_priority_score = %s, risk_severity_score = %s, risk_level = %s, confidence = %s, reasoning = %s,
                    source_document_id = %s, status = %s, execution_status = %s, risk_status = %s, graph_role = %s, canonical_id = %s, risk_source = %s,
                    priority_order = %s, previous_highest_score = %s, recommended_action = %s
                WHERE id = %s
            """, (
                final_risk_score, final_exec_score, final_risk_sev, final_risk_level, confidence, final_reasoning,
                document_id, status, final_exec_status, final_risk_status, graph_role or 'DOWNSTREAM_ACTIVITY', canonical_id or '', risk_source,
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
                fallback_label = 'Resolved'
                risk_origin_value = 'Resolved'
                final_exec_status = 'RESOLVED'
                final_risk_status = 'RESOLVED'
                final_risk_score = 0
                final_exec_score = 0
                final_risk_sev = 0
                new_peak = 0
                risk_level = 'LOW'
                
                resolved_reasoning = _embed_owner_in_reasoning(reasoning or 'Auto-resolved (Condition cleared)', owner)
                
                tracker_sql = """
                    INSERT INTO tracker_items
                    (project_id, source_document_id, item_type, reference_id, title, is_out_of_scope,
                     risk_score, execution_priority_score, risk_severity_score, previous_highest_score,
                     risk_level, risk_category, risk_origin,
                     confidence, reasoning, requires_escalation, risk_source,
                     status, execution_status, risk_status, graph_role, canonical_id, recommended_action,
                     resolution, resolved_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())
                """
                db_cursor.execute(tracker_sql, (
                    project_id, document_id, item_type or 'ACTIVITY', reference_id, title, int(is_out_of_scope),
                    0, 0, 0, 0,
                    'LOW', risk_category or 'RESOLVED', risk_origin_value,
                    confidence, resolved_reasoning, int(requires_escalation), risk_source,
                    'RESOLVED', 'RESOLVED', 'RESOLVED',
                    graph_role or 'DOWNSTREAM_ACTIVITY',
                    canonical_id or '',
                    recommended_action,
                    reasoning or 'Auto-resolved (Condition cleared)'
                ))
                tracker_id = db_cursor.lastrowid
                action_type = 'RESOLVED'
                return tracker_id

            fallback_label = risk_category.replace('_', ' ').title() if risk_category else 'Execution Risk'
            risk_origin_value = _ORIGIN_MAP.get(risk_category, fallback_label)
            new_peak = risk_score

            has_priority = priority_order is not None
            final_exec_score = execution_priority_score if execution_priority_score is not None else risk_score
            final_risk_sev = risk_severity_score if risk_severity_score is not None else risk_score

            # FIX 1: Embed owner in reasoning JSON before INSERT
            reasoning = _embed_owner_in_reasoning(reasoning, owner)

            # Resolve execution_status default
            final_exec_status = (execution_status or status or 'NOT_STARTED').upper()
            if final_exec_status in ('UNKNOWN', ''):
                final_exec_status = 'NOT_STARTED'
            final_risk_status = risk_status or 'OPEN'

            tracker_sql = """
                INSERT INTO tracker_items
                (project_id, source_document_id, item_type, reference_id, title, is_out_of_scope,
                 risk_score, execution_priority_score, risk_severity_score, previous_highest_score,
                 risk_level, risk_category, risk_origin,
                 confidence, reasoning, requires_escalation, risk_source{priority_col},
                 status, execution_status, risk_status, graph_role, canonical_id, recommended_action)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s{priority_placeholder}, %s, %s, %s, %s, %s, %s)
            """.format(
                priority_col=", priority_order" if has_priority else "",
                priority_placeholder=", %s" if has_priority else ""
            )
            extra_vals = (priority_order,) if has_priority else ()
            db_cursor.execute(tracker_sql, (
                project_id, document_id, item_type, reference_id, title, int(is_out_of_scope),
                risk_score, final_exec_score, final_risk_sev, new_peak,
                risk_level, risk_category, risk_origin_value,
                confidence, reasoning, int(requires_escalation), risk_source
            ) + extra_vals + (
                status, final_exec_status, final_risk_status,
                graph_role or 'DOWNSTREAM_ACTIVITY',
                canonical_id or '',
                recommended_action
            ))
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
            "owner": owner,
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
