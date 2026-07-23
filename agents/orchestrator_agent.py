from agents.status_ingestion_agent import StatusIngestionAgent
from agents.risk_evaluation_agent import RiskEvaluationAgent

class OrchestratorAgent:
    @classmethod
    def run_workflow(cls, project_id: int, document_id: int, text: str, db_cursor, progress_callback=None):
        """
        Coordinates the document monitoring workflow.
        Follows the Plan-and-Execute pattern:
        1. Delegates parsing to StatusIngestionAgent.
        2. Manages state by inserting basic activities and requests into the database.
        3. Delegates advanced risk evaluation to RiskEvaluationAgent.
        """
        # 1. Delegate to Status Ingestion Agent to extract structured JSON from raw text
        if progress_callback:
            progress_callback("Extracting Activities", 30, "running")
            
        extracted_data = StatusIngestionAgent.extract_status(text)
        
        # 2. Manage State: Insert standard items into the database and retrieve maps
        activity_map, request_map = cls._persist_ingested_data(project_id, document_id, extracted_data, db_cursor)
        
        # 3. Delegate to Risk Evaluation Agent for multi-agent scope and timeline analysis
        try:
            RiskEvaluationAgent.evaluate_document(
                project_id=project_id,
                document_id=document_id,
                document_text=text,
                db_cursor=db_cursor,
                activity_map=activity_map,
                request_map=request_map,
                progress_callback=progress_callback
            )
        except Exception as e:
            print(f"Warning: Multi-Agent risk evaluation failed. Termination handled gracefully. Error: {e}")
            if progress_callback:
                progress_callback("Saving Results", 90, "failed", error=str(e))
            raise e
            
    @classmethod
    def _persist_ingested_data(cls, project_id: int, document_id: int, extracted_data: dict, db_cursor):
        """
        Helper function to persist the ingested activities and requests.
        Returns maps mapping lowered clean names to database IDs.
        """
        activity_map = {}
        request_map = {}

        # --- 1. Process Activities ---
        for item in extracted_data.get("activities", []):
            name = item.get("activity_name", "Unknown")
            sql = """INSERT INTO project_activities 
                     (project_id, document_id, activity_name, description, activity_status, progress_percentage, requested_by, owner, mentioned_deadline, source_page, source_section, evidence_text, confidence)
                     VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"""
            db_cursor.execute(sql, (
                project_id, document_id, name, item.get("description", ""),
                item.get("activity_status", "UNKNOWN"), item.get("progress_percentage"), item.get("requested_by"),
                item.get("owner"), item.get("mentioned_deadline"), item.get("source_page"),
                item.get("source_section"), item.get("evidence_text", ""), item.get("confidence", 0.5)
            ))
            activity_id = db_cursor.lastrowid
            activity_map[name.lower().strip()] = activity_id
            
        # --- 2. Process New Requests ---
        for item in extracted_data.get("new_requests", []):
            name = item.get("request_name", "Unknown")
            sql = """INSERT INTO new_requests
                     (project_id, document_id, request_name, requested_by, request_status, source_page, evidence_text)
                     VALUES (%s, %s, %s, %s, 'DETECTED', %s, %s)"""
            db_cursor.execute(sql, (
                project_id, document_id, name, item.get("requested_by"),
                item.get("source_page"), item.get("evidence_text", "")
            ))
            request_id = db_cursor.lastrowid
            request_map[name.lower().strip()] = request_id

        return activity_map, request_map
