import os
import sys
import shutil
import mysql.connector
import dotenv
dotenv.load_dotenv()

# Force utf-8 stdout to avoid charmap codec errors in Windows
sys.stdout.reconfigure(encoding='utf-8')

# Setup paths
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from core.config import settings
from core.database import get_db_connection
from repositories.document_repository import DocumentRepository
from services.document_service import DocumentService
from agents.orchestrator_agent import OrchestratorAgent

project_id = 52 # User's current active project
docs = [
    "MoM_Week_14_Project_Status.docx",
    "Week-18-MOM.docx",
    "Week-24-MOM.docx",
    "Week-31-MOM.docx",
    "Week-34-MOM.docx",
    "Week-36-MOM.docx"
]

def main():
    conn = get_db_connection()
    if not conn:
        print("Failed to connect to DB")
        return

    cursor = conn.cursor(dictionary=True)

    print("==================================================")
    print("Starting Enterprise Lifecycle Validation Sequence")
    print("==================================================")

    # Clean up previous runs for project 52 to allow sequential testing
    print("Cleaning up previous test data for Project 52...")
    cursor.execute("DELETE FROM audit_logs WHERE project_id = %s", (project_id,))
    cursor.execute("DELETE FROM tracker_items WHERE project_id = %s", (project_id,))
    cursor.execute("DELETE FROM documents WHERE project_id = %s AND document_type = 'MOM'", (project_id,))
    conn.commit()

    desktop_dir = r"c:\Users\ADMIN\Desktop\Agent-2"
    storage_dir = os.path.join(settings.UPLOAD_PATH, str(project_id))
    os.makedirs(storage_dir, exist_ok=True)

    for doc_name in docs:
        print(f"\n---> Processing Document: {doc_name} <---")
        source_path = os.path.join(desktop_dir, doc_name)
        if not os.path.exists(source_path):
            print(f"File not found: {source_path}")
            continue

        ext = os.path.splitext(doc_name)[1].lower()
        storage_key = os.path.join(storage_dir, doc_name)
        shutil.copy2(source_path, storage_key)

        # Create Document in DB
        doc_id = DocumentRepository.create_document(
            db=conn,
            project_id=project_id,
            document_name=doc_name,
            document_type="MOM",
            storage_key=storage_key,
            uploaded_by=2 # assuming user ID 2 is Engagement Manager
        )
        conn.commit()

        print(f"[{doc_name}] Saved as ID {doc_id}")

        # Parse text
        chunks = DocumentService.parse_document(storage_key, ext)
        text = "\n".join([chunk["text"] for chunk in chunks[:8]])
        if len(text) > 8000:
            text = text[:8000]

        # Run Workflow
        try:
            OrchestratorAgent.run_workflow(project_id, doc_id, text, cursor)
            conn.commit()
            print(f"[{doc_name}] Workflow completed successfully.")
        except Exception as e:
            conn.rollback()
            print(f"[{doc_name}] Workflow failed: {e}")

        # Fetch Active Risks
        cursor.execute("SELECT id, title, status, risk_category, reference_id FROM tracker_items WHERE project_id = %s AND status = 'OPEN'", (project_id,))
        active_risks = cursor.fetchall()
        print(f"\n[ACTIVE RISKS after {doc_name}]: {len(active_risks)}")
        for r in active_risks:
            print(f" - {r['title']} [{r['risk_category']}]")

        # Fetch Resolved Risks
        cursor.execute("SELECT id, title, status, resolution, risk_category FROM tracker_items WHERE project_id = %s AND status = 'RESOLVED' AND source_document_id = %s", (project_id, doc_id))
        resolved_in_doc = cursor.fetchall()
        print(f"\n[RISKS RESOLVED during {doc_name}]: {len(resolved_in_doc)}")
        for r in resolved_in_doc:
            res_str = r['resolution'] or "No explicit reason"
            print(f" - {r['title']} -> {res_str}")
            
    cursor.close()
    conn.close()

if __name__ == "__main__":
    main()
