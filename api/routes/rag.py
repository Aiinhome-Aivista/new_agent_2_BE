from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from typing import List, Optional
from core.database import get_db, get_db_connection
from api.dependencies.auth import get_current_user, verify_project_access
from services.rag_service import RAGService
from services.llm_service import LLMService
from fastapi.responses import FileResponse
import mysql.connector
import json

router = APIRouter()

# Schema Setup logic
def create_rag_tables():
    conn = get_db_connection()
    if not conn:
        print("RAG Table Setup: Failed to connect to database")
        return
    cursor = conn.cursor()
    try:
        # Create sessions table
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS rag_chat_sessions (
            id BIGINT PRIMARY KEY AUTO_INCREMENT,
            project_id BIGINT NOT NULL,
            session_name VARCHAR(255) NOT NULL,
            created_by BIGINT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
            CONSTRAINT fk_rag_session_project FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE,
            CONSTRAINT fk_rag_session_user FOREIGN KEY (created_by) REFERENCES users(id) ON DELETE CASCADE
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
        """)
        
        # Create messages table
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS rag_chat_messages (
            id BIGINT PRIMARY KEY AUTO_INCREMENT,
            session_id BIGINT NOT NULL,
            role ENUM('USER', 'ASSISTANT') NOT NULL,
            content TEXT NOT NULL,
            citations_json JSON NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            CONSTRAINT fk_rag_message_session FOREIGN KEY (session_id) REFERENCES rag_chat_sessions(id) ON DELETE CASCADE
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
        """)
        conn.commit()
        print("RAG tables successfully checked/created.")
    except Exception as e:
        print(f"RAG Tables Setup Error: {e}")
        conn.rollback()
    finally:
        cursor.close()
        conn.close()

# Auto setup tables on import
create_rag_tables()

# Request/Response schemas
class SessionCreate(BaseModel):
    session_name: str

class MessageCreate(BaseModel):
    query: str

@router.get("/sessions")
def get_chat_sessions(
    project_id: int,
    current_user: dict = Depends(get_current_user),
    db: mysql.connector.connection.MySQLConnection = Depends(get_db)
):
    verify_project_access(project_id, current_user, db)
    cursor = db.cursor(dictionary=True)
    cursor.execute("""
        SELECT rcs.*, u.name as creator_name 
        FROM rag_chat_sessions rcs
        JOIN users u ON rcs.created_by = u.id
        WHERE rcs.project_id = %s
        ORDER BY rcs.updated_at DESC
    """, (project_id,))
    sessions = cursor.fetchall()
    cursor.close()
    return {"success": True, "data": sessions}

@router.post("/sessions")
def create_chat_session(
    project_id: int,
    payload: SessionCreate,
    current_user: dict = Depends(get_current_user),
    db: mysql.connector.connection.MySQLConnection = Depends(get_db)
):
    verify_project_access(project_id, current_user, db)
    cursor = db.cursor(dictionary=True)
    
    cursor.execute("""
        INSERT INTO rag_chat_sessions (project_id, session_name, created_by)
        VALUES (%s, %s, %s)
    """, (project_id, payload.session_name, current_user["id"]))
    db.commit()
    
    session_id = cursor.lastrowid
    
    cursor.execute("""
        SELECT rcs.*, u.name as creator_name 
        FROM rag_chat_sessions rcs
        JOIN users u ON rcs.created_by = u.id
        WHERE rcs.id = %s
    """, (session_id,))
    new_session = cursor.fetchone()
    cursor.close()
    
    return {"success": True, "data": new_session}

@router.delete("/sessions/{session_id}")
def delete_chat_session(
    project_id: int,
    session_id: int,
    current_user: dict = Depends(get_current_user),
    db: mysql.connector.connection.MySQLConnection = Depends(get_db)
):
    verify_project_access(project_id, current_user, db)
    cursor = db.cursor(dictionary=True)
    
    # Check session ownership or project rights
    cursor.execute("SELECT * FROM rag_chat_sessions WHERE id = %s AND project_id = %s", (session_id, project_id))
    session = cursor.fetchone()
    if not session:
        cursor.close()
        raise HTTPException(status_code=404, detail="Session not found")
        
    cursor.execute("DELETE FROM rag_chat_sessions WHERE id = %s", (session_id,))
    db.commit()
    cursor.close()
    
    return {"success": True, "message": "Chat session deleted successfully"}

@router.get("/sessions/{session_id}/messages")
def get_session_messages(
    project_id: int,
    session_id: int,
    current_user: dict = Depends(get_current_user),
    db: mysql.connector.connection.MySQLConnection = Depends(get_db)
):
    verify_project_access(project_id, current_user, db)
    cursor = db.cursor(dictionary=True)
    
    # Verify session belongs to project
    cursor.execute("SELECT * FROM rag_chat_sessions WHERE id = %s AND project_id = %s", (session_id, project_id))
    if not cursor.fetchone():
        cursor.close()
        raise HTTPException(status_code=404, detail="Session not found")
        
    cursor.execute("""
        SELECT * FROM rag_chat_messages 
        WHERE session_id = %s 
        ORDER BY created_at ASC
    """, (session_id,))
    messages = cursor.fetchall()
    
    # Deserialize citations_json
    for msg in messages:
        if msg.get("citations_json"):
            try:
                msg["citations"] = json.loads(msg["citations_json"])
            except Exception:
                msg["citations"] = []
        else:
            msg["citations"] = []
            
    cursor.close()
    return {"success": True, "data": messages}

