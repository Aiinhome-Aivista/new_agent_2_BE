# pyrefly: ignore [missing-import]
from fastapi import APIRouter, Depends, HTTPException, status, Body
from pydantic import BaseModel
from typing import List, Optional
import os
import difflib
import re
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
from services.milestone_dependency_extractor import MilestoneDependencyExtractor
from services.milestone_dependency_service import MilestoneDependencyService
from services.recurring_deliverable_service import RecurringDeliverableService
from services.rag_service import RAGService

# pyrefly: ignore [missing-import]
import mysql.connector
from repositories.document_repository import DocumentRepository
import json
# pyrefly: ignore [missing-import]
from fastapi import Query
import threading
import uuid
import tempfile
from services.s3_service import S3Service

router = APIRouter()

# Supported extraction modes for the baseline pipeline.
QUICK_EXTRACT = "QUICK"
DEEP_SCAN = "DEEP_SCAN"


def _supersede_previous_vector_versions(db_conn, project_id: int, current_document_id: int):
    """
    Automatic VectorDB version control.

    When a new EL/IFA document is processed for extraction, purge the Vector DB
    chunks (Chroma + BM25) of any previous, superseded EL/IFA documents for this
    project, so RAG does not return conflicting answers from stale versions.
    The old documents remain in the relational DB for history — only their
    vector chunks are removed.
    """
    try:
        prior_docs = BaselineRepository.get_other_elifa_documents(
            db_conn, project_id, current_document_id
        )
        for prior in prior_docs:
            try:
                RAGService.delete_document(project_id, prior["id"])
                print(
                    f"[VectorVersioning] Purged vector chunks for superseded "
                    f"{prior.get('document_type')} document id={prior['id']} "
                    f"('{prior.get('document_name')}')"
                )
            except Exception as del_err:
                print(
                    f"[VectorVersioning] Failed to purge vector chunks for "
                    f"document id={prior['id']}: {del_err}"
                )
    except Exception as e:
        # Non-fatal: extraction should still proceed even if cleanup fails.
        print(f"[VectorVersioning] Non-fatal error during vector version cleanup: {e}")


def run_baseline_pipeline(project_id: int, document_id: int, mode: str = QUICK_EXTRACT):
    # Establish a fresh connection for the thread
    thread_conn = get_db_connection()
    if not thread_conn:
        print("!!! Background baseline pipeline failed to connect !!!")
        return
        
    def emit(step: str, progress: int):
        print(f"DEBUG: Starting emit for {step}")
        try:
            upd_cursor = thread_conn.cursor()
            upd_cursor.execute(
                "UPDATE documents SET processing_progress = %s, processing_step = %s WHERE id = %s",
                (progress, step, document_id)
            )
            thread_conn.commit()
            upd_cursor.close()
            print(f"DEBUG: Successfully emitted {step}")
        except Exception as ex:
            print(f"Failed to update baseline progress in DB for {step}: {ex}")

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

        # Automatic VectorDB version control: remove vector chunks of any
        # previous, superseded EL/IFA documents for this project. This keeps RAG
        # answers consistent for BOTH modes, and additionally ensures the Deep
        # Scan heatmap only sees the current document's chunks.
        _supersede_previous_vector_versions(thread_conn, project_id, document_id)

        ext = os.path.splitext(doc["storage_key"])[1].lower()
        temp_path = os.path.join(tempfile.gettempdir(), f"temp_{uuid.uuid4()}{ext}")
        
        try:
            S3Service.download_to_temp_file(doc["storage_key"], temp_path)
            chunks = DocumentService.parse_document(temp_path, ext)
        finally:
            if os.path.exists(temp_path):
                os.remove(temp_path)
        
        # Pipeline Step 1: Detect Sections
        emit("Detecting Scope Sections", 15)
        chunks_with_sections = ScopeSectionDetector.detect_sections(chunks)
        
        # Pipeline Step 2: Extract Candidates
        # Branch on the selected extraction mode. Both branches MUST return
        # candidates in the same dictionary shape so the rest of the pipeline
        # (classification -> dedup -> milestones -> saving) is mode-agnostic.
        emit("Extracting Scope Candidates", 30)
        if mode == DEEP_SCAN:
            print("[Baseline] Using DEEP_SCAN (Map-Reduce + Heatmap) extraction mode.")
            from services.deep_scan_extractor import DeepScanExtractor
            raw_candidates = DeepScanExtractor.extract_candidates(
                project_id=project_id,
                document_id=document_id,
                document_type=doc.get("document_type", "EL"),
                parsed_chunks=chunks,
                sectioned_chunks=chunks_with_sections,
            )
        else:
            print("[Baseline] Using QUICK extraction mode.")
            raw_candidates = ScopeCandidateExtractor.extract_candidates(chunks_with_sections, document_id)
        
        # Pipeline Step 3: Classification
        emit("Classifying Scope Items", 50)
        classified_candidates = ScopeClassifier.classify_candidates_batch(project_id, raw_candidates)
            
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
        latest_approved = BaselineRepository.get_latest_approved_baseline(thread_conn, project_id)

        if existing_draft:
            baseline_id = existing_draft["id"]
            BaselineRepository.update_baseline_source_document(thread_conn, baseline_id, document_id)
            BaselineRepository.delete_scope_items_by_baseline(thread_conn, baseline_id)
            BaselineRepository.delete_deliverables_by_baseline(thread_conn, baseline_id)
            BaselineRepository.delete_stakeholders_by_project(thread_conn, project_id)
            if latest_approved:
                app_baseline_id = latest_approved["id"]
                BaselineRepository.copy_scope_items(thread_conn, app_baseline_id, baseline_id)
                BaselineRepository.copy_deliverables(thread_conn, app_baseline_id, baseline_id)
        else:
            max_v = BaselineRepository.get_max_baseline_version(thread_conn, project_id)
            next_version = max_v + 1
            baseline_id = BaselineRepository.create_baseline(thread_conn, project_id, next_version, document_id)
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
            
            item_category = item.get("category")
            if item.get("is_pure_milestone", False):
                item_type = "IN_SCOPE"
                item_category = "MILESTONE"
            
            if not item_category:
                item_category = "FUNCTIONAL"

            raw_status = item.get("milestone_status", "").upper()
            completion_status = "ACTIVE"
            if "COMPLETED" in raw_status or "DONE" in raw_status:
                completion_status = "COMPLETED"
            elif "CANCEL" in raw_status:
                completion_status = "CANCELLED"

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
                    deadline_normalized=item.get("deadline_normalized"),
                    category=item_category,
                    completion_status=completion_status
                )
                item_id = existing_item["id"]
                item["_db_id"] = item_id
            else:
                item_id = BaselineRepository.insert_scope_item_extracted(
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
                    deadline_normalized=item.get("deadline_normalized"),
                    category=item_category,
                    completion_status=completion_status
                )
                item["_db_id"] = item_id
        
        # Commit DB changes to release any locks on `documents` table 
        # so `emit` (which gets a new connection) does not deadlock.
        thread_conn.commit()
        
        # Process Milestones & Dependencies
        emit("Building Milestone Dependencies", 97)
        # Clear existing draft milestones/mappings/dependencies (if any)
        # We will clear project_milestones for the draft baseline. But actually, project_milestones are tied to project_id.
        # Let's delete project_milestones for this project_id and baseline_id.
        cursor = thread_conn.cursor()
        cursor.execute("DELETE FROM project_milestones WHERE project_id = %s AND baseline_id = %s", (project_id, baseline_id))
        
        # 1. Extract distinct milestones
        milestone_dict = {} # normalized_name -> dict
        for item in extracted_data.get("scope_items", []):
            m_norm = item.get("milestone_normalized")
            m_orig = item.get("milestone")
            if m_norm:
                if m_norm not in milestone_dict:
                    milestone_dict[m_norm] = {
                        "name": m_orig,
                        "planned_date": item.get("deadline_normalized"),
                        "status": item.get("milestone_status", "Planned"),
                        "scope_item_ids": []
                    }
                if item.get("_db_id"):
                    milestone_dict[m_norm]["scope_item_ids"].append(item["_db_id"])
                    
        # 2. Sort milestones chronologically and insert into project_milestones
        def get_date_val(item):
            d = item[1].get("planned_date")
            return d if d else "9999-12-31"
            
        sorted_milestones = sorted(milestone_dict.items(), key=get_date_val)
        
        seq = 1
        name_to_id = {}
        for m_norm, m_data in sorted_milestones:
            m_id = BaselineRepository.create_project_milestone(
                thread_conn, project_id, baseline_id, m_data["name"], seq, m_data["status"], m_data["planned_date"]
            )
            name_to_id[m_norm] = m_id
            seq += 1
            for scope_id in m_data["scope_item_ids"]:
                BaselineRepository.create_scope_milestone_mapping(thread_conn, scope_id, m_id)
                
        # 3. Extract dependencies
        extracted_deps = MilestoneDependencyExtractor.extract_dependencies(extracted_data.get("scope_items", []), doc["document_text"] if "document_text" in doc else "")
        
        edges = []
        for dep in extracted_deps:
            parent_name = NormalizationService.normalize_milestone(dep["parent_milestone"], None)
            child_name = NormalizationService.normalize_milestone(dep["child_milestone"], None)
            if parent_name in name_to_id and child_name in name_to_id:
                edges.append((name_to_id[parent_name], name_to_id[child_name]))
                
        # 4. Validate DAG
        dag_valid = True
        try:
            if edges:
                MilestoneDependencyService.validate_dag(edges)
        except ValueError:
            dag_valid = False
            
        if dag_valid and edges:
            for p, c in edges:
                cursor.execute(
                    "INSERT INTO milestone_dependencies (project_id, parent_milestone_id, child_milestone_id, dependency_type) VALUES (%s, %s, %s, 'FINISH_TO_START')",
                    (project_id, p, c)
                )
        else:
            # FALLBACK: If LLM failed to extract a valid DAG, use sequential dependencies
            MilestoneDependencyService.generate_sequential_dependencies(cursor, project_id)
        cursor.close()
        
        # ── Step 6.5: Detect and expand recurring commitments ──
        emit("Detecting Recurring Commitments", 97)
        try:
            # Fetch the project record to get start/end dates
            proj_cursor = thread_conn.cursor(dictionary=True)
            proj_cursor.execute("SELECT id, start_date, end_date FROM projects WHERE id = %s", (project_id,))
            project_record = proj_cursor.fetchone() or {}
            proj_cursor.close()

            RecurringDeliverableService.process_recurring_commitments(
                db=thread_conn,
                baseline_id=baseline_id,
                project_id=project_id,
                scope_items=extracted_data.get("scope_items", []),
                project=project_record,
            )
        except Exception as rec_err:
            import traceback
            print(f"[Recurring] Non-fatal error during recurring commitment processing: {rec_err}")
            traceback.print_exc()

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

