# pyrefly: ignore [missing-import]
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from typing import List, Optional
from core.database import get_db, get_db_connection
from api.dependencies.auth import get_current_user, verify_project_access
from services.rag_service import RAGService
from services.llm_service import LLMService
from services.project_knowledge_service import ProjectKnowledgeService
from services.graph_rag_service import GraphRAGService
from services.rag_guardrail_service import RAGGuardrailService
# pyrefly: ignore [missing-import]
from fastapi.responses import FileResponse
import mysql.connector
import json

router = APIRouter()


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

    # 0. Fetch project name and evaluate safety guardrails
    cursor.execute("SELECT project_name FROM projects WHERE id = %s", (project_id,))
    proj_row = cursor.fetchone()
    project_name = proj_row["project_name"] if proj_row else "the project"

    guard_result = RAGGuardrailService.classify_and_guard(query, project_name=project_name)
    if not guard_result["is_in_domain"]:
        safe_answer = guard_result.get("safe_response") or RAGGuardrailService.SAFE_REFUSAL_MESSAGE
        cursor.execute("""
            INSERT INTO rag_chat_messages (session_id, role, content)
            VALUES (%s, 'USER', %s)
        """, (session_id, query))
        cursor.execute("""
            INSERT INTO rag_chat_messages (session_id, role, content, citations_json)
            VALUES (%s, 'ASSISTANT', %s, %s)
        """, (session_id, safe_answer, "[]"))
        db.commit()
        assistant_msg_id = cursor.lastrowid
        cursor.execute("SELECT * FROM rag_chat_messages WHERE id = %s", (assistant_msg_id,))
        assistant_msg = cursor.fetchone()
        assistant_msg["citations"] = []
        cursor.close()
        return {"success": True, "data": assistant_msg}

    if guard_result.get("needs_clarification") and guard_result.get("clarification_prompt"):
        clarify_answer = guard_result["clarification_prompt"]
        cursor.execute("""
            INSERT INTO rag_chat_messages (session_id, role, content)
            VALUES (%s, 'USER', %s)
        """, (session_id, query))
        cursor.execute("""
            INSERT INTO rag_chat_messages (session_id, role, content, citations_json)
            VALUES (%s, 'ASSISTANT', %s, %s)
        """, (session_id, clarify_answer, "[]"))
        db.commit()
        assistant_msg_id = cursor.lastrowid
        cursor.execute("SELECT * FROM rag_chat_messages WHERE id = %s", (assistant_msg_id,))
        assistant_msg = cursor.fetchone()
        assistant_msg["citations"] = []
        cursor.close()
        return {"success": True, "data": assistant_msg}

    if guard_result.get("safe_response"):
        # Greeting response
        greeting_answer = guard_result["safe_response"]
        cursor.execute("""
            INSERT INTO rag_chat_messages (session_id, role, content)
            VALUES (%s, 'USER', %s)
        """, (session_id, query))
        cursor.execute("""
            INSERT INTO rag_chat_messages (session_id, role, content, citations_json)
            VALUES (%s, 'ASSISTANT', %s, %s)
        """, (session_id, greeting_answer, "[]"))
        db.commit()
        assistant_msg_id = cursor.lastrowid
        cursor.execute("SELECT * FROM rag_chat_messages WHERE id = %s", (assistant_msg_id,))
        assistant_msg = cursor.fetchone()
        assistant_msg["citations"] = []
        cursor.close()
        return {"success": True, "data": assistant_msg}

    # 0.5. Check if this is the first message in the session to rename it dynamically (from 'New Chat')
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

    # 1. Fetch structured scope baseline items from MySQL (Latest Approved Baseline)
    cursor.execute("""
        SELECT id, version, status 
        FROM scope_baselines 
        WHERE project_id = %s AND status = 'APPROVED' 
        ORDER BY version DESC, id DESC 
        LIMIT 1
    """, (project_id,))
    active_baseline = cursor.fetchone()
    
    if not active_baseline:
        # Fallback to latest draft or any baseline if none is approved yet
        cursor.execute("""
            SELECT id, version, status 
            FROM scope_baselines 
            WHERE project_id = %s 
            ORDER BY version DESC, id DESC 
            LIMIT 1
        """, (project_id,))
        active_baseline = cursor.fetchone()
        
    scope_items = []
    baseline_info_str = "No baseline registered."
    
    if active_baseline:
        baseline_id = active_baseline['id'] if isinstance(active_baseline, dict) else active_baseline[0]
        baseline_ver = active_baseline['version'] if isinstance(active_baseline, dict) else active_baseline[1]
        baseline_stat = active_baseline['status'] if isinstance(active_baseline, dict) else active_baseline[2]
        
        cursor.execute("""
            SELECT si.*, d.document_name 
            FROM scope_items si
            LEFT JOIN documents d ON si.source_document_id = d.id
            WHERE si.project_id = %s AND si.baseline_id = %s
            ORDER BY si.scope_type ASC, si.id ASC
        """, (project_id, baseline_id))
        scope_items = cursor.fetchall() or []
        baseline_info_str = f"ACTIVE BASELINE: Version {baseline_ver} (Status: {baseline_stat})"
    else:
        # Direct fallback to all scope items for the project
        cursor.execute("""
            SELECT si.*, d.document_name 
            FROM scope_items si
            LEFT JOIN documents d ON si.source_document_id = d.id
            WHERE si.project_id = %s
            ORDER BY si.scope_type ASC, si.id ASC
        """, (project_id,))
        scope_items = cursor.fetchall() or []
        if scope_items:
            baseline_info_str = "ACTIVE BASELINE: Project Scope Items"
        else:
            baseline_info_str = "No baseline registered."
    
    mysql_context = []
    if scope_items:
        in_scope_items = [i for i in scope_items if i.get('scope_type') == 'IN_SCOPE']
        out_of_scope_items = [i for i in scope_items if i.get('scope_type') == 'OUT_OF_SCOPE']
        
        mysql_context.append(f"=== {baseline_info_str} ===")
        mysql_context.append(f"• Total Baseline Scope Items: {len(scope_items)} ({len(in_scope_items)} In-Scope Deliverables, {len(out_of_scope_items)} Out-of-Scope Exclusions)\n")
        
        mysql_context.append("--- IN-SCOPE DELIVERABLES & FEATURES ---")
        if in_scope_items:
            for item in in_scope_items:
                doc_source = item.get("document_name") or "Contract Baseline"
                status_tag = f" ({item['status_change_tag']})" if item.get("status_change_tag") else ""
                deadline_val = item.get('deadline_text') or item.get('deadline_original') or item.get('deadline_normalized') or item.get('deadline') or 'N/A'
                deadline_str = f" | Deadline: {deadline_val}"
                milestone_val = item.get('milestone') or item.get('milestone_normalized') or ""
                milestone_str = f" | Milestone: {milestone_val}" if milestone_val else ""
                cat_str = f" | Category: {item.get('category')}" if item.get('category') else ""
                
                mysql_context.append(
                    f"• {item['name']} [IN_SCOPE]{cat_str}{milestone_str}{deadline_str}\n"
                    f"  Status: {item.get('completion_status', 'ACTIVE')}{status_tag} | Source: {doc_source}\n"
                    f"  Description: {item.get('description') or 'N/A'}"
                )
        else:
            mysql_context.append("None")
            
        mysql_context.append("\n--- OUT-OF-SCOPE / EXCLUDED ITEMS ---")
        if out_of_scope_items:
            for item in out_of_scope_items:
                doc_source = item.get("document_name") or "Contract Baseline"
                mysql_context.append(
                    f"• {item['name']} [OUT_OF_SCOPE / EXCLUDED]\n"
                    f"  Source: {doc_source} | Description: {item.get('description') or 'N/A'}"
                )
        else:
            mysql_context.append("None")
            
        mysql_context_str = "\n".join(mysql_context)
    else:
        mysql_context_str = f"=== {baseline_info_str} ===\nNo structured scope items found in the database."
        
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

    # 2.6 Retrieve GraphRAG Topological Lineage & Simulation context
    graph_rag_context_str = GraphRAGService.get_graph_rag_context(cursor, project_id, query)

    # 2.7 Fetch recent conversation history for multi-turn conversational context
    cursor.execute("""
        SELECT role, content 
        FROM rag_chat_messages 
        WHERE session_id = %s 
        ORDER BY id DESC LIMIT 6
    """, (session_id,))
    recent_history_rows = cursor.fetchall() or []
    recent_history_rows.reverse()
    
    history_str = ""
    if recent_history_rows:
        h_lines = []
        for h in recent_history_rows:
            role_label = "User" if h.get("role") == "USER" else "Assistant"
            h_lines.append(f"{role_label}: {h.get('content', '')}")
        history_str = "\n".join(h_lines)
    
    # 3. Construct prompt with Enterprise Guardrails & Anti-Hallucination
    history_block = f"\n=== RECENT CONVERSATION HISTORY ===\n{history_str}\n" if history_str else ""
    
    prompt = f"""You are the Project AI Assistant. Your sole purpose is to provide factual, accurate, and professional information regarding THIS specific project, its contractual documents, deliverables, milestones, dependency graph, and risk tracker.

=== FOUR AUTHORITATIVE INFORMATION SOURCES ===
1. STRUCTURED SCOPE ITEMS (From MySQL): The active approved baseline deliverables and excluded items.
2. SOURCE DOCUMENT EXCERPTS (From Vector DB / ChromaDB): Extracted paragraphs from uploaded project documents (EL, IFA, MOMs, Status Reports) with page numbers.
3. LIVE PM EXECUTION ENGINE & RISK REGISTER (From MySQL): The real-time project metrics, milestone statuses, dependency chains, open risks, and resolved items.
4. GRAPHRAG TOPOLOGICAL REASONING (From Graph Engine): Upstream root causes, downstream cascade impact paths, and 'what-if' unblock simulations.
{history_block}
=== USER QUERY ===
{query}

=== CONTEXT SOURCE 1: STRUCTURED SCOPE ITEMS (MySQL) ===
{mysql_context_str}

=== CONTEXT SOURCE 2: SOURCE DOCUMENT EXCERPTS (Vector DB) ===
{chroma_context_str}

=== CONTEXT SOURCE 3: PM EXECUTION ENGINE & DEPENDENCY GRAPH ===
{pm_context_str}

=== CONTEXT SOURCE 4: GRAPHRAG TOPOLOGICAL LINEAGE & SIMULATION ===
{graph_rag_context_str}

{RAGGuardrailService.get_guardrail_system_instructions()}
=== FORMATTING & STYLE RULES ===
1. DIRECT PINPOINT ANSWER: Always start the response with a direct, 1-2 sentence pinpoint summary answering the user's question immediately.
2. CONCISE STRUCTURED BREAKDOWN: If the user requires supporting reasons, blockers, or milestone context, provide a brief numbered breakdown (1., 2., 3.) with bold titles. Keep each point short, direct, and free of fluff.
3. Do NOT use the hyphen/dash symbol (-) or asterisk (*) as bullet points, separators, or list markers.
4. Do NOT output raw horizontal line dividers (like ---).
5. Structure your response cleanly, using double newlines for paragraph breaks.
6. Cite the source documents, page numbers, or baseline items you referenced in your response.
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
