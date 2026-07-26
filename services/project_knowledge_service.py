from tools.mcp_tools import MCPTools


class ProjectKnowledgeService:
    """
    Centralized Project Knowledge Service.
    Single source of truth for all baseline knowledge access.
    Used by Risk Evaluation, Baseline Extraction, Smart Diff, and future modules.
    """

    @classmethod
    def get_approved_baseline(cls, db_cursor, project_id: int) -> list:
        """
        Fetch IN_SCOPE items from the latest APPROVED baseline.
        Includes milestone, deadline, and full metadata for rich context building.
        """
        try:
            db_cursor.execute("""
                SELECT si.id, si.name, si.scope_type, si.milestone, si.deadline, si.deadline_text
                FROM scope_items si
                JOIN scope_baselines sb ON si.baseline_id = sb.id
                WHERE si.project_id = %s
                  AND si.scope_type = 'IN_SCOPE'
                  AND sb.status = 'APPROVED'
                  AND si.completion_status = 'ACTIVE'
                ORDER BY sb.id DESC, si.id ASC
            """, (project_id,))
            return db_cursor.fetchall() or []
        except Exception as e:
            print(f"Warning: Could not fetch approved scope items: {e}")
            return []

    @classmethod
    def get_full_baseline(cls, db_cursor, project_id: int) -> list:
        """
        Fetch ALL scope items from the latest APPROVED baseline — both IN_SCOPE and OUT_OF_SCOPE.
        Used for canonical name resolution only (e.g. "Voice Bot implementation" as OUT_OF_SCOPE
        should use the exact baseline wording, not the normalized activity name).
        """
        try:
            db_cursor.execute("""
                SELECT si.id, si.name, si.scope_type, si.milestone, si.deadline, si.deadline_text
                FROM scope_items si
                JOIN scope_baselines sb ON si.baseline_id = sb.id
                WHERE si.project_id = %s
                  AND sb.status = 'APPROVED'
                  AND si.completion_status = 'ACTIVE'
                ORDER BY sb.id DESC, si.id ASC
            """, (project_id,))
            return db_cursor.fetchall() or []
        except Exception as e:
            print(f"Warning: Could not fetch full baseline items: {e}")
            return []

    @classmethod
    def get_activity_context(cls, project_id: int, activity_name: str, matched_scope_item: dict = None) -> str:
        """
        STEP 5 (Baseline Context Builder): Builds a compact, targeted context string
        for a single ambiguous activity.

        If a matched_scope_item is provided (from deterministic matching), builds context
        from MySQL metadata directly without ChromaDB.
        
        For unmatched activities, performs a targeted ChromaDB hybrid retrieval
        to find relevant EL/IFA evidence — never retrieves the full baseline.

        Returns a compact string ready for LLM prompt injection.
        """
        context_lines = []

        # If we have a deterministic match, use MySQL metadata first
        if matched_scope_item:
            context_lines.append(f"Scope Item: {matched_scope_item.get('name', 'N/A')}")
            if matched_scope_item.get('milestone'):
                context_lines.append(f"Milestone: {matched_scope_item['milestone']}")
            if matched_scope_item.get('deadline') or matched_scope_item.get('deadline_text'):
                deadline = matched_scope_item.get('deadline_text') or str(matched_scope_item.get('deadline', ''))
                context_lines.append(f"Deadline: {deadline}")
            context_lines.append(f"Status: ACTIVE (Approved Baseline)")

        # Hybrid ChromaDB retrieval — narrow, activity-specific query only
        query = f"{activity_name} deadline milestone dependency customer responsibility exclusion"
        evidence_chunks = MCPTools.search_baseline(project_id, query)
        for chunk in evidence_chunks[:3]:  # Max 3 chunks per activity to keep context compact
            text = chunk.get("text", "").strip()
            if text:
                context_lines.append(f"Evidence: {text[:300]}")

        if not context_lines:
            context_lines.append("No baseline context found for this activity.")

        return "\n".join(context_lines)