class ExtractRequest(BaseModel):
    # "QUICK" (default, existing deterministic hybrid) or "DEEP_SCAN" (Map-Reduce).
    mode: Optional[str] = QUICK_EXTRACT


@router.post("/extract")
def extract_baseline(
    project_id: int,
    document_id: int,
    payload: Optional[ExtractRequest] = Body(default=None),
    current_user: dict = Depends(require_roles(["ADMIN", "ENGAGEMENT_MANAGER"])),
    db: mysql.connector.connection.MySQLConnection = Depends(get_db)
):
    verify_project_access(project_id, current_user, db)

    # Resolve and validate the extraction mode (backward compatible: no body -> QUICK).
    mode = (payload.mode if payload and payload.mode else QUICK_EXTRACT).upper()
    if mode not in (QUICK_EXTRACT, DEEP_SCAN):
        mode = QUICK_EXTRACT
    
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
        
    # Set status to PROCESSING synchronously to prevent race condition with frontend polling
    cursor = db.cursor()
    cursor.execute(
        "UPDATE documents SET processing_status = 'PROCESSING', processing_progress = 5, processing_step = 'Starting extraction...', processing_started_at = NOW() WHERE id = %s",
        (document_id,)
    )
    db.commit()
    cursor.close()

    thread = threading.Thread(
        target=run_baseline_pipeline,
        args=(project_id, document_id, mode),
        daemon=True
    )
    thread.start()

    mode_label = "Deep Scan" if mode == DEEP_SCAN else "Quick Extract"
    return {"success": True, "message": f"Baseline extraction started ({mode_label})", "data": {"baseline_id": None, "mode": mode}}

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

def _extract_compound_abbrevs(word: str) -> set:
    """Generically extract common compound abbreviations (e.g. 'database' -> 'db')."""
    w = word.lower()
    res = {w}
    compound_splits = re.findall(r'[a-z]+', re.sub(r'([A-Z])', r' \1', word).lower())
    if len(compound_splits) >= 2:
        res.add("".join(s[0] for s in compound_splits))
    if "data" in w and "base" in w:
        res.add("db")
    return res

