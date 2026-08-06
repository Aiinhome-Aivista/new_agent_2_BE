import sys
import json
sys.path.append(r"c:\Users\ADMIN\Desktop\Agent-2\new_agent_2_BE")
from core.database import get_db_connection
from agents.risk_evaluation_agent import RiskEvaluationAgent
from services.document_service import DocumentService

def test():
    db = get_db_connection()
    cursor = db.cursor(dictionary=True)
    try:
        cursor.execute("SELECT storage_key, document_name FROM documents WHERE id = 14")
        doc = cursor.fetchone()
        import os
        ext = os.path.splitext(doc["storage_key"])[1].lower()
        chunks = DocumentService.parse_document(doc["storage_key"], ext)
        doc_text = "\n".join([chunk["text"] for chunk in chunks[:8]])
        print(f"DEBUG: doc_text length = {len(doc_text)}")
        print(f"DEBUG: doc_text snippet = {doc_text[:200]}")
        
        # We need to hook the RiskEvaluationAgent to print what's going on!
        result = RiskEvaluationAgent.evaluate_document(8, 14, doc_text, cursor)
        print("EVALUATION RESULT:")
        print(json.dumps(result, indent=2))
    except Exception as e:
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    test()
