from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from typing import List, Optional
from core.database import get_db, get_db_connection
from api.dependencies.auth import get_current_user, verify_project_access
from services.rag_service import RAGService
from services.llm_service import LLMService
from services.project_knowledge_service import ProjectKnowledgeService
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

@router.get("/suggestions")
def get_project_suggestions(
    project_id: int,
    current_user: dict = Depends(get_current_user),
    db: mysql.connector.connection.MySQLConnection = Depends(get_db)
):
    verify_project_access(project_id, current_user, db)
    cursor = db.cursor(dictionary=True)
    
    # 1. Fetch project
    cursor.execute("SELECT project_name FROM projects WHERE id = %s", (project_id,))
    project = cursor.fetchone()
    project_name = project["project_name"] if project else "the project"
    
    # 2. Fetch documents
    cursor.execute("SELECT document_name, document_type FROM documents WHERE project_id = %s LIMIT 5", (project_id,))
    docs = cursor.fetchall()
    doc_details = [f"{d['document_name']} ({d['document_type']})" for d in docs]
    
    # 3. Fetch scope items
    cursor.execute("SELECT name, scope_type FROM scope_items WHERE project_id = %s LIMIT 5", (project_id,))
    scope_items = cursor.fetchall()
    scope_details = [f"{s['name']} ({s['scope_type']})" for s in scope_items]
    
    cursor.close()
    
    # 4. Generate suggestions using LLM
    prompt = f"""You are the Project AI Assistant. Based on the following project context, generate exactly 4 highly specific, relevant, and realistic questions that a user (e.g. project manager, delivery lead, reviewer) would want to ask to understand the project's scope, deliverables, or exclusions.
Make the questions directly reference the specific documents or scope items listed below.

Project Name: {project_name}
Uploaded Documents: {", ".join(doc_details) if doc_details else "None"}
Baseline Scope Items: {", ".join(scope_details) if scope_details else "None"}

Requirements:
- Generate exactly 4 questions.
- Each question must be realistic and refer to actual files or scope items if available.
- Keep them under 15 words each.
- Do not use markdown bullet characters, dashes (-), or hashes (#).
- Return the response as a JSON array of strings, e.g., ["Question 1", "Question 2", "Question 3", "Question 4"].
- Return ONLY the raw JSON array. Do not wrap in ```json or any other text.
"""
    try:
        res_text = LLMService.generate(prompt).strip()
        # Clean any markdown block formatting
        if res_text.startswith("```"):
            res_text = res_text.replace("```json", "").replace("```", "").strip()
        suggestions = json.loads(res_text)
        if not isinstance(suggestions, list) or len(suggestions) < 2:
            raise ValueError("Invalid format")
    except Exception as e:
        print(f"Failed to generate dynamic suggestions using LLM: {e}")
        # Clean fallback
        suggestions = [
            f"What is the status of deliverables in {project_name}?",
            f"Are there any scope deviations in {project_name}?",
            f"Summarize the key exclusions from the contract documents",
            f"What are the main client dependencies for {project_name}?"
        ]
        
    return {"success": True, "data": suggestions[:4]}

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
    
    # 0. Check if this is the first message in the session to rename it dynamically (from 'New Chat')
    cursor.execute("SELECT COUNT(*) as count FROM rag_chat_messages WHERE session_id = %s", (session_id,))
    msg_count_res = cursor.fetchone()
    is_first_msg = (msg_count_res["count"] == 0) if msg_count_res else True
    
    if is_first_msg:
        try:
            # Let LLM generate a short title based on query
            title_prompt = f"Given the user query: '{query}', generate a short, descriptive 3 to 4 word title for this chat session. Do not use quotes, hashtags, or formatting. Return ONLY the 3-4 word title."
            short_title = LLMService.generate(title_prompt).strip().strip('"').strip("'").rstrip('.').strip()
            # If the LLM returned nothing or something too long, fallback
            if not short_title or len(short_title.split()) > 5:
                short_title = " ".join(query.split()[:4])
                if len(query.split()) > 4:
                    short_title += "..."
        except Exception:
            short_title = " ".join(query.split()[:4])
            if len(query.split()) > 4:
                short_title += "..."
        
        cursor.execute("UPDATE rag_chat_sessions SET session_name = %s WHERE id = %s", (short_title, session_id))
        db.commit()

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
    
    # 2.5 Retrieve PM Execution Engine context
    pm_context_str = ProjectKnowledgeService.get_pm_execution_context(cursor, project_id)
    
    # 3. Construct prompt
    prompt = f"""You are the Project AI Assistant. Your job is to answer the user's question accurately and professionally, based on the provided project contexts.
You have access to three sources of information:
1. STRUCTURED SCOPE ITEMS (From MySQL): Approved items currently in the scope baseline (In Scope or Out of Scope), including manual additions.
2. SOURCE DOCUMENT EXCERPTS (From Vector DB / ChromaDB): Paragraphs retrieved from all project documents (EL, IFA, MOMs, Status Reports).
3. PM EXECUTION ENGINE & DEPENDENCY GRAPH: The active project milestones, sequential dependencies, critical execution blockers, and external customer dependencies.

=== USER QUERY ===
{query}

=== CONTEXT SOURCE 1: STRUCTURED SCOPE ITEMS (MySQL) ===
{mysql_context_str}

=== CONTEXT SOURCE 2: SOURCE DOCUMENT EXCERPTS (Vector DB) ===
{chroma_context_str}

=== CONTEXT SOURCE 3: PM EXECUTION ENGINE & DEPENDENCY GRAPH ===
{pm_context_str}

=== INSTRUCTIONS ===
- Answer the user's query precisely using ONLY the provided contexts. If the context does not contain the answer, say "I cannot find the answer in the project documents."
- For questions about dependencies, timelines, root causes, external blockers, or parallel execution, USE CONTEXT SOURCE 3.
- Ground your answer in the provided facts. Do not assume or extrapolate.
- CRITICAL: Do NOT use the hyphen/dash symbol (-) or asterisk (*) as bullet points, separators, or list markers.
- CRITICAL: Do NOT output raw horizontal line dividers (like ---).
- Structure your response cleanly, using double newlines for paragraph breaks and numbered points (e.g., 1., 2., 3.) with bold titles for any lists.
- Example of correct points format:
  1. **Point Title**: Details of the point.
  2. **Point Title**: Details of the point.
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
