import sys
import os
import json

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.database import get_db_connection
from repositories.baseline_repository import BaselineRepository
from services.final_baseline_service import FinalBaselineService

conn = get_db_connection()
try:
    print("--- BaselineRepository.get_latest_baseline_details(conn, 126) ---")
    data = BaselineRepository.get_latest_baseline_details(conn, 126)
    if not data:
        print("Data is None!")
    else:
        print("Data keys:", list(data.keys()))
        print("id:", data.get("id"))
        print("scope_items len:", len(data.get("scope_items", [])))
        print("deliverables len:", len(data.get("deliverables", [])))
        print("work_items len:", len(data.get("work_items", [])))
        print("all_records len:", len(data.get("all_records", [])))
        print("milestones len:", len(data.get("milestones", [])))

    print("\n--- FinalBaselineService.load_validated_final_baseline(126) ---")
    final_res = FinalBaselineService.load_validated_final_baseline(project_id=126)
    print("Final status:", final_res.get("status"))
    print("Final message:", final_res.get("message"))
    art = final_res.get("artifact") or {}
    print("Artifact keys:", list(art.keys()))
    print("Artifact summary:", art.get("summary"))
finally:
    conn.close()

