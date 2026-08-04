from agents.status_ingestion_agent import StatusIngestionAgent
from agents.risk_evaluation_agent import RiskEvaluationAgent
from typing import Callable, Optional
import datetime
import re

def clean_date_value(val):
    if not val:
        return None
    val_str = str(val).strip()
    if val_str.upper() in ["N/A", "NONE", "NULL", "UNKNOWN", "PENDING", "", "TBD"]:
        return None
    
    # Split to get just the first word if it has noise, e.g. "2026-10-15 or later"
    val_str = val_str.split()[0]
    
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%d-%m-%Y", "%d/%m/%Y"):
        try:
            return datetime.datetime.strptime(val_str, fmt).date().strftime("%Y-%m-%d")
        except ValueError:
            pass
            
    match = re.search(r'(\d{4})[-/](\d{1,2})[-/](\d{1,2})', val_str)
    if match:
        try:
            return f"{match.group(1)}-{int(match.group(2)):02d}-{int(match.group(3)):02d}"
        except ValueError:
            pass
            
    return None

def clean_decimal_value(val):
    if val is None:
        return None
    val_str = str(val).strip().replace('%', '')
    if val_str.upper() in ["N/A", "NONE", "NULL", "UNKNOWN", ""]:
        return None
    try:
        match = re.search(r'[-+]?\d*\.\d+|\d+', val_str)
        if match:
            return float(match.group(0))
    except ValueError:
        pass
    return None

def clean_status_value(val):
    if not val:
        return "UNKNOWN"
    val_str = str(val).upper().strip().replace(' ', '_')
    allowed = {"NOT_STARTED", "PLANNED", "IN_PROGRESS", "COMPLETED", "BLOCKED", "DELAYED", "UNKNOWN"}
    if val_str in allowed:
        return val_str
    mappings = {
        "INPROGRESS": "IN_PROGRESS",
        "NOTSTARTED": "NOT_STARTED",
        "ON_HOLD": "BLOCKED",
        "ONHOLD": "BLOCKED",
        "ACTIVE": "IN_PROGRESS",
        "STARTED": "IN_PROGRESS",
        "COMPLETED": "COMPLETED",
        "DONE": "COMPLETED",
        "FINISHED": "COMPLETED",
    }
    return mappings.get(val_str, "UNKNOWN")

def clean_confidence_value(val):
    if val is None:
        return 0.5
    val_str = str(val).strip().replace('%', '')
    try:
        word_map = {
            "HIGH": 0.9,
            "MEDIUM": 0.7,
            "LOW": 0.4,
            "CRITICAL": 0.95
        }
        if val_str.upper() in word_map:
            return word_map[val_str.upper()]
        
        num = float(val_str)
        if num > 1.0:
            num = num / 100.0
        return min(max(num, 0.0), 1.0)
    except ValueError:
        return 0.5

class OrchestratorAgent:
    @classmethod
    def run_workflow(cls, project_id: int, document_id: int, text: str, db_cursor,
                     emit: Optional[Callable[[str, int], None]] = None):
        """
        Coordinates the document monitoring workflow.
        Follows the Plan-and-Execute pattern:
        1. Delegates parsing to StatusIngestionAgent.
        2. Manages state by inserting basic activities and requests into the database.
        3. Delegates advanced risk evaluation to RiskEvaluationAgent.

        emit(step_name, progress_percent) — optional SSE callback.
        If provided, progress events are streamed to the client in real time.
        If not provided, runs silently (backward compatible with /process endpoint).
        """
        def _emit(step: str, pct: int):
            if emit:
                emit(step, pct)

        # ── Step 0: Validate Document Order ─────────────────────────────────────
        from agents.execution_pipeline import TransitionValidator
        if not TransitionValidator.validate_document_order(db_cursor, project_id, document_id):
            print(f"Stale document detected (ID {document_id}). Aborting workflow to prevent regressions.")
            _emit("Stale Document Ignored", 100)
            return

        # ── Step 1: Status Ingestion ────────────────────────────────────────────
        _emit("Reading Uploaded Document", 15)
        extracted_data = StatusIngestionAgent.extract_status(text)

        # ── Step 2: Persist activities and requests ────────────────────────────
        _emit("Extracting Activities", 30)
        activity_map, request_map = cls._persist_ingested_data(
            project_id, document_id, extracted_data, db_cursor
        )

        # ── Step 3: Multi-Agent Risk Evaluation ───────────────────────────────
        try:
            RiskEvaluationAgent.evaluate_document(
                project_id=project_id,
                document_id=document_id,
                document_text=text,
                db_cursor=db_cursor,
                activity_map=activity_map,
                request_map=request_map,
                emit=emit,
            )
        except Exception as e:
            print(f"Warning: Multi-Agent risk evaluation failed. Termination handled gracefully. Error: {e}")
            raise e

    @classmethod
    def _persist_ingested_data(cls, project_id: int, document_id: int, extracted_data: dict, db_cursor):
        """
        Helper function to persist the ingested activities and requests.
        Returns maps mapping lowered clean names to database IDs.
        """
        activity_map = {}
        request_map = {}

        # --- 0. Clean up old data for this document ---
        db_cursor.execute("DELETE FROM project_activities WHERE document_id = %s", (document_id,))
        db_cursor.execute("DELETE FROM tracker_items WHERE source_document_id = %s", (document_id,))
        db_cursor.execute("DELETE FROM new_requests WHERE document_id = %s", (document_id,))
        
        # --- 1. Process Activities ---
        for item in extracted_data.get("activities", []):
            name = item.get("activity_name", "Unknown")
            sql = """INSERT INTO project_activities 
                     (project_id, document_id, activity_name, description, activity_status, progress_percentage, requested_by, owner, mentioned_deadline, source_page, source_section, evidence_text, confidence)
                     VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"""
            
            cleaned_deadline = clean_date_value(item.get("mentioned_deadline"))
            cleaned_progress = clean_decimal_value(item.get("progress_percentage"))
            cleaned_status = clean_status_value(item.get("activity_status"))
            cleaned_confidence = clean_confidence_value(item.get("confidence"))
            
            db_cursor.execute(sql, (
                project_id, document_id, name, item.get("description", ""),
                cleaned_status, cleaned_progress, item.get("requested_by"),
                item.get("owner"), cleaned_deadline, item.get("source_page"),
                item.get("source_section"), item.get("evidence_text", ""), cleaned_confidence
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