def _extract_acronyms(text: str) -> set:
    """Generically extract acronyms from multi-word phrases and hyphenated terms."""
    stop_words = {"for", "and", "the", "with", "to", "of", "in", "a", "an", "on", "is", "by", "at", "as", "from"}
    clean_text = re.sub(r'[\(\)\[\]\{\}\-_,\.:;\t\r\n]+', ' ', text)
    words = [w for w in re.findall(r'\b[a-zA-Z0-9]+\b', clean_text.lower()) if w not in stop_words]
    acrs = set()
    if len(words) >= 2:
        acrs.add("".join(w[0] for w in words))
    for l in range(2, min(len(words) + 1, 6)):
        for i in range(len(words) - l + 1):
            sub = words[i:i+l]
            acrs.add("".join(w[0] for w in sub))
    for w in words:
        acrs.update(_extract_compound_abbrevs(w))
    return acrs

def _stem_token(token: str) -> str:
    """Generic light stemming for English verb/noun inflections."""
    t = token.lower()
    for suffix in ("ation", "tion", "ment", "ing", "ies", "ed", "es", "s"):
        if t.endswith(suffix) and len(t) - len(suffix) >= 3:
            return t[:-len(suffix)]
    return t

def _tokenize_stemmed_words(text: str) -> set:
    stop_words = {"for", "and", "the", "with", "to", "of", "in", "a", "an", "on", "is", "by", "at", "as", "from", "etc"}
    clean_text = re.sub(r'[\(\)\[\]\{\}\-_,\.:;\t\r\n]+', ' ', text)
    raw_tokens = re.findall(r'\b[a-zA-Z0-9]+\b', clean_text.lower())
    return {_stem_token(w) for w in raw_tokens if w not in stop_words and len(w) > 1}

def _is_token_match(t1: str, t2: str) -> bool:
    if t1 == t2:
        return True
    if len(t1) >= 4 and len(t2) >= 4 and (t1.startswith(t2) or t2.startswith(t1)):
        return True
    if len(t1) >= 3 and len(t2) >= 3 and difflib.SequenceMatcher(None, t1, t2).ratio() >= 0.82:
        return True
    return False

def _matches_any(target: str, pool: set) -> bool:
    for item in pool:
        if _is_token_match(target, item):
            return True
    return False

def _is_title_match(a: str, b: str) -> bool:
    """
    100% Generic, document-agnostic matching algorithm.
    Works for ANY project, ANY baseline, and ANY industry domain.
    """
    if not a or not b:
        return False
        
    a_clean = re.sub(r'[\(\)\[\]\{\}\-_,\.:;\t\r\n]+', ' ', a).lower().strip()
    b_clean = re.sub(r'[\(\)\[\]\{\}\-_,\.:;\t\r\n]+', ' ', b).lower().strip()
    
    if a_clean == b_clean:
        return True

    # Check for period/month or year conflict (e.g. May 2026 vs Aug 2026 vs generic CSI)
    MONTH_TOKENS = {
        'january', 'february', 'march', 'april', 'may', 'june',
        'july', 'august', 'september', 'october', 'november', 'december',
        'jan', 'feb', 'mar', 'apr', 'jun', 'jul', 'aug', 'sep', 'oct', 'nov', 'dec'
    }
    months_a = {w for w in re.findall(r'\b[a-zA-Z]+\b', a.lower()) if w in MONTH_TOKENS}
    months_b = {w for w in re.findall(r'\b[a-zA-Z]+\b', b.lower()) if w in MONTH_TOKENS}
    
    # If both specify different months -> Conflict!
    if months_a and months_b and not (months_a & months_b):
        return False

    # If one specifies a month and the other does not -> Conflict!
    # A generic activity title cannot match a specific monthly occurrence.
    if bool(months_a) != bool(months_b):
        return False

    years_a = {w for w in re.findall(r'\b20\d{2}\b', a)}
    years_b = {w for w in re.findall(r'\b20\d{2}\b', b)}
    if years_a and years_b and not (years_a & years_b):
        return False
    if bool(years_a) != bool(years_b) and (months_a or months_b):
        return False

    # 1. Parenthetical aliases
    def get_parentheses_aliases(raw: str):
        aliases = [raw.lower().strip()]
        for p in re.findall(r'\((.*?)\)', raw):
            if p.strip():
                aliases.append(p.strip().lower())
        no_parens = re.sub(r'\(.*?\)', '', raw).strip().lower()
        if no_parens:
            aliases.append(no_parens)
        return list(set(aliases))

    a_parens = get_parentheses_aliases(a)
    b_parens = get_parentheses_aliases(b)
    for ap in a_parens:
        for bp in b_parens:
            if ap == bp:
                return True

    # 2. Compound multi-phase protection
    a_parts = [p.strip() for p in re.split(r'[,;/]|\band\b', a) if len(p.strip()) > 2]
    b_parts = [p.strip() for p in re.split(r'[,;/]|\band\b', b) if len(p.strip()) > 2]

    words_a = _tokenize_stemmed_words(a)
    words_b = _tokenize_stemmed_words(b)
    acrs_a = _extract_acronyms(a)
    acrs_b = _extract_acronyms(b)

    pool_a = words_a | acrs_a
    pool_b = words_b | acrs_b

    if len(a_parts) > 1 and len(b_parts) == 1:
        first_pool = _tokenize_stemmed_words(a_parts[0]) | _extract_acronyms(a_parts[0])
        matched_in_first = sum(1 for wb in words_b if _matches_any(wb, first_pool))
        if len(words_b) > 0 and (matched_in_first / len(words_b)) >= 0.70:
            return True
        return False
    elif len(b_parts) > 1 and len(a_parts) == 1:
        first_pool = _tokenize_stemmed_words(b_parts[0]) | _extract_acronyms(b_parts[0])
        matched_in_first = sum(1 for wa in words_a if _matches_any(wa, first_pool))
        if len(words_a) > 0 and (matched_in_first / len(words_a)) >= 0.70:
            return True
        return False

    # 3. Dynamic acronym & word match
    matched_a_in_b = sum(1 for wa in words_a if _matches_any(wa, pool_b))
    matched_b_in_a = sum(1 for wb in words_b if _matches_any(wb, pool_a))

    len_a = len(words_a)
    len_b = len(words_b)
    
    if len_a > 0 and len_b > 0:
        containment_a = matched_a_in_b / len_a
        containment_b = matched_b_in_a / len_b
        
        if min(len_a, len_b) <= 3 and max(containment_a, containment_b) >= 0.65 and max(matched_a_in_b, matched_b_in_a) >= 2:
            return True
        if max(containment_a, containment_b) >= 0.75 and max(matched_a_in_b, matched_b_in_a) >= 2:
            return True
        if (containment_a >= 0.50 and containment_b >= 0.50) and (matched_a_in_b >= 2 or matched_b_in_a >= 2):
            return True

    # 4. Levenshtein / Sequence Matcher
    ratio = difflib.SequenceMatcher(None, a_clean, b_clean).ratio()
    if ratio >= 0.85:
        return True

    return False