@router.post("/sessions/{session_id}/messages")
def send_chat_message(
    project_id: int,
    session_id: int,
    payload: MessageCreate,
    current_user: dict = Depends(get_current_user),
    db: mysql.connector.connection.MySQLConnection = Depends(get_db)
):
    verify_project_access(project_id, current_user, db)
    cursor = db.cursor(dictionary=True)
    
    # Verify session
    cursor.execute("SELECT * FROM rag_chat_sessions WHERE id = %s AND project_id = %s", (session_id, project_id))
    if not cursor.fetchone():
        cursor.close()
        raise HTTPException(status_code=404, detail="Session not found")
        
    query = payload.query
    
    # 1. Fetch structured scope baseline items from MySQL
    cursor.execute("""
        SELECT si.*, d.document_name 
        FROM scope_items si
        LEFT JOIN documents d ON si.source_document_id = d.id
        WHERE si.project_id = %s
    """, (project_id,))
    scope_items = cursor.fetchall()
    
    mysql_context = []
    if scope_items:
        for item in scope_items:
            doc_source = item.get("document_name") or "Manually Added"
            status_tag = f" ({item['status_change_tag']})" if item.get("status_change_tag") else ""
            mysql_context.append(
                f"- Name: {item['name']}\n"
                f"  Type: {item['scope_type']}\n"
                f"  Status: {item['completion_status']}{status_tag}\n"
                f"  Description: {item.get('description') or 'N/A'}\n"
                f"  Source Document: {doc_source}\n"
                f"  Evidence: {item.get('evidence_text') or 'N/A'}"
            )
        mysql_context_str = "\n".join(mysql_context)
    else:
        mysql_context_str = "No structured scope items found in the MySQL database."
        
    # 2. Retrieve document vector chunks from ChromaDB (RAG)
    try:
        retrieved_chunks = RAGService.retrieve_evidence(project_id, query)
    except Exception as e:
        print(f"RAG Retrieval error: {e}")
        retrieved_chunks = []
        
    context_chunks = []
    citations = []
    for item in retrieved_chunks:
        meta = item.get("metadata", {})
        doc_name = meta.get("document_name", "Unknown Document")
        doc_id = meta.get("document_id")
        page = meta.get("page_idx", 0) + 1  # Convert to 1-based page
        text = item.get("text", "")
        context_chunks.append(f"Document: {doc_name} (Page {page})\nExcerpt: {text}\n---")
        citations.append({
            "document_id": doc_id,
            "document_name": doc_name,
            "page": page,
            "text": text
        })
    chroma_context_str = "\n".join(context_chunks) if context_chunks else "No relevant document excerpts found in Vector DB."
    
    # 3. Construct prompt
    prompt = f"""You are the Project AI Assistant. Your job is to answer the user's question accurately and professionally, based on the provided project contexts.
You have access to two sources of information:
1. STRUCTURED SCOPE ITEMS (From MySQL): Approved items currently in the scope baseline (In Scope or Out of Scope), including manual additions.
2. SOURCE DOCUMENT EXCERPTS (From Vector DB / ChromaDB): Paragraphs retrieved from all project documents (EL, IFA, MOMs, Status Reports).

=== USER QUERY ===
{query}

=== CONTEXT SOURCE 1: STRUCTURED SCOPE ITEMS (MySQL) ===
{mysql_context_str}

=== CONTEXT SOURCE 2: SOURCE DOCUMENT EXCERPTS (Vector DB) ===
{chroma_context_str}

=== INSTRUCTIONS ===
- Answer the user's query precisely using ONLY the provided contexts. If the context does not contain the answer, say "I cannot find the answer in the project documents."
- Ground your answer in the provided facts. Do not assume or extrapolate.
- Structure your answer cleanly with bullet points if helpful.
- Cite the source documents, page numbers, or baseline items you referenced in your response.
"""
    
    # 4. Generate AI response
    try:
        answer_text = LLMService.generate(prompt)
    except Exception as e:
        cursor.close()
        raise HTTPException(status_code=500, detail=f"AI generation failed: {e}")
        
    # 5. Save messages to DB
    cursor.execute("""
        INSERT INTO rag_chat_messages (session_id, role, content)
        VALUES (%s, 'USER', %s)
    """, (session_id, query))
    
    cursor.execute("""
        INSERT INTO rag_chat_messages (session_id, role, content, citations_json)
        VALUES (%s, 'ASSISTANT', %s, %s)
    """, (session_id, answer_text, json.dumps(citations)))
    db.commit()
    
    assistant_msg_id = cursor.lastrowid
    
    cursor.execute("SELECT * FROM rag_chat_messages WHERE id = %s", (assistant_msg_id,))
    assistant_msg = cursor.fetchone()
    assistant_msg["citations"] = citations
    
    # Update session updated_at
    cursor.execute("UPDATE rag_chat_sessions SET updated_at = NOW() WHERE id = %s", (session_id,))
    db.commit()
    
    cursor.close()
    return {"success": True, "data": assistant_msg}
