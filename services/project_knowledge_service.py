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

            # FIX 3: Inject near-deadline milestone context so LLM
            # correctly assesses immediate vs future business impact.
            # APPROACHING_DEADLINE_DAYS = 14 is configurable here.
            APPROACHING_DEADLINE_DAYS = 14
            try:
                db_cursor.execute("""
                    SELECT name, planned_date, status
                    FROM project_milestones
                    WHERE project_id = %s
                      AND status NOT IN ('COMPLETED', 'RESOLVED')
                      AND planned_date IS NOT NULL
                      AND planned_date >= CURDATE()
                      AND planned_date <= DATE_ADD(CURDATE(), INTERVAL %s DAY)
                    ORDER BY planned_date ASC
                """, (project_id, APPROACHING_DEADLINE_DAYS))
                approaching_milestones = db_cursor.fetchall()
                
                if approaching_milestones:
                    lines.append(
                        f"\n=== APPROACHING DEADLINE (within {APPROACHING_DEADLINE_DAYS} days) ==="
                    )
                    lines.append(
                        "IMPORTANT: Dependencies blocking these milestones have HIGH URGENCY and IMMEDIATE business impact. "
                        "Treat blockers of these milestones as critical."
                    )
                    for m in approaching_milestones:
                        m_name = m['name'] if isinstance(m, dict) else m[0]
                        m_date = m['planned_date'] if isinstance(m, dict) else m[1]
                        m_status = m['status'] if isinstance(m, dict) else m[2]
                        if m_date:
                            if isinstance(m_date, str):
                                try:
                                    m_date = datetime.strptime(m_date.split(" ")[0], "%Y-%m-%d").date()
                                except ValueError:
                                    pass
                            if isinstance(m_date, date):
                                days_left = (m_date - today).days
                                lines.append(
                                    f"- {m_name}: due in {days_left} days (planned: {m_date}, status: {m_status})"
                                )
                            else:
                                lines.append(
                                    f"- {m_name}: deadline approaching (planned: {m_date}, status: {m_status})"
                                )
            except Exception as e:
                print(f"  [Warning] Could not fetch approaching milestones: {e}")
            
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
        Gathers comprehensive live PM execution data (milestones, dependency graph, open risks,
        resolved risks, and summary metrics) to inject into the AI Chat Assistant.
        """
        lines = []
        from datetime import date, datetime
        today = date.today()
        
        try:
            # 1. Project Milestones & Status
            db_cursor.execute("""
                SELECT id, name, status, planned_date 
                FROM project_milestones 
                WHERE project_id = %s 
                ORDER BY id ASC
            """, (project_id,))
            milestones = db_cursor.fetchall()
            
            completed_milestones = 0
            in_progress_milestones = 0
            blocked_milestones = 0
            overdue_milestones = 0
            
            milestone_lines = []
            if milestones:
                for m in milestones:
                    m_name = m['name'] if isinstance(m, dict) else m[1]
                    m_status = m['status'] if isinstance(m, dict) else m[2]
                    m_date = m['planned_date'] if isinstance(m, dict) else m[3]
                    m_id = m['id'] if isinstance(m, dict) else m[0]
                    
                    status_upper = str(m_status).upper()
                    if status_upper in ["COMPLETED", "RESOLVED"]:
                        completed_milestones += 1
                    elif status_upper == "IN_PROGRESS":
                        in_progress_milestones += 1
                    elif status_upper in ["BLOCKED", "WAITING"]:
                        blocked_milestones += 1
                        
                    date_info = ""
                    if m_date:
                        d_obj = None
                        if isinstance(m_date, str):
                            try:
                                d_obj = datetime.strptime(m_date.split(" ")[0], "%Y-%m-%d").date()
                            except ValueError:
                                pass
                        elif isinstance(m_date, date):
                            d_obj = m_date
                            
                        if d_obj:
                            days_diff = (d_obj - today).days
                            if days_diff < 0 and status_upper not in ["COMPLETED", "RESOLVED"]:
                                overdue_milestones += 1
                                date_info = f" | Planned: {d_obj} (OVERDUE by {abs(days_diff)} days)"
                            elif days_diff == 0:
                                date_info = f" | Planned: {d_obj} (DUE TODAY)"
                            else:
                                date_info = f" | Planned: {d_obj} (Due in {days_diff} days)"
                                
                    milestone_lines.append(f"- {m_name} | Status: {m_status}{date_info} (Milestone ID: {m_id})")
            
            # 2. Milestone Dependency Graph
            db_cursor.execute("""
                SELECT p.name AS parent_name, c.name AS child_name,
                       COALESCE(md.dependency_type, 'FINISH_TO_START') AS dependency_type
                FROM milestone_dependencies md
                JOIN project_milestones p ON md.parent_milestone_id = p.id
                JOIN project_milestones c ON md.child_milestone_id = c.id
                WHERE md.project_id = %s
            """, (project_id,))
            dependencies = db_cursor.fetchall()
            
            dep_lines = []
            if dependencies:
                for d in dependencies:
                    p_name = d['parent_name'] if isinstance(d, dict) else d[0]
                    c_name = d['child_name'] if isinstance(d, dict) else d[1]
                    dep_type = d['dependency_type'] if isinstance(d, dict) else d[2]
                    dep_lines.append(f"- {p_name} -> {c_name} [{dep_type}]")
                    
            # 3. Active & Open Risks (Tracker Items with status = 'OPEN')
            db_cursor.execute("""
                SELECT title, item_type, risk_category, risk_level, status, 
                       execution_priority_score, risk_score, graph_role, reasoning, recommended_action
                FROM tracker_items
                WHERE project_id = %s AND status = 'OPEN'
                ORDER BY execution_priority_score DESC, risk_score DESC
            """, (project_id,))
            open_tracker_items = db_cursor.fetchall() or []
            
            # 4. Resolved Risks (Tracker Items with status = 'RESOLVED')
            db_cursor.execute("""
                SELECT title, item_type, risk_category, risk_level, status, resolution, reasoning, resolved_at
                FROM tracker_items
                WHERE project_id = %s AND status = 'RESOLVED'
                ORDER BY id DESC
            """, (project_id,))
            resolved_tracker_items = db_cursor.fetchall() or []
            
            # ── BUILD STRUCTURED PM CONTEXT ──
            lines.append("=== LIVE PROJECT EXECUTION & RISK REGISTER SUMMARY (MySQL Single Source of Truth) ===")
            lines.append(f"• Total Milestones: {len(milestones)} (Completed: {completed_milestones}, In Progress: {in_progress_milestones}, Blocked: {blocked_milestones}, Overdue: {overdue_milestones})")
            lines.append(f"• Total Open/Active Risks & Blockers: {len(open_tracker_items)}")
            lines.append(f"• Total Resolved Risks & Completed Actions: {len(resolved_tracker_items)}")
            
            lines.append("\n--- PROJECT MILESTONES & STATUS ---")
            if milestone_lines:
                lines.extend(milestone_lines)
            else:
                lines.append("No milestones defined.")
                
            lines.append("\n--- MILESTONE DEPENDENCY GRAPH (EXECUTION SEQUENCE) ---")
            if dep_lines:
                lines.extend(dep_lines)
            else:
                lines.append("No sequential dependencies defined.")
                
            lines.append(f"\n--- ACTIVE / OPEN RISKS & BLOCKERS (Total: {len(open_tracker_items)}) ---")
            if open_tracker_items:
                for t in open_tracker_items:
                    title = t['title'] if isinstance(t, dict) else t[0]
                    itype = t['item_type'] if isinstance(t, dict) else t[1]
                    cat = t['risk_category'] if isinstance(t, dict) else t[2]
                    lvl = t['risk_level'] if isinstance(t, dict) else t[3]
                    prio = t.get('execution_priority_score') if isinstance(t, dict) else t[5]
                    role = t.get('graph_role') if isinstance(t, dict) else t[7]
                    reason = t.get('reasoning') if isinstance(t, dict) else t[8]
                    rec = t.get('recommended_action') if isinstance(t, dict) else t[9]
                    
                    details = f"• [{cat}] {title} | Severity: {lvl} | Graph Role: {role or 'ACTIVITY'} | Priority Score: {prio or 'N/A'}"
                    if reason:
                        clean_reason = str(reason).strip()
                        # If reasoning is a JSON PMO narrative, extract the executive summary or clean text
                        if clean_reason.startswith("{") and "executive_summary" in clean_reason:
                            try:
                                import json
                                parsed = json.loads(clean_reason)
                                clean_reason = parsed.get("executive_summary") or parsed.get("gap_analysis") or clean_reason
                            except Exception:
                                pass
                        details += f"\n  Why at Risk / Blocker: {clean_reason[:300]}"
                    if rec:
                        details += f"\n  Action to Unblock: {rec}"
                    lines.append(details)
            else:
                lines.append("No active open risks or blockers.")
                
            lines.append(f"\n--- RESOLVED RISKS & CLOSED ITEMS (Total: {len(resolved_tracker_items)}) ---")
            if resolved_tracker_items:
                for r in resolved_tracker_items:
                    title = r['title'] if isinstance(r, dict) else r[0]
                    cat = r['risk_category'] if isinstance(r, dict) else r[2]
                    res = r.get('resolution') if isinstance(r, dict) else r[5]
                    lines.append(f"- [{cat}] {title} | Status: RESOLVED {f'({res})' if res else ''}")
            else:
                lines.append("No resolved risks recorded yet.")
                
        except Exception as e:
            print(f"Failed to build PM Execution Context: {e}")
            return ""

        return "\n".join(lines)