MONTH_MAP = {
    'january': 1, 'february': 2, 'march': 3, 'april': 4,
    'may': 5, 'june': 6, 'july': 7, 'august': 8,
    'september': 9, 'october': 10, 'november': 11, 'december': 12,
    'jan': 1, 'feb': 2, 'mar': 3, 'apr': 4, 'jun': 6,
    'jul': 7, 'aug': 8, 'sep': 9, 'oct': 10, 'nov': 11, 'dec': 12,
}

def _extract_months_from_text(text: str) -> set:
    if not text:
        return set()
    t = text.lower()
    found = set()
    for m_name, m_num in MONTH_MAP.items():
        if re.search(r'\b' + re.escape(m_name) + r'\b', t):
            found.add(m_num)
    return found

def _is_scope_item_match_for_resolution(resolved_item: dict, si: dict) -> bool:
    res_name = resolved_item.get("name", "") if isinstance(resolved_item, dict) else str(resolved_item)
    res_ev = resolved_item.get("resolution_evidence", "") if isinstance(resolved_item, dict) else ""
    si_name = si.get("name", "") if isinstance(si, dict) else (si[1] if len(si) > 1 else "")
    si_rec = bool(si.get("is_recurring") if isinstance(si, dict) else (si[3] if len(si) > 3 else False))
    si_pid = si.get("parent_scope_item_id") if isinstance(si, dict) else (si[4] if len(si) > 4 else None)
    si_dl = si.get("deadline") if isinstance(si, dict) else (si[5] if len(si) > 5 else None)

    # Child occurrence of a recurring commitment
    if si_rec and si_pid is not None:
        si_base = re.sub(r'\s*[—\-–]\s*[A-Za-z]{3,9}\s*\d{4}.*$', '', si_name).strip()
        if not (_is_title_match(res_name, si_name) or _is_title_match(res_name, si_base)):
            return False
        occ_months = set()
        if si_dl:
            try:
                from datetime import datetime, date as date_type
                d = si_dl if isinstance(si_dl, (date_type, datetime)) else datetime.strptime(str(si_dl)[:10], '%Y-%m-%d').date()
                occ_months.add(d.month)
            except Exception:
                pass
        occ_months |= _extract_months_from_text(si_name)
        res_months = _extract_months_from_text(res_name) | _extract_months_from_text(res_ev)
        if res_months:
            return bool(occ_months & res_months)
        return False

    # Parent of a recurring commitment (ongoing process)
    if si_rec and si_pid is None:
        return False

    # Standard non-recurring deliverable
    return _is_title_match(res_name, si_name)

