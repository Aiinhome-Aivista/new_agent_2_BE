from datetime import datetime, timezone

class CommitmentMonitoringEngine:
    @classmethod
    def evaluate(cls, state_snapshot, llm_extracted_activities, all_baseline_items, project_id, document_date=None):
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
        extracted_names = [a.get("canonical_title", "").strip().lower() for a in llm_extracted_activities]
        extracted_names.extend([a.get("_canonical_title", "").strip().lower() for a in llm_extracted_activities])
        
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
                
                # Was it mentioned?
                mentioned = False
                for e_name in extracted_names:
                    if e_name and (e_name == name_clean or e_name in name_clean or name_clean in e_name):
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
