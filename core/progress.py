import threading
from typing import Dict, Any, Optional

class ProgressManager:
    _lock = threading.Lock()
    _progress: Dict[str, Dict[str, Any]] = {}  # key: f"{project_id}_{document_id}" -> progress dict

    @classmethod
    def set_progress(
        cls, 
        project_id: int, 
        document_id: int, 
        stage: str, 
        progress: int, 
        status: str, 
        details: Optional[Dict[str, Any]] = None,
        error: Optional[str] = None
    ):
        key = f"{project_id}_{document_id}"
        with cls._lock:
            existing = cls._progress.get(key, {})
            existing_details = existing.get("details", {})
            if details:
                # Merge details dictionary
                new_details = {**existing_details, **details}
            else:
                new_details = existing_details

            cls._progress[key] = {
                "currentStage": stage,
                "progress": progress,
                "status": status,
                "details": new_details,
                "error": error
            }

    @classmethod
    def get_progress(cls, project_id: int, document_id: int) -> Optional[Dict[str, Any]]:
        key = f"{project_id}_{document_id}"
        with cls._lock:
            return cls._progress.get(key)

    @classmethod
    def get_active_progress_for_project(cls, project_id: int) -> Optional[Dict[str, Any]]:
        prefix = f"{project_id}_"
        with cls._lock:
            # Look for any running progress for this project
            for key, val in cls._progress.items():
                if key.startswith(prefix) and val.get("status") == "running":
                    doc_id = int(key.split("_")[1])
                    return {**val, "document_id": doc_id}
            
            # Fallback: return the latest updated progress for this project (running or not)
            for key, val in cls._progress.items():
                if key.startswith(prefix):
                    doc_id = int(key.split("_")[1])
                    return {**val, "document_id": doc_id}
            return None
