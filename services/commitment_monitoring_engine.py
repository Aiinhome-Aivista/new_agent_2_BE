from datetime import datetime, timezone

class CommitmentMonitoringEngine:
    @classmethod
    def evaluate(cls, state_snapshot, llm_extracted_activities, all_baseline_items, project_id, document_date=None, resolved_items=None):
        """
        Compares expected baseline milestones against MoM extraction to find missing updates.
        Returns a list of synthetic risks to inject into the graph.
        """
        synthetic_risks = []
        today = datetime.now(timezone.utc).date()
        if document_date:
            try:
                today = document_date.date() if isinstance(document_date, datetime) else document_date
            except:
                pass
                
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
        
        try:
            from api.routes.baseline import _is_title_match
        except Exception:
            _is_title_match = None

        for m_id, name in state_snapshot.milestone_id_to_name.items():
            # Only track actual project milestones, not virtual nodes
            if str(m_id).startswith("VIRTUAL_"): continue
            
            status = state_snapshot.get_status(m_id)
            if status in ["COMPLETED", "CANCELLED", "RESOLVED"]:
                continue
                
            planned_date = state_snapshot.get_date(m_id)
            if not planned_date:
                continue
                
            # If the item is expected to be done or in-progress right now
            try:
                p_date = planned_date.date() if isinstance(planned_date, datetime) else planned_date
                days_overdue = (today - p_date).days
            except:
                continue
                
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
                    if days_overdue > 0:
                        # Past due date with no evidence -> OVERDUE
                        synthetic_risks.append({
                            "activity": f"Overdue Commitment: {name}",
                            "_canonical_title": name,
                            "status": "OVERDUE",
                            "entity_type": "COMMITMENT_RISK",
                            "evidence_text": f"Milestone was expected by {planned_date} ({days_overdue} days ago), but no completion evidence has been recorded.",
                            "reasoning": f"Milestone '{name}' is past its committed completion date ({planned_date}) and no update was provided in the latest Meeting Minutes.",
                            "classification_type": "RISK",
                            "confidence": 100
                        })
                    else:
                        # Approaching due date with no evidence -> MONITORING / NO_EVIDENCE_YET
                        synthetic_risks.append({
                            "activity": f"Missing Update: {name}",
                            "_canonical_title": name,
                            "status": "NO_EVIDENCE_YET",
                            "entity_type": "COMMITMENT_RISK",
                            "evidence_text": f"Milestone is approaching due date on {planned_date}, but no update was provided in recent Meeting Minutes.",
                            "reasoning": f"Milestone '{name}' is approaching its target date ({planned_date}), but the latest meeting minutes contained no update on progress.",
                            "classification_type": "RISK",
                            "confidence": 100
                        })
                    
        return synthetic_risks