def _rebuild_graph_and_recalculate(cursor, project_id: int, completed_title: Optional[str] = None) -> None:
    """
    GRAPH_RECALC PMO ENGINE:
    Rebuild the runtime dependency graph from OPEN tracker_items' reasoning JSON
    and re-evaluate graph_role and execution_priority_score using RiskScoringEngine.
    Executes full Step 2A through Step 2G pipeline with complete terminal visibility.
    """
    try:
        from services.risk_scoring_engine import RiskScoringEngine, _parse_due_date
    except Exception as e:
        print(f"[GRAPH RECALC WARNING] Could not import RiskScoringEngine: {e}")
        return

    print("\n" + "="*70)
    print(f"🚀 [GRAPH RECALC PIPELINE] Starting PMO Evaluation for Project {project_id}")
    if completed_title:
        print(f"   Triggered by Deliverable Completion: '{completed_title}'")
    print("="*70)

    # ──────────────────────────────────────────────────────────────────────────
    # 🟢 STEP 2A: Fact Extraction & Completed Scope Verification
    # ──────────────────────────────────────────────────────────────────────────
    cursor.execute("""
        SELECT name, scope_item_normalized FROM scope_items 
        WHERE project_id = %s AND (completion_status = 'ACTIVE' OR completion_status IS NULL)
    """, (project_id,))
    active_scope_rows = cursor.fetchall() or []
    active_scope_names = {r["name"] for r in active_scope_rows if r.get("name")} | {r["scope_item_normalized"] for r in active_scope_rows if r.get("scope_item_normalized")}

    cursor.execute("""
        SELECT name, scope_item_normalized FROM scope_items 
        WHERE project_id = %s AND completion_status = 'COMPLETED'
    """, (project_id,))
    completed_scope_rows = cursor.fetchall() or []
    completed_scope_names = {r["name"] for r in completed_scope_rows if r.get("name")} | {r["scope_item_normalized"] for r in completed_scope_rows if r.get("scope_item_normalized")}
    if completed_title:
        completed_scope_names.add(completed_title)

    print("\n" + "-"*70)
    print("🟢 STEP 2A: Fact Extraction & Completed Deliverables Verification")
    print(f"   Completed Scope Items ({len(completed_scope_names)}): {list(completed_scope_names)}")

    # 1. Ensure any open tracker item whose scope deliverable is COMPLETED is marked RESOLVED
    cursor.execute("""
        SELECT id, title FROM tracker_items 
        WHERE project_id = %s AND status = 'OPEN'
    """, (project_id,))
    open_items_check = cursor.fetchall() or []
    for it in open_items_check:
        title = it["title"]
        if any(_is_title_match(title, c_name) for c_name in completed_scope_names):
            cursor.execute("""
                UPDATE tracker_items 
                SET status = 'RESOLVED', execution_status = 'RESOLVED', risk_status = 'RESOLVED',
                    execution_priority_score = 0, risk_score = 0,
                    resolution = 'Deliverable completed.', resolved_at = NOW()
                WHERE id = %s
            """, (it["id"],))
            print(f"   ✓ Auto-resolved tracker item #{it['id']} '{title}' (matching completed deliverable)")

    # 2. Ensure any tracker item previously auto-resolved by deliverable completion whose deliverable was REOPENED to ACTIVE is restored to OPEN
    cursor.execute("""
        SELECT id, title, resolution, resolved_by FROM tracker_items 
        WHERE project_id = %s AND status = 'RESOLVED'
    """, (project_id,))
    resolved_items_check = cursor.fetchall() or []
    for it in resolved_items_check:
        title = it["title"]
        is_auto_resolved = (it.get("resolution") == "Deliverable completed.")
        if is_auto_resolved:
            matches_active = any(_is_title_match(title, a_name) for a_name in active_scope_names)
            matches_completed = any(_is_title_match(title, c_name) for c_name in completed_scope_names)
            if matches_active and not matches_completed:
                cursor.execute("""
                    UPDATE tracker_items 
                    SET status = 'OPEN', execution_status = 'IN_PROGRESS', risk_status = 'OPEN',
                        resolution = NULL, resolved_by = NULL, resolved_at = NULL
                    WHERE id = %s
                """, (it["id"],))
                print(f"   ✓ Restored active tracker item #{it['id']} '{title}' (matching active deliverable)")

    # ──────────────────────────────────────────────────────────────────────────
    # 🔵 STEP 2B: Context & Active Dependency Graph Reconstruction
    # ──────────────────────────────────────────────────────────────────────────
    cursor.execute("""
        SELECT * FROM tracker_items 
        WHERE project_id = %s AND status = 'OPEN'
    """, (project_id,))
    open_items = cursor.fetchall() or []
    
    cursor.execute("""
        SELECT title FROM tracker_items 
        WHERE project_id = %s AND status = 'RESOLVED'
    """, (project_id,))
    resolved_rows = cursor.fetchall() or []
    resolved_titles = {r["title"] for r in resolved_rows if r.get("title")} | completed_scope_names

    print("\n" + "-"*70)
    print("🔵 STEP 2B: Context & Active Dependency Graph Reconstruction")
    print(f"   Remaining Active Items ({len(open_items)})")
    print(f"   Resolved Ancestor Nodes ({len(resolved_titles)})")
        
    if not open_items:
        print("   ℹ️ No active items remaining to recalculate.")
        print("="*70 + "\n")
        return

    # Rebuild raw dependency graph from ALL project tracker items (open + resolved)
    cursor.execute("""
        SELECT title, reasoning FROM tracker_items 
        WHERE project_id = %s
    """, (project_id,))
    all_tracker_items = cursor.fetchall() or []

    raw_graph = {}
    for item in all_tracker_items:
        title = item["title"]
        try:
            r = json.loads(item.get("reasoning") or "{}") if isinstance(item.get("reasoning"), str) else (item.get("reasoning") or {})
        except Exception:
            r = {}
            
        blocks_list = r.get("blocks", [])
        if isinstance(blocks_list, list):
            for b in blocks_list:
                if isinstance(b, str) and b.strip():
                    raw_graph.setdefault(title, []).append(b.strip())
                    
        chain = r.get("execution_chain", [])
        if isinstance(chain, list) and len(chain) > 1:
            for idx in range(len(chain) - 1):
                src = chain[idx]
                dst = chain[idx + 1]
                if isinstance(src, str) and isinstance(dst, str):
                    if dst not in raw_graph.get(src, []):
                        raw_graph.setdefault(src, []).append(dst)

    # Filter out completed items from the active DAG
    graph = {}
    for src, targets in raw_graph.items():
        if any(_is_title_match(src, res) for res in resolved_titles):
            continue
        filtered_targets = []
        for t in targets:
            if not any(_is_title_match(t, res) for res in resolved_titles):
                filtered_targets.append(t)
        if filtered_targets:
            graph[src] = filtered_targets

    # STEP 2B SUPPLEMENT: Include static milestone dependency edges for tracker items
    # that have an m_id set or match a project milestone, whose downstream milestone has no tracker item.
    # This prevents cascade count loss when downstream milestones are not tracked separately.
    # Generic: works for any tracker item matching project_milestones.
    try:
        cursor.execute("SELECT id, name, status FROM project_milestones WHERE project_id = %s", (project_id,))
        all_pm = cursor.fetchall() or []
        open_tracker_mids = {}
        for item in open_items:
            ref_id = item.get('reference_id')
            title = item.get('title') or ''
            found_mid = None
            if ref_id and any((pm['id'] == ref_id if isinstance(pm, dict) else pm[0] == ref_id) for pm in all_pm):
                found_mid = ref_id
            else:
                for pm in all_pm:
                    pm_name = pm['name'] if isinstance(pm, dict) else pm[1]
                    if _is_title_match(title, pm_name):
                        found_mid = pm['id'] if isinstance(pm, dict) else pm[0]
                        break
            if found_mid and title:
                open_tracker_mids[found_mid] = title

        if open_tracker_mids:
            mid_list = list(open_tracker_mids.keys())
            format_str = ','.join(['%s'] * len(mid_list))
            cursor.execute(f"""
                SELECT md.parent_milestone_id, md.child_milestone_id, pm.name as downstream_name
                FROM milestone_dependencies md
                JOIN project_milestones pm ON md.child_milestone_id = pm.id
                WHERE md.parent_milestone_id IN ({format_str})
                AND pm.project_id = %s
                AND pm.status NOT IN ('Completed', 'COMPLETED')
            """, (*mid_list, project_id))

            milestone_deps = cursor.fetchall() or []
            for dep in milestone_deps:
                upstream_mid = dep['parent_milestone_id'] if isinstance(dep, dict) else dep[0]
                downstream_name = dep['downstream_name'] if isinstance(dep, dict) else dep[2]

                if upstream_mid in open_tracker_mids:
                    upstream_title = open_tracker_mids[upstream_mid]
                    # Add edge if downstream is not already resolved/completed
                    downstream_is_resolved = any(_is_title_match(downstream_name, res) for res in resolved_titles)
                    downstream_is_tracker = any(
                        _is_title_match(downstream_name, t.get('title', ''))
                        for t in open_items
                    )
                    if not downstream_is_resolved and not downstream_is_tracker and downstream_name:
                        # Add virtual downstream node to the runtime graph
                        if upstream_title not in graph:
                            graph[upstream_title] = []
                        if not any(_is_title_match(downstream_name, existing_t) for existing_t in graph[upstream_title]):
                            graph[upstream_title].append(downstream_name)
                            print(f"  [MilestoneEdgeSupp] Added milestone edge: "
                                  f"'{upstream_title}' → '{downstream_name}' (m_id={upstream_mid})")
    except Exception as e:
        print(f"  [MilestoneEdgeSupp] Warning: {e}")

    print(f"   Active Runtime Graph Edges: {graph if graph else 'None (All isolated/independent)'}")

    def _bfs_cascade(start_node: str) -> int:
        visited = set()
        queue = [start_node]
        while queue:
            curr = queue.pop(0)
            for child in graph.get(curr, []):
                child_canonical = None
                for k in graph.keys():
                    if _is_title_match(k, child):
                        child_canonical = k
                        break
                target_key = child_canonical or child
                if target_key not in visited:
                    visited.add(target_key)
                    queue.append(target_key)
        return len(visited)

    # ──────────────────────────────────────────────────────────────────────────
    # 🟡 STEP 2C & 🟣 STEP 2D: Graph Roles & RiskScoringEngine Evaluation
    # ──────────────────────────────────────────────────────────────────────────
    print("\n" + "-"*70)
    print("🟡 STEP 2C: Topological Cascade & Graph Role Classification")
    print("🟣 STEP 2D: RiskScoringEngine 7-Band Mathematical Evaluation")
    
    recalculated = []
    for item in open_items:
        title = item["title"]
        is_scope_creep = bool(item.get("is_out_of_scope", 0)) or item.get("graph_role") == "SCOPE_CREEP"
        
        if is_scope_creep:
            new_graph_role = "SCOPE_CREEP"
            cascade = 0
        else:
            has_incoming = False
            for src, targets in graph.items():
                if not _is_title_match(src, title):
                    for t in targets:
                        if _is_title_match(t, title):
                            has_incoming = True
                            break
                    if has_incoming:
                        break
                        
            has_outgoing = False
            for src in graph.keys():
                if _is_title_match(src, title):
                    if len(graph[src]) > 0:
                        has_outgoing = True
                        break

            # Check if this item had upstream predecessors in the full project architecture
            had_upstream = False
            for src, targets in raw_graph.items():
                if not _is_title_match(src, title):
                    for t in targets:
                        if _is_title_match(t, title):
                            had_upstream = True
                            break
                    if had_upstream:
                        break

            all_upstreams_done = had_upstream and not has_incoming

            if not has_incoming and has_outgoing:
                new_graph_role = "ROOT_CAUSE"
                cascade = _bfs_cascade(title)
            elif has_incoming and has_outgoing:
                new_graph_role = "INTERMEDIATE_BLOCKER"
                cascade = _bfs_cascade(title)
            elif has_incoming and not has_outgoing:
                new_graph_role = "TERMINAL_ACTIVITY"
                cascade = 0
            elif all_upstreams_done:
                # All upstream predecessors completed -> Unblocked and ready for execution
                new_graph_role = "ROOT_CAUSE"
                cascade = 0
            else:
                new_graph_role = "ISOLATED"
                cascade = 0

        risk_severity = item.get("risk_severity_score") or item.get("risk_score") or 50
        days_until_due = item.get("days_until_due") or 9999
        owner = item.get("owner") or "Internal"
        
        if days_until_due == 9999:
            try:
                r_obj = json.loads(item.get("reasoning") or "{}") if isinstance(item.get("reasoning"), str) else (item.get("reasoning") or {})
                due_date_str = r_obj.get("due_date")
                if due_date_str:
                    d_parsed = _parse_due_date(due_date_str)
                    if d_parsed is not None:
                        days_until_due = d_parsed
            except Exception:
                pass

        try:
            score_res = RiskScoringEngine.calculate(
                status=item.get("status", "OPEN"),
                blocked_by=[],
                graph_role=new_graph_role,
                cascade_count=cascade,
                is_scope_creep=is_scope_creep,
                days_until_due=days_until_due,
                dependency_owner=owner,
                execution_unlock_count=cascade,
                criticality_score=float(risk_severity),
            )
            new_exec_score = score_res["execution_priority"]
        except Exception as err:
            print(f"[GRAPH RECALC WARNING] Scoring error on {title}: {err}")
            new_exec_score = item.get("execution_priority_score") or 20

        # Step 6: Determine new execution_status & recommended action
        old_exec_status = item.get("execution_status") or "OPEN"
        if new_graph_role == "ROOT_CAUSE":
            if old_exec_status in ["BLOCKED", "IN_PROGRESS", "OPEN"]:
                new_exec_status = "IN_PROGRESS" if owner != "Customer" else "WAITING_ON_CUSTOMER"
                new_rec_action = f"Prerequisites satisfied ({completed_title or 'Upstream deliverable'} completed). Ready for implementation."
            else:
                new_exec_status = "WAITING_ON_CUSTOMER" if owner == "Customer" else "NOT_STARTED"
                new_rec_action = item.get("recommended_action") or "Prioritize execution."
        elif new_graph_role == "INTERMEDIATE_BLOCKER":
            new_exec_status = "BLOCKED"
            new_rec_action = item.get("recommended_action") or "Awaiting completion of upstream prerequisite."
        elif new_graph_role == "TERMINAL_ACTIVITY":
            new_exec_status = "BLOCKED"
            new_rec_action = item.get("recommended_action") or "Track execution progress."
        else:
            new_exec_status = item.get("execution_status") or "IN_PROGRESS"
            new_rec_action = item.get("recommended_action") or "Track execution progress."

        # ──────────────────────────────────────────────────────────────────────────
        # 📝 STEP 2F: PM Impact & Executive Summary Synchronization
        # ──────────────────────────────────────────────────────────────────────────
        try:
            r_json = json.loads(item.get("reasoning") or "{}") if isinstance(item.get("reasoning"), str) else (item.get("reasoning") or {})
            if isinstance(r_json, dict):
                if new_graph_role == "ROOT_CAUSE" and old_exec_status in ["BLOCKED", "IN_PROGRESS"]:
                    if "business_impact" in r_json and isinstance(r_json["business_impact"], dict):
                        r_json["business_impact"]["immediate"] = "Prerequisites satisfied. Unblocked and ready for execution."
                    r_json["executive_summary"] = f"Prerequisites completed; {title} is now unblocked and ready for implementation."
                updated_reasoning_str = json.dumps(r_json)
            else:
                updated_reasoning_str = item.get("reasoning")
        except Exception:
            updated_reasoning_str = item.get("reasoning")

        # Step 7: Update tracker_items
        cursor.execute("""
            UPDATE tracker_items
            SET execution_priority_score = %s,
                graph_role = %s,
                execution_status = %s,
                recommended_action = %s,
                reasoning = %s,
                risk_score = %s
            WHERE id = %s
        """, (
            new_exec_score,
            new_graph_role,
            new_exec_status,
            new_rec_action,
            updated_reasoning_str,
            new_exec_score,
            item["id"]
        ))
        
        recalculated.append({
            "id": item["id"],
            "title": title,
            "new_score": new_exec_score,
            "graph_role": new_graph_role,
            "execution_status": new_exec_status,
            "cascade": cascade
        })

        print(f"   • '{title}': Role={new_graph_role:<20} | Cascade={cascade} | Score={new_exec_score:<3} | Status={new_exec_status}")

    # ──────────────────────────────────────────────────────────────────────────
    # ⚖️ STEP 2E: Parent > Child Constraint Enforcement
    # ──────────────────────────────────────────────────────────────────────────
    print("\n" + "-"*70)
    print("⚖️ STEP 2E: Parent > Child Constraint Enforcement")
    constraint_applied = False
    for src, targets in graph.items():
        src_items = [r for r in recalculated if _is_title_match(r["title"], src)]
        if not src_items:
            continue
        src_rec = src_items[0]
        for tgt in targets:
            tgt_items = [r for r in recalculated if _is_title_match(r["title"], tgt)]
            if not tgt_items:
                continue
            tgt_rec = tgt_items[0]
            if src_rec["new_score"] <= tgt_rec["new_score"]:
                forced_score = min(100, tgt_rec["new_score"] + 1)
                cursor.execute("UPDATE tracker_items SET execution_priority_score = %s WHERE id = %s", (forced_score, src_rec["id"]))
                print(f"   ⚠️ CONSTRAINT: Bumped Parent '{src_rec['title']}' ({src_rec['new_score']} → {forced_score}) to exceed Child '{tgt_rec['title']}' ({tgt_rec['new_score']})")
                src_rec["new_score"] = forced_score
                constraint_applied = True
    if not constraint_applied:
        print("   ✓ All hierarchical DAG score constraints satisfied.")

    # ──────────────────────────────────────────────────────────────────────────
    # 📊 STEP 2G: Final Risk Tracker Output & Aggregation
    # ──────────────────────────────────────────────────────────────────────────
    print("\n" + "-"*70)
    print("📊 STEP 2G: Final Risk Tracker State & Recalculation Summary")
    recalculated_sorted = sorted(recalculated, key=lambda x: x["new_score"], reverse=True)
    for rank, it in enumerate(recalculated_sorted, start=1):
        print(f"   #{rank:<2} {it['title']:<50} | Score: {it['new_score']:<3} | Role: {it['graph_role']:<20} | Status: {it['execution_status']}")
    print("="*70 + "\n")

