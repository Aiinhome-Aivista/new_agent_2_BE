from datetime import datetime, timezone

class CommitmentMonitoringEngine:
    @classmethod
    def evaluate(cls, state_snapshot, llm_extracted_activities, all_baseline_items, project_id, resolved_items=None, db_cursor=None, document_id=None, document_date=None):
        """
        Compares expected baseline milestones against MoM extraction to find missing updates.
        Returns a list of synthetic risks to inject into the graph.
        """
        synthetic_risks = []
        # Risk date calculation: today's real date is taken as priority for overdue risk calculation
        # even if any date is mentioned in MoM.
        try:
            from datetime import date
            today = date.today()
        except Exception:
            today = datetime.now(timezone.utc).date()
                
        # What did the LLM extract as having an update?
        extracted_names = [a.get("canonical_title", "").strip().lower() for a in llm_extracted_activities if a.get("canonical_title")]
        extracted_names.extend([a.get("_canonical_title", "").strip().lower() for a in llm_extracted_activities if a.get("_canonical_title")])
        extracted_names.extend([a.get("activity", "").strip().lower() for a in llm_extracted_activities if a.get("activity")])
        extracted_names.extend([a.get("statement", "").strip().lower() for a in llm_extracted_activities if a.get("statement")])

        # Include resolved items from this document so completed deliverables are not marked missing
        for res in (resolved_items or []):
            if res.get("name"):
                extracted_names.append(res["name"].strip().lower())
            if res.get("canonical_name"):
                extracted_names.append(res["canonical_name"].strip().lower())
        
        def _is_title_match(a: str, b: str) -> bool:
            if not a or not b: return False
            import re, difflib
            a_c = re.sub(r'[^a-zA-Z0-9]', '', a).lower()
            b_c = re.sub(r'[^a-zA-Z0-9]', '', b).lower()
            if not a_c or not b_c: return False
            if a_c == b_c or a_c in b_c or b_c in a_c: return True
            return difflib.SequenceMatcher(None, a_c, b_c).ratio() >= 0.80

        # Collect all items to evaluate:
        # 1. Project milestones from state_snapshot
        items_to_evaluate = []
        seen_names = set()

        for m_id, name in state_snapshot.milestone_id_to_name.items():
            if str(m_id).startswith("VIRTUAL_"):
                continue
            status = state_snapshot.get_status(m_id)
            planned_date = state_snapshot.get_date(m_id)
            if planned_date:
                items_to_evaluate.append({
                    "id": m_id,
                    "name": name,
                    "status": status,
                    "planned_date": planned_date,
                    "is_recurring": False,
                })
                seen_names.add(name.strip().lower())

        # 2. Scope baseline items (including recurring child occurrences and scheduled deliverables)
        scope_rows = []
        if db_cursor and project_id:
            try:
                db_cursor.execute("""
                    SELECT id, name, deadline, completion_status, is_recurring, parent_scope_item_id
                    FROM scope_items
                    WHERE project_id = %s
                      AND (deadline IS NOT NULL OR is_recurring = 1)
                """, (project_id,))
                scope_rows = db_cursor.fetchall() or []
            except Exception as e:
                print(f"  [CommitmentMonitor] Warning: Failed to query scope_items: {e}")
                scope_rows = []

        if not scope_rows and all_baseline_items:
            scope_rows = all_baseline_items

        for si in scope_rows:
            si_id = si.get("id") if isinstance(si, dict) else si[0]
            si_name = si.get("name") if isinstance(si, dict) else si[1]
            si_deadline = si.get("deadline") if isinstance(si, dict) else (si[2] if len(si) > 2 else None)
            si_status = si.get("completion_status") if isinstance(si, dict) else (si[3] if len(si) > 3 else "ACTIVE")
            si_rec = si.get("is_recurring") if isinstance(si, dict) else (si[4] if len(si) > 4 else 0)
            si_parent = si.get("parent_scope_item_id") if isinstance(si, dict) else (si[5] if len(si) > 5 else None)

            if not si_name or not si_deadline:
                continue

            clean_name = si_name.strip().lower()
            if clean_name in seen_names:
                continue
            seen_names.add(clean_name)

            items_to_evaluate.append({
                "id": si_id,
                "name": si_name,
                "status": (si_status or "ACTIVE").upper().replace(" ", "_"),
                "planned_date": si_deadline,
                "is_recurring": bool(si_rec or si_parent),
            })

        for item in items_to_evaluate:
            item_id = item["id"]
            name = item["name"]
            status = item["status"]
            planned_date = item["planned_date"]
            is_rec = item.get("is_recurring", False)

            if status in ["COMPLETED", "CANCELLED", "RESOLVED"]:
                continue

            try:
                p_date = planned_date.date() if isinstance(planned_date, datetime) else planned_date
                if isinstance(p_date, str):
                    p_date = datetime.strptime(p_date.split(' ')[0], "%Y-%m-%d").date()
                days_overdue = (today - p_date).days
            except Exception:
                continue

            if is_rec:
                print(f"  [CommitmentMonitor] Checking recurring occurrence: '{name}' deadline={p_date}, reference_date={today}")

            # If it's overdue or due within the next 30 days, we EXPECT an update
            if days_overdue >= -30:
                name_clean = name.strip().lower()

                # Was it mentioned or resolved?
                mentioned = False
                for e_name in extracted_names:
                    if not e_name: continue
                    if e_name == name_clean or e_name in name_clean or name_clean in e_name:
                        mentioned = True
                        break
                    if _is_title_match and _is_title_match(name, e_name):
                        mentioned = True
                        break

                if not mentioned:
                    # GAP 3 FIX: Before raising a risk, check if this occurrence was
                    # retroactively completed in a later document.
                    if days_overdue > 0 and db_cursor is not None:
                        # Build a synthetic occurrence dict from item data
                        occurrence_for_check = {
                            "id": item_id if not str(item_id).startswith("VIRTUAL_") else None,
                            "name": name,
                        }
                        already_resolved = cls._retroactively_resolve_if_completed(
                            db_cursor=db_cursor,
                            project_id=project_id,
                            occurrence=occurrence_for_check,
                            document_id=document_id,
                        )
                        if already_resolved:
                            continue  # Don't create a risk item — it's done

                        # Double check deliverable_progress for COMPLETED status
                        if item_id and not str(item_id).startswith("VIRTUAL_"):
                            try:
                                db_cursor.execute("""
                                    SELECT status_code FROM deliverable_progress
                                    WHERE scope_item_id = %s ORDER BY id DESC LIMIT 1
                                """, (item_id,))
                                dp_row = db_cursor.fetchone()
                                if dp_row:
                                    sc = dp_row.get('status_code') if isinstance(dp_row, dict) else dp_row[0]
                                    if sc in ['COMPLETED', 'RESOLVED', 'CANCELLED']:
                                        continue
                            except Exception:
                                pass

                    if days_overdue > 0:
                        # Past due date with no evidence -> OVERDUE
                        # Use occurrence name (could be "CSI Process – Month 1") NOT parent name
                        tracker_title = name
                        if is_rec:
                            print(f"  [CommitmentMonitor] OVERDUE: '{tracker_title}' is overdue with no completion record -> creating tracker risk item")
                        synthetic_risks.append({
                            "activity": f"Overdue Commitment: {tracker_title}",
                            "_canonical_title": tracker_title,
                            "status": "OVERDUE",
                            "entity_type": "COMMITMENT_RISK",
                            "evidence_text": f"Milestone was expected by {p_date} ({days_overdue} days ago), but no completion evidence has been recorded.",
                            "reasoning": f"Milestone '{tracker_title}' is past its committed completion date ({p_date}) and no update was provided in the latest Meeting Minutes.",
                            "classification_type": "RISK",
                            "confidence": 100,
                            "days_overdue": days_overdue,
                            "planned_date": str(p_date),
                            "owner": "Internal",
                        })
                    else:
                        # Approaching due date with no evidence -> MONITORING / NO_EVIDENCE_YET
                        synthetic_risks.append({
                            "activity": f"Missing Update: {name}",
                            "_canonical_title": name,
                            "status": "NO_EVIDENCE_YET",
                            "entity_type": "COMMITMENT_RISK",
                            "evidence_text": f"Milestone is approaching due date on {p_date}, but no update was provided in recent Meeting Minutes.",
                            "reasoning": f"Milestone '{name}' is approaching its target date ({p_date}), but the latest meeting minutes contained no update on progress.",
                            "classification_type": "RISK",
                            "confidence": 100,
                            "days_overdue": 0,
                            "days_until_due": abs(days_overdue),
                            "planned_date": str(p_date),
                            "owner": "Internal",
                        })

        return synthetic_risks

    @staticmethod
    def _retroactively_resolve_if_completed(
        db_cursor,
        project_id: int,
        occurrence: dict,
        document_id: int,
    ) -> bool:
        """
        Checks if a deliverable_progress COMPLETED record exists for this
        occurrence in ANY document (including documents newer than the
        occurrence's deadline). If yes, auto-resolve the tracker item.

        This handles: "Month 1 was overdue, but Month 2 MoM confirmed it done."

        Returns True if the occurrence was resolved, False if still overdue.
        Generic: works for any recurring item.
        """
        try:
            occ_id = occurrence.get('id')
            if not occ_id:
                return False

            # Check if ANY deliverable_progress record for this occurrence is COMPLETED
            db_cursor.execute("""
                SELECT id, source_document_id FROM deliverable_progress
                WHERE scope_item_id = %s AND status_code = 'COMPLETED'
                ORDER BY id DESC LIMIT 1
            """, (occ_id,))
            completed_row = db_cursor.fetchone()

            if not completed_row:
                return False  # Still genuinely overdue

            # Completed progress exists -> resolve the tracker item for this occurrence
            occ_name = occurrence.get('name', '')
            print(f"  [CommitmentMonitor] Retroactive resolution: '{occ_name}' "
                  f"was overdue but has COMPLETED progress -> resolving tracker item")

            try:
                from agents.tracker_audit_agent import TrackerAuditAgent
                TrackerAuditAgent.persist_tracker_item(
                    db_cursor, project_id, document_id, 'ACTIVITY',
                    False, 0, 'LOW', 'RESOLVED',
                    1.0,
                    f"Retroactively resolved: completion evidence found in a later document.",
                    False,
                    title=occ_name,
                    status='RESOLVED',
                    resolve_only=True,
                    risk_status='RESOLVED',
                )
            except Exception as e:
                print(f"  [CommitmentMonitor] Warning: retroactive resolve failed: {e}")

            return True
        except Exception as e:
            print(f"  [CommitmentMonitor] Warning: _retroactively_resolve_if_completed failed: {e}")
            return False
