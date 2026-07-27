# pyrefly: ignore [missing-import]
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from typing import List, Optional
import os
import difflib
from core.database import get_db, get_db_connection
from api.dependencies.auth import get_current_user, require_roles, verify_project_access
from services.document_service import DocumentService
from agents.scope_extraction_agent import ScopeExtractionAgent
from repositories.baseline_repository import BaselineRepository
from services.chroma_service import ChromaService
from services.embedding_service import EmbeddingService
from services.scope_section_detector import ScopeSectionDetector
from services.milestone_deadline_extractor import MilestoneDeadlineExtractor
from services.scope_candidate_extractor import ScopeCandidateExtractor
from services.scope_classifier import ScopeClassifier
from services.scope_deduplicator import ScopeDeduplicator
from services.normalization_service import NormalizationService
import mysql.connector
import threading

router = APIRouter()

def run_baseline_pipeline(project_id: int, document_id: int):
    # Establish a fresh connection for the thread
    thread_conn = get_db_connection()
    if not thread_conn:
        print("!!! Background baseline pipeline failed to connect !!!")
        return
        
    def emit(step: str, progress: int):
        try:
            update_conn = get_db_connection()
            if update_conn:
                upd_cursor = update_conn.cursor()
                upd_cursor.execute(
                    "UPDATE documents SET processing_progress = %s, processing_step = %s WHERE id = %s",
                    (progress, step, document_id)
                )
                update_conn.commit()
                upd_cursor.close()
                update_conn.close()
        except Exception as ex:
            print(f"Failed to update baseline progress in DB: {ex}")

    try:
        # Mark as PROCESSING and set start time
        upd = thread_conn.cursor()
        upd.execute(
            "UPDATE documents SET processing_status = 'PROCESSING', processing_progress = 5, processing_step = 'Detecting Scope Sections', processing_started_at = NOW() WHERE id = %s",
            (document_id,)
        )
        thread_conn.commit()
        upd.close()
        
        doc = BaselineRepository.get_document(thread_conn, document_id, project_id)
        if not doc:
            raise RuntimeError("Document not found")
            
        ext = os.path.splitext(doc["storage_key"])[1].lower()
        chunks = DocumentService.parse_document(doc["storage_key"], ext)
        
        # Pipeline Step 1: Detect Sections
        emit("Detecting Scope Sections", 15)
        chunks_with_sections = ScopeSectionDetector.detect_sections(chunks)
        
        # Pipeline Step 2: Extract Candidates
        emit("Extracting Scope Candidates", 30)
        raw_candidates = ScopeCandidateExtractor.extract_candidates(chunks_with_sections, document_id)
        
        # Pipeline Step 3: Classification
        emit("Classifying Scope Items", 50)
        import concurrent.futures
        classified_candidates = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
            def classify(candidate):
                return ScopeClassifier.classify_candidate(project_id, candidate)
            classified_candidates = list(executor.map(classify, raw_candidates))
            
        # Pipeline Step 4: Fuzzy Deduplication
        emit("Deduplicating Candidates", 70)
        deduped_candidates = ScopeDeduplicator.deduplicate(classified_candidates)
        
        # Pipeline Step 5: Milestone & Deadline Extraction
        emit("Extracting Milestones & Deadlines", 85)
        enriched_candidates = MilestoneDeadlineExtractor.extract(deduped_candidates)
        
        # Pipeline Step 6: Normalization and Diffing/Saving
        emit("Saving Baseline Draft", 95)
        for item in enriched_candidates:
            item["scope_item_normalized"] = NormalizationService.normalize_scope_item(item.get("name"))
            item["milestone_normalized"] = NormalizationService.normalize_milestone(item.get("milestone"), item.get("scope_item_normalized"))
            item["deadline_original"] = item.get("deadline_text")
            item["deadline_normalized"] = item.get("deadline")
            
        extracted_data = {
            "scope_items": enriched_candidates,
            "deliverables": [],
            "stakeholders": []
        }
        
        existing_draft = BaselineRepository.get_draft_baseline(thread_conn, project_id)
        if existing_draft:
            baseline_id = existing_draft["id"]
            BaselineRepository.update_baseline_source_document(thread_conn, baseline_id, document_id)
            BaselineRepository.delete_stakeholders_by_project(thread_conn, project_id)
        else:
            max_v = BaselineRepository.get_max_baseline_version(thread_conn, project_id)
            next_version = max_v + 1
            baseline_id = BaselineRepository.create_baseline(thread_conn, project_id, next_version, document_id)
            latest_approved = BaselineRepository.get_latest_approved_baseline(thread_conn, project_id)
            if latest_approved:
                app_baseline_id = latest_approved["id"]
                BaselineRepository.copy_scope_items(thread_conn, app_baseline_id, baseline_id)
                BaselineRepository.copy_deliverables(thread_conn, app_baseline_id, baseline_id)
            BaselineRepository.delete_stakeholders_by_project(thread_conn, project_id)
            
        has_approved = BaselineRepository.get_latest_approved_baseline(thread_conn, project_id) is not None
        existing_scope_items = BaselineRepository.get_scope_items_for_diff(thread_conn, baseline_id)
        
        for item in extracted_data.get("scope_items", []):
            item_name = item.get("name", "Unknown")
            item_type = item.get("scope_type", "UNCERTAIN")
            
            existing_item = None
            best_ratio = 0.0
            for db_item in existing_scope_items:
                ratio = difflib.SequenceMatcher(None, item_name.lower(), db_item["name"].lower()).ratio()
                if ratio > 0.8 and ratio > best_ratio:
                    best_ratio = ratio
                    existing_item = db_item
            
            if existing_item:
                tags = []
                old_type = existing_item["scope_type"]
                if has_approved:
                    if old_type != item_type:
                        tags.append(f"Changed from {old_type} to {item_type}")
                    old_deadline = existing_item.get("deadline_text")
                    new_deadline = item.get("deadline_text")
                    if old_deadline != new_deadline:
                        if not old_deadline and new_deadline:
                            tags.append(f"Deadline Added: {new_deadline}")
                        elif old_deadline and not new_deadline:
                            tags.append(f"Deadline Removed")
                        else:
                            tags.append(f"Deadline Changed: {old_deadline} -> {new_deadline}")
                    old_milestone = existing_item.get("milestone")
                    new_milestone = item.get("milestone")
                    if old_milestone != new_milestone:
                        if not old_milestone and new_milestone:
                            tags.append(f"Milestone Added: {new_milestone}")
                        elif old_milestone and not new_milestone:
                            tags.append(f"Milestone Removed")
                        else:
                            tags.append(f"Milestone Changed: {old_milestone} -> {new_milestone}")
                status_change_tag = " | ".join(tags) if tags else None
                BaselineRepository.update_scope_item(
                    db=thread_conn,
                    item_id=existing_item["id"],
                    description=item.get("description", ""),
                    scope_type=item_type,
                    source_document_id=document_id,
                    source_page=item.get("source_page"),
                    source_section=item.get("source_section"),
                    evidence_text=item.get("evidence_text", ""),
                    confidence=item.get("confidence", 0.5),
                    status_change_tag=status_change_tag,
                    deadline=item.get("deadline"),
                    milestone=item.get("milestone"),
                    deadline_text=item.get("deadline_text"),
                    extraction_confidence=item.get("extraction_confidence"),
                    extraction_method=item.get("extraction_method"),
                    scope_item_normalized=item.get("scope_item_normalized"),
                    milestone_normalized=item.get("milestone_normalized"),
                    deadline_original=item.get("deadline_original"),
                    deadline_normalized=item.get("deadline_normalized")
                )
            else:
                BaselineRepository.insert_scope_item_extracted(
                    db=thread_conn,
                    baseline_id=baseline_id,
                    project_id=project_id,
                    name=item_name,
                    description=item.get("description", ""),
                    scope_type=item_type,
                    source_document_id=document_id,
                    source_page=item.get("source_page"),
                    source_section=item.get("source_section"),
                    evidence_text=item.get("evidence_text", ""),
                    confidence=item.get("confidence", 0.5),
                    deadline=item.get("deadline"),
                    milestone=item.get("milestone"),
                    deadline_text=item.get("deadline_text"),
                    extraction_confidence=item.get("extraction_confidence"),
                    extraction_method=item.get("extraction_method"),
                    scope_item_normalized=item.get("scope_item_normalized"),
                    milestone_normalized=item.get("milestone_normalized"),
                    deadline_original=item.get("deadline_original"),
                    deadline_normalized=item.get("deadline_normalized")
                )
                
        # UPSERT deliverables
        existing_deliverables = BaselineRepository.get_deliverables_for_diff(thread_conn, baseline_id)
        for item in extracted_data.get("deliverables", []):
            item_name = item.get("name", "Unknown")
            deadline = item.get("deadline") if item.get("deadline") else None
            
            existing_deliv = None
            best_ratio = 0.0
            for db_item in existing_deliverables:
                ratio = difflib.SequenceMatcher(None, item_name.lower(), db_item["name"].lower()).ratio()
                if ratio > 0.8 and ratio > best_ratio:
                    best_ratio = ratio
                    existing_deliv = db_item
            
            if existing_deliv:
                BaselineRepository.update_deliverable(
                    db=thread_conn,
                    item_id=existing_deliv["id"],
                    description=item.get("description", ""),
                    deadline=deadline,
                    owner=item.get("owner"),
                    source_document_id=document_id
                )
            else:
                BaselineRepository.insert_deliverable(
                    db=thread_conn,
                    baseline_id=baseline_id,
                    project_id=project_id,
                    name=item_name,
                    description=item.get("description", ""),
                    deadline=deadline,
                    owner=item.get("owner"),
                    source_document_id=document_id
                )
                
        # Insert stakeholders
        for stakeholder in extracted_data.get("stakeholders", []):
            BaselineRepository.insert_stakeholder(
                db=thread_conn,
                project_id=project_id,
                name=stakeholder.get("name", "Unknown"),
                email=stakeholder.get("email"),
                role=stakeholder.get("role"),
                responsibility=stakeholder.get("responsibility")
            )
            
        BaselineRepository.update_project_monitoring_status(thread_conn, project_id, 'BASELINE_PENDING_REVIEW')
        
        # Mark as COMPLETED
        upd2 = thread_conn.cursor()
        upd2.execute(
            "UPDATE documents SET processing_status = 'COMPLETED', processing_progress = 100, processing_step = 'Completed' WHERE id = %s",
            (document_id,)
        )
        thread_conn.commit()
        upd2.close()
        
    except Exception as e:
        import traceback
        print("!!! Baseline pipeline execution failed !!!")
        traceback.print_exc()
        try:
            thread_conn.rollback()
        except Exception:
            pass
        try:
            err_conn = get_db_connection()
            if err_conn:
                err_cursor = err_conn.cursor()
                err_cursor.execute(
                    "UPDATE documents SET processing_status = 'FAILED', processing_error = %s, processing_progress = 0, processing_step = 'Failed' WHERE id = %s",
                    (str(e)[:500], document_id)
                )
                err_conn.commit()
                err_cursor.close()
                err_conn.close()
        except Exception as db_ex:
            traceback.print_exc()
    finally:
        thread_conn.close()