class ScopeItemCompletionUpdate(BaseModel):
    completion_status: Optional[str] = None
    deadline: Optional[str] = None
    completion_notes: Optional[str] = None
    resolve_prerequisite_ids: Optional[List[int]] = None
    resolve_prerequisite_names: Optional[List[str]] = None
    resolve_upstream_scope_item_ids: Optional[List[int]] = None

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
        
    if data.completion_status is not None and data.completion_status not in ["ACTIVE", "COMPLETED", "CANCELLED"]:
        raise HTTPException(status_code=400, detail="Invalid completion status")

    cursor = db.cursor(dictionary=True)
    
    # Fetch old state for the audit log
    cursor.execute("""
        SELECT id, baseline_id, name, scope_item_normalized, milestone, milestone_normalized, completion_status, deadline 
        FROM scope_items 
        WHERE id = %s AND project_id = %s
    """, (item_id, project_id))
    old_item = cursor.fetchone()
    
    if not old_item:
        cursor.close()
        raise HTTPException(status_code=404, detail="Scope item not found")

    item_name = old_item["name"]
    normalized_name = old_item.get("scope_item_normalized") or item_name
    milestone_name = old_item.get("milestone_normalized") or old_item.get("milestone") or item_name
    baseline_id = old_item["baseline_id"]
    resolution_text = (data.completion_notes or "").strip() or f"Marked {data.completion_status} by {current_user.get('email', 'User')}"

    print("\n" + "=" * 60)
    print(f"[DELIVERABLE COMPLETION] Status Update Triggered for Project {project_id}")
    print(f"Deliverable ID: {item_id} | Name: '{item_name}'")
    print(f"Target Status: {data.completion_status} | Deadline: {data.deadline}")
    print(f"Notes / Reason: {resolution_text}")
    print("-" * 60)

    # 1. Perform repository update for the main scope item
    BaselineRepository.update_scope_item_details(
        db, 
        item_id, 
        project_id, 
        data.completion_status, 
        data.deadline
    )

    # 2. Update matching project_milestones record
    if data.completion_status:
        target_m_status = "COMPLETED" if data.completion_status == "COMPLETED" else ("CANCELLED" if data.completion_status == "CANCELLED" else "In Progress")
        cursor.execute("""
            UPDATE project_milestones 
            SET status = %s 
            WHERE project_id = %s AND baseline_id = %s 
              AND (LOWER(TRIM(name)) = LOWER(TRIM(%s)) 
                   OR LOWER(TRIM(name)) = LOWER(TRIM(%s)) 
                   OR LOWER(TRIM(name)) = LOWER(TRIM(%s)))
        """, (target_m_status, project_id, baseline_id, item_name, normalized_name, milestone_name))
        print(f"[PM MILESTONES] Updated matching milestone to status: '{target_m_status}'")

    # 2.5. Update deliverable_progress table so latest_progress stays in sync
    if data.completion_status == "COMPLETED":
        cursor.execute("""
            UPDATE deliverable_progress 
            SET status_code = 'COMPLETED', progress_percentage = 100.0,
                execution_summary = %s
            WHERE project_id = %s AND scope_item_id = %s
        """, (resolution_text, project_id, item_id))
        print(f"[DELIVERABLE PROGRESS] Synchronized deliverable_progress to COMPLETED (100%)")
    elif data.completion_status == "ACTIVE":
        cursor.execute("""
            UPDATE deliverable_progress 
            SET status_code = 'IN_PROGRESS', progress_percentage = 70.0
            WHERE project_id = %s AND scope_item_id = %s
        """, (project_id, item_id))
        print(f"[DELIVERABLE PROGRESS] Reverted deliverable_progress to IN_PROGRESS")

    # 3. Update matching tracker_items for this activity if it exists
    cursor.execute("SELECT id, title, status FROM tracker_items WHERE project_id = %s", (project_id,))
    existing_tracker_rows = cursor.fetchall() or []
    
    if data.completion_status == "COMPLETED":
        for tr in existing_tracker_rows:
            if _is_title_match(tr["title"], item_name) or _is_title_match(tr["title"], normalized_name) or _is_title_match(tr["title"], milestone_name):
                cursor.execute("""
                    UPDATE tracker_items 
                    SET status = 'RESOLVED', execution_status = 'RESOLVED', risk_status = 'RESOLVED',
                        execution_priority_score = 0, risk_score = 0,
                        resolution = %s, resolved_by = %s, resolved_at = NOW() 
                    WHERE id = %s
                """, (resolution_text, current_user.get("id"), tr["id"]))
                print(f"[RISK TRACKER] Resolved activity tracker item #{tr['id']} '{tr['title']}' matching '{item_name}'")
    elif data.completion_status == "ACTIVE":
        for tr in existing_tracker_rows:
            if _is_title_match(tr["title"], item_name) or _is_title_match(tr["title"], normalized_name) or _is_title_match(tr["title"], milestone_name):
                cursor.execute("""
                    UPDATE tracker_items 
                    SET status = 'OPEN', execution_status = 'IN_PROGRESS', risk_status = 'OPEN',
                        resolution = NULL, resolved_by = NULL, resolved_at = NULL 
                    WHERE id = %s
                """, (tr["id"],))
                print(f"[RISK TRACKER] Reopened activity tracker item #{tr['id']} '{tr['title']}' matching '{item_name}'")

    # 4. Resolve selected prerequisite blocker IDs and matching blocker titles in tracker_items
    resolved_prereqs_count = 0
    if data.completion_status == "COMPLETED":
        if data.resolve_prerequisite_ids:
            for prereq_id in data.resolve_prerequisite_ids:
                cursor.execute("""
                    UPDATE tracker_items 
                    SET status = 'RESOLVED', 
                        execution_status = 'RESOLVED',
                        risk_status = 'RESOLVED',
                        execution_priority_score = 0,
                        risk_score = 0,
                        resolution = %s, 
                        resolved_by = %s, 
                        resolved_at = NOW() 
                    WHERE id = %s AND project_id = %s
                """, (f"Prerequisite resolved with deliverable completion: {resolution_text}", current_user.get("id"), prereq_id, project_id))
                resolved_prereqs_count += cursor.rowcount
        
        if data.resolve_prerequisite_names:
            for p_name in data.resolve_prerequisite_names:
                cursor.execute("""
                    UPDATE tracker_items 
                    SET status = 'RESOLVED', 
                        execution_status = 'RESOLVED',
                        risk_status = 'RESOLVED',
                        execution_priority_score = 0,
                        risk_score = 0,
                        resolution = %s, 
                        resolved_by = %s, 
                        resolved_at = NOW() 
                    WHERE project_id = %s AND status = 'OPEN'
                      AND (LOWER(TRIM(title)) = LOWER(TRIM(%s)) OR LOWER(TRIM(title)) LIKE LOWER(CONCAT('%%', %s, '%%')))
                """, (f"Prerequisite resolved with deliverable completion: {resolution_text}", current_user.get("id"), project_id, p_name, p_name))
                resolved_prereqs_count += cursor.rowcount
        
        if resolved_prereqs_count > 0:
            print(f"[RISK TRACKER] Resolved {resolved_prereqs_count} user-selected prerequisite blockers")

    # 5. Resolve selected upstream scope items if requested
    resolved_upstreams_count = 0
    if data.resolve_upstream_scope_item_ids and data.completion_status == "COMPLETED":
        for up_item_id in data.resolve_upstream_scope_item_ids:
            cursor.execute("""
                UPDATE scope_items 
                SET completion_status = 'COMPLETED' 
                WHERE id = %s AND project_id = %s
            """, (up_item_id, project_id))
            cursor.execute("""
                SELECT name, milestone_normalized FROM scope_items WHERE id = %s
            """, (up_item_id,))
            up_row = cursor.fetchone()
            if up_row:
                cursor.execute("""
                    UPDATE project_milestones 
                    SET status = 'COMPLETED' 
                    WHERE project_id = %s AND baseline_id = %s 
                      AND (LOWER(TRIM(name)) = LOWER(TRIM(%s)) OR LOWER(TRIM(name)) = LOWER(TRIM(%s)))
                """, (project_id, baseline_id, up_row["name"], up_row.get("milestone_normalized") or up_row["name"]))
            resolved_upstreams_count += 1
        print(f"[UPSTREAM] Resolved {resolved_upstreams_count} upstream deliverables")

    # 6. Dynamic Graph Propagation across milestone_dependencies
    cursor.execute("""
        SELECT md.parent_milestone_id, md.child_milestone_id, 
               pm_p.status as parent_status, pm_c.status as child_status,
               pm_c.id as child_id, pm_c.name as child_name
        FROM milestone_dependencies md
        JOIN project_milestones pm_p ON md.parent_milestone_id = pm_p.id
        JOIN project_milestones pm_c ON md.child_milestone_id = pm_c.id
        WHERE md.project_id = %s
    """, (project_id,))
    dep_rows = cursor.fetchall()
    
    if dep_rows:
        child_parents = {}
        child_current_status = {}
        child_names = {}
        for row in dep_rows:
            cid = row["child_id"]
            if cid not in child_parents:
                child_parents[cid] = []
                child_current_status[cid] = (row["child_status"] or "Planned").upper()
                child_names[cid] = row["child_name"]
            child_parents[cid].append((row["parent_status"] or "Planned").upper())
            
        for cid, parent_statuses in child_parents.items():
            curr_st = child_current_status[cid]
            if curr_st in ["COMPLETED", "CANCELLED"]:
                continue
                
            all_parents_done = all(pst in ["COMPLETED", "CANCELLED"] for pst in parent_statuses)
            if all_parents_done and curr_st == "BLOCKED":
                cursor.execute("UPDATE project_milestones SET status = 'Planned' WHERE id = %s", (cid,))
                print(f"[GRAPH PROPAGATION] Unblocked milestone '{child_names[cid]}' (All parents completed)")
            elif not all_parents_done and curr_st not in ["BLOCKED", "COMPLETED", "CANCELLED"]:
                cursor.execute("UPDATE project_milestones SET status = 'BLOCKED' WHERE id = %s", (cid,))
                print(f"[GRAPH PROPAGATION] Blocked milestone '{child_names[cid]}' (Parent incomplete)")

    # 6.5. Dynamic Risk Tracker Recalculation using Graph Engine & RiskScoringEngine
    # GRAPH_RECALC FIX: Rebuild runtime dependency graph and re-score open items with RiskScoringEngine
    _rebuild_graph_and_recalculate(cursor, project_id, completed_title=item_name if data.completion_status == "COMPLETED" else None)

    # 7. Log audit event
    details = {
        "item_id": item_id,
        "item_name": item_name,
        "old_status": old_item["completion_status"],
        "new_status": data.completion_status or old_item["completion_status"],
        "notes": resolution_text,
        "resolved_prereqs_count": resolved_prereqs_count,
        "resolved_upstreams_count": resolved_upstreams_count,
        "old_deadline": str(old_item["deadline"]) if old_item["deadline"] else None,
        "new_deadline": data.deadline or (str(old_item["deadline"]) if old_item["deadline"] else None)
    }
    DocumentRepository.log_audit(
        db=db,
        project_id=project_id,
        agent_name="Web UI",
        action="UPDATE_SCOPE_ITEM_COMPLETION",
        entity_type="SCOPE_ITEM",
        entity_id=item_id,
        details_json=json.dumps(details)
    )

    db.commit()
    cursor.close()
    print("=" * 60 + "\n")
    
    return {
        "success": True, 
        "message": f"Scope item updated to {data.completion_status or 'new values'} successfully",
        "resolved_prerequisites": resolved_prereqs_count,
        "resolved_upstreams": resolved_upstreams_count
    }

@router.post("/followup/trigger")
def trigger_followup_reminders(
    project_id: int,
    target_date: Optional[str] = Query(None, description="ISO Date format YYYY-MM-DD to check deliverables due on that day"),
    current_user: dict = Depends(require_roles(["ADMIN", "ENGAGEMENT_MANAGER", "PROJECT_LEAD"])),
    db: mysql.connector.connection.MySQLConnection = Depends(get_db)
):
    verify_project_access(project_id, current_user, db)
    
    from services.followup_scheduler import run_followup_checks
    
    res = run_followup_checks(target_date)
    return res

