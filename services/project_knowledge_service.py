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
                SELECT si.id, si.name, si.scope_type, si.category, si.milestone, si.deadline, si.deadline_text
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
                SELECT si.id, si.name, si.scope_type, si.category, si.milestone, si.deadline, si.deadline_text
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
        try:
            from tools.mcp_tools import MCPTools
            evidence_chunks = MCPTools.search_baseline(project_id, query) or []
        except Exception as e:
            print(f"  [Warning] Baseline evidence search failed: {e}")
            evidence_chunks = []

        for chunk in evidence_chunks[:3]:  # Max 3 chunks per activity to keep context compact
            text = chunk.get("text", "").strip()
            if text:
                context_lines.append(f"Evidence: {text[:300]}")

        if not context_lines:
            context_lines.append("No baseline context found for this activity.")

        return "\n".join(context_lines)

    @staticmethod
    def calculate_milestone_progress(db_cursor, project_id: int) -> str:
        """
        Calculates deterministic progress based on weighted milestone mapping.
        Also includes overdue and blocked milestones for LLM prompt awareness (Problem 7).
        """
        try:
            from datetime import datetime, date
            
            # Find the active baseline for the project
            db_cursor.execute("SELECT id FROM scope_baselines WHERE project_id = %s AND status = 'APPROVED' ORDER BY version DESC LIMIT 1", (project_id,))
            baseline = db_cursor.fetchone()
            if not baseline:
                # Fallback to latest draft if no approved baseline exists
                db_cursor.execute("SELECT id FROM scope_baselines WHERE project_id = %s ORDER BY version DESC LIMIT 1", (project_id,))
                baseline = db_cursor.fetchone()
            
            if not baseline:
                return "Milestone Progress: 0% (No baseline found)"

            # Assuming baseline is a dict or tuple. If tuple, get by index 0. If dict, get 'id'
            baseline_id = baseline["id"] if isinstance(baseline, dict) else baseline[0]

            # Calculate total weight and completed weight from scope_milestone_mapping
            query = """
                SELECT 
                    SUM(smm.weight) as total_weight,
                    SUM(CASE WHEN pm.status = 'COMPLETED' THEN smm.weight ELSE 0 END) as completed_weight
                FROM scope_milestone_mapping smm
                JOIN project_milestones pm ON smm.milestone_id = pm.id
                JOIN scope_items si ON smm.scope_item_id = si.id
                WHERE pm.baseline_id = %s
            """
            db_cursor.execute(query, (baseline_id,))
            result = db_cursor.fetchone()

            if not result:
                return "Milestone Progress: 0% (No milestones mapped)"

            # Support both dict and tuple formats based on cursor type
            total_weight = result['total_weight'] if isinstance(result, dict) else result[0]
            completed_weight = result['completed_weight'] if isinstance(result, dict) else result[1]

            if total_weight is None or total_weight == 0:
                return "Milestone Progress: 0% (No milestones mapped)"

            total = float(total_weight)
            completed = float(completed_weight or 0.0)
            percentage = int((completed / total) * 100)

            lines = [f"Milestone Progress: {percentage}% (Completed weight: {completed:.1f} / {total:.1f})"]
            
            # ── Problem 7: Overdue & Blocked milestone context for LLM awareness ──
            today = date.today()
            
            # Fetch overdue milestones (planned_date < today AND status != COMPLETED)
            try:
                db_cursor.execute("""
                    SELECT name, planned_date, status 
                    FROM project_milestones 
                    WHERE project_id = %s AND status != 'COMPLETED' AND planned_date IS NOT NULL AND planned_date < CURDATE()
                    ORDER BY planned_date ASC
                """, (project_id,))
                overdue_milestones = db_cursor.fetchall()
                
                if overdue_milestones:
                    lines.append("\n=== OVERDUE MILESTONES (deadline has PASSED) ===")
                    lines.append("IMPORTANT: Any dependency blocking these milestones has IMMEDIATE impact (not future).")
                    for m in overdue_milestones:
                        m_name = m['name'] if isinstance(m, dict) else m[0]
                        m_date = m['planned_date'] if isinstance(m, dict) else m[1]
                        m_status = m['status'] if isinstance(m, dict) else m[2]
                        if m_date:
                            if isinstance(m_date, str):
                                try:
                                    m_date = datetime.strptime(m_date, "%Y-%m-%d").date()
                                except ValueError:
                                    pass
                            if isinstance(m_date, date):
                                days_overdue = (today - m_date).days
                                lines.append(f"- {m_name}: {days_overdue} days overdue (planned: {m_date}, status: {m_status})")
                            else:
                                lines.append(f"- {m_name}: OVERDUE (planned: {m_date}, status: {m_status})")
            except Exception as e:
                print(f"  [Warning] Could not fetch overdue milestones: {e}")
            
            # Fetch blocked milestones
            try:
                db_cursor.execute("""
                    SELECT name, planned_date, status 
                    FROM project_milestones 
                    WHERE project_id = %s AND status = 'BLOCKED'
                    ORDER BY planned_date ASC
                """, (project_id,))
                blocked_milestones = db_cursor.fetchall()
                
                if blocked_milestones:
                    lines.append("\n=== BLOCKED MILESTONES ===")
                    for m in blocked_milestones:
                        m_name = m['name'] if isinstance(m, dict) else m[0]
                        m_date = m['planned_date'] if isinstance(m, dict) else m[1]
                        lines.append(f"- {m_name} (planned: {m_date}, status: BLOCKED)")
            except Exception as e:
                print(f"  [Warning] Could not fetch blocked milestones: {e}")
            
            lines.append(f"\nDocument analysis date: {today.isoformat()}")
            
            return "\n".join(lines)
        except Exception as e:
            return f"Milestone Progress: Error calculating progress ({str(e)})"

    @staticmethod
    def get_dependency_context_block(dependency_graph: dict) -> str:
        """
        Formats the dependency graph into a concise block for LLM prompt injection.
        Only includes items that are incomplete AND blocking at least one other item.
        """
        if not dependency_graph:
            return ""

        blocker_lines = []
        for item_id, info in dependency_graph.items():
            if info.get("is_incomplete") and info.get("dependent_count", 0) > 0:
                blocked_names = ", ".join(info["blocked_items"])
                impact = "HIGH IMPACT" if info["dependent_count"] >= 2 else "MEDIUM IMPACT"
                blocker_lines.append(
                    f"- {info['name']} [{info['status']}] blocks: {blocked_names} ({impact})"
                )

        if not blocker_lines:
            return ""

        lines = ["=== DEPENDENCY RISK CONTEXT ==="]
        lines.extend(blocker_lines)
        return "\n".join(lines)

    @classmethod
    def get_pm_execution_context(cls, db_cursor, project_id: int) -> str:
        """
        Gathers comprehensive PM execution data (milestone graph, status, risks, and customer dependencies)
        to inject into the AI Chat Assistant.
        """
        lines = []
        
        try:
            # 1. Project Milestones & Status
            db_cursor.execute("SELECT id, name, status FROM project_milestones WHERE project_id = %s ORDER BY id ASC", (project_id,))
            milestones = db_cursor.fetchall()
            
            lines.append("--- PROJECT MILESTONES & STATUS ---")
            if milestones:
                for m in milestones:
                    lines.append(f"Milestone: {m['name']} | Status: {m['status']} (ID: {m['id']})")
            else:
                lines.append("No milestones defined.")
                
            # 2. Milestone Dependency Graph (Edges with dependency type)
            db_cursor.execute("""
                SELECT p.name AS parent_name, c.name AS child_name,
                       COALESCE(md.dependency_type, 'FINISH_TO_START') AS dependency_type
                FROM milestone_dependencies md
                JOIN project_milestones p ON md.parent_milestone_id = p.id
                JOIN project_milestones c ON md.child_milestone_id = c.id
                WHERE md.project_id = %s
            """, (project_id,))
            dependencies = db_cursor.fetchall()
            
            dep_type_labels = {
                "FINISH_TO_START": "Finish-to-Start (FS): must FINISH before child can START",
                "START_TO_START": "Start-to-Start (SS): must START before child can START",
                "FINISH_TO_FINISH": "Finish-to-Finish (FF): must FINISH before child can FINISH",
                "START_TO_FINISH": "Start-to-Finish (SF): must START before child can FINISH",
            }

            lines.append("\n--- MILESTONE DEPENDENCY GRAPH (EXECUTION SEQUENCE) ---")
            if dependencies:
                for d in dependencies:
                    dep_label = dep_type_labels.get(d['dependency_type'], d['dependency_type'])
                    lines.append(f"{d['parent_name']} -> {d['child_name']} [{dep_label}]")
            else:
                lines.append("No sequential dependencies defined.")
                
            # 3. Active Risks & External Dependencies (Tracker)
            db_cursor.execute("""
                SELECT title, item_type, risk_category, risk_level, status
                FROM tracker_items
                WHERE project_id = %s AND status = 'ACTIVE'
            """, (project_id,))
            tracker_items = db_cursor.fetchall()
            
            lines.append("\n--- ACTIVE RISKS & CUSTOMER DEPENDENCIES ---")
            if tracker_items:
                for t in tracker_items:
                    lines.append(f"[{t['risk_category']}] {t['title']} | Severity: {t['risk_level']} | Type: {t['item_type']}")
            else:
                lines.append("No active risks or external dependencies.")
                
        except Exception as e:
            print(f"Failed to build PM Execution Context: {e}")
            return ""

        return "\n".join(lines)