@router.post("/extract")
def extract_baseline(project_id: int, document_id: int, current_user: dict = Depends(require_roles(["ADMIN", "ENGAGEMENT_MANAGER"])), db: mysql.connector.connection.MySQLConnection = Depends(get_db)):
    verify_project_access(project_id, current_user, db)
    
    doc = BaselineRepository.get_document(db, document_id, project_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
        
    if doc["document_type"] not in ["EL", "IFA"]:
        raise HTTPException(status_code=400, detail="Only EL and IFA can be used for baseline extraction")
        
    # Prevent concurrent baseline extraction on the same project
    cursor = db.cursor(dictionary=True)
    cursor.execute(
        "SELECT id FROM documents WHERE project_id = %s AND processing_status = 'PROCESSING' LIMIT 1",
        (project_id,)
    )
    active_proc = cursor.fetchone()
    cursor.close()
    if active_proc or doc.get("processing_status") == "PROCESSING":
        return {"success": True, "message": "Baseline extraction already in progress for this project", "data": {"baseline_id": None}}
        
    thread = threading.Thread(
        target=run_baseline_pipeline,
        args=(project_id, document_id),
        daemon=True
    )
    thread.start()
    
    return {"success": True, "message": "Baseline extraction started", "data": {"baseline_id": None}}

@router.get("/")
def get_baseline(project_id: int, current_user: dict = Depends(get_current_user), db: mysql.connector.connection.MySQLConnection = Depends(get_db)):
    verify_project_access(project_id, current_user, db)
    data = BaselineRepository.get_latest_baseline_details(db, project_id)
    return {"success": True, "data": data}

@router.get("/versions")
def get_baseline_versions(project_id: int, current_user: dict = Depends(get_current_user), db: mysql.connector.connection.MySQLConnection = Depends(get_db)):
    verify_project_access(project_id, current_user, db)
    baselines = BaselineRepository.get_all_baseline_versions(db, project_id)
    return {"success": True, "data": baselines}

@router.post("/approve")
def approve_baseline(project_id: int, current_user: dict = Depends(require_roles(["ENGAGEMENT_MANAGER", "ADMIN"])), db: mysql.connector.connection.MySQLConnection = Depends(get_db)):
    verify_project_access(project_id, current_user, db)
    
    baseline = BaselineRepository.get_draft_baseline(db, project_id)
    if not baseline:
        raise HTTPException(status_code=404, detail="No draft baseline found")
        
    BaselineRepository.approve_baseline(db, baseline["id"], current_user["id"])
    BaselineRepository.update_project_monitoring_status(db, project_id, 'ACTIVE')
    db.commit()
    
    return {"success": True, "message": "Baseline approved. Project is now ACTIVE."}

class ScopeItemCreate(BaseModel):
    name: str
    description: Optional[str] = None
    scope_type: str = "IN_SCOPE"
    evidence_text: Optional[str] = None
    confidence: Optional[float] = 1.0
    milestone: Optional[str] = None
    deadline_text: Optional[str] = None
    deadline: Optional[str] = None

@router.post("/items")
def add_scope_item(
    project_id: int,
    item: ScopeItemCreate,
    current_user: dict = Depends(require_roles(["ADMIN", "ENGAGEMENT_MANAGER"])),
    db: mysql.connector.connection.MySQLConnection = Depends(get_db)
):
    verify_project_access(project_id, current_user, db)
    
    baseline = BaselineRepository.get_latest_baseline(db, project_id)
    if not baseline:
        baseline_id = BaselineRepository.create_simple_baseline(db, project_id, 'DRAFT')
        source_document_id = None
        db.commit()
    else:
        baseline_id = baseline["id"]
        source_document_id = baseline.get("source_document_id")
        
    item_id = BaselineRepository.create_scope_item(
        db=db,
        baseline_id=baseline_id,
        project_id=project_id,
        name=item.name,
        description=item.description or "",
        scope_type=item.scope_type,
        evidence_text=item.evidence_text or "Manually added item",
        confidence=item.confidence if item.confidence is not None else 1.0,
        source_document_id=source_document_id,
        deadline=item.deadline,
        milestone=item.milestone,
        deadline_text=item.deadline_text
    )
    db.commit()
    
    try:
        # Sync to Vector DB so it can be queried by agents
        text_to_embed = f"{item.name}: {item.description or ''}\nReasoning: {item.evidence_text or ''}"
        embeddings = EmbeddingService.encode_batch([text_to_embed])
        
        # Determine baseline_version (0 for draft)
        version_rec = BaselineRepository.get_latest_approved_baseline(db, project_id)
        version = version_rec["version"] if version_rec and "version" in version_rec else 0

        chunk = {
            "chunk_index": item_id, # Use item_id to make it unique
            "text": text_to_embed,
            "page_number": 0,
            "scope_item_normalized": NormalizationService.normalize_scope_item(item.name),
            "scope_type": item.scope_type,
            "milestone_normalized": NormalizationService.normalize_milestone(item.milestone, NormalizationService.normalize_scope_item(item.name)) if item.milestone else "NULL",
            "deadline_original": item.deadline_text or "NULL",
            "deadline_normalized": item.deadline or "NULL",
            "baseline_version": version,
            "status": "APPROVED" if version > 0 else "DRAFT"
        }
        ChromaService.add_chunks(
            project_id=project_id,
            document_id=source_document_id or 0,
            document_name="Manual Addition",
            document_type="MANUAL_SCOPE",
            chunks=[chunk],
            embeddings=embeddings
        )
    except Exception as e:
        print(f"Warning: Failed to sync manual scope item to ChromaDB: {e}")
    
    created_item = BaselineRepository.get_scope_item(db, item_id)
    return {"success": True, "message": "Scope item added successfully", "data": created_item}

@router.delete("/items/{item_id}")
def delete_scope_item(
    project_id: int,
    item_id: int,
    current_user: dict = Depends(require_roles(["ADMIN", "ENGAGEMENT_MANAGER"])),
    db: mysql.connector.connection.MySQLConnection = Depends(get_db)
):
    verify_project_access(project_id, current_user, db)
    
    item = BaselineRepository.check_scope_item_exists_in_project(db, item_id, project_id)
    if not item:
        raise HTTPException(status_code=404, detail="Scope item not found")
        
    BaselineRepository.delete_scope_item(db, item_id, project_id)
    db.commit()
    
    return {"success": True, "message": "Scope item deleted successfully"}

class ScopeItemCompletionUpdate(BaseModel):
    completion_status: str

@router.patch("/items/{item_id}/completion")
def update_scope_item_completion(
    project_id: int,
    item_id: int,
    data: ScopeItemCompletionUpdate,
    current_user: dict = Depends(require_roles(["ADMIN", "ENGAGEMENT_MANAGER", "PROJECT_LEAD"])),
    db: mysql.connector.connection.MySQLConnection = Depends(get_db)
):
    verify_project_access(project_id, current_user, db)
    
    item = BaselineRepository.check_scope_item_exists_in_project(db, item_id, project_id)
    if not item:
        raise HTTPException(status_code=404, detail="Scope item not found")
        
    if data.completion_status not in ["ACTIVE", "COMPLETED", "CANCELLED"]:
        raise HTTPException(status_code=400, detail="Invalid completion status")
        
    BaselineRepository.update_scope_item_completion(db, item_id, project_id, data.completion_status)
    db.commit()
    
    return {"success": True, "message": f"Scope item marked as {data.completion_status}"}
