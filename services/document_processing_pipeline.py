import json
from agents.risk_evaluator_subagents import ActivityExtractorAgent
from typing import Callable, Optional

class DocumentProcessingPipeline:
    @classmethod
    def process_document(cls, project_id: int, document_id: int, text: str, db_cursor, emit: Optional[Callable[[str, int], None]] = None):
        """
        The orchestrated Enterprise PMO pipeline (Unified Risk Tracker):
        1. Document Fact Extraction (LLM)
        2. Normalization & Deterministic Classification (Risk Engine)
        3. Relationship Traversal & Execution Unlock (Risk Engine)
        4. Risk Evaluation & Project State (Risk Engine)
        5. Risk Reconciliation & Persistence (Risk Engine -> tracker_items)
        """
        def _emit(msg, pct):
            if emit:
                emit(msg, pct)
                
        _emit("Fact Extraction via LLM", 20)
        # ── Step 1: LLM Extraction ────────────────────────────────────────────
        active_tracker_block = "None"
        try:
            db_cursor.execute(
                "SELECT id, title, status FROM tracker_items WHERE project_id = %s AND status = 'OPEN'",
                (project_id,)
            )
            active_items = db_cursor.fetchall()
            active_items_list = [
                f"- {r['title'] if isinstance(r, dict) else r[1]}"
                for r in active_items
            ]
            active_tracker_block = "\n".join(active_items_list) if active_items_list else "None"
        except Exception as e:
            print("Could not fetch active tracker items:", e)
            
        extraction_result = ActivityExtractorAgent.extract_activities(text, active_tracker_block)
        
        _emit("Deterministic Classification", 40)
        try:
            from services.pmo_classifier import PMOClassifier
            from services.entity_normalizer_service import EntityNormalizerService
            
            # Load baseline canonical names
            normalizer = EntityNormalizerService()
            db_cursor.execute("SELECT name FROM scope_items WHERE project_id = %s", (project_id,))
            baseline_titles = [r['name'] if isinstance(r, dict) else r[0] for r in db_cursor.fetchall()]
            baseline_canonical_names = set(normalizer.normalize(t, project_id) for t in baseline_titles)
            
            # Apply deterministic classification
            items = extraction_result.get("activities", [])
            if not items:
                items = extraction_result.get("extractions", [])
                
            for item in items:
                item["classification_type"] = PMOClassifier.classify(item, baseline_canonical_names)
        except Exception as e:
            print(f"Failed to apply PMOClassifier: {e}")
            
        _emit("Enterprise Risk Engine Evaluation", 80)
        # ── Step 2-5: Risk Engine ─────────────────────────────────────────────
        try:
            from agents.risk_evaluation_agent import RiskEvaluationAgent
            # Pass our pre-extracted result to the Risk Engine which natively handles
            # categorization, relationships, dependencies, and state analysis.
            activity_map = {
                'pre_extracted_activities': extraction_result,
            }
            RiskEvaluationAgent.evaluate_document(project_id, document_id, text, db_cursor, activity_map, emit=emit)
        except Exception as e:
            import traceback
            traceback.print_exc()
            print(f"Enterprise risk engine failed: {e}")
            
        _emit("Pipeline Complete", 100)
