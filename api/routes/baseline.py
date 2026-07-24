# pyrefly: ignore [missing-import]
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from typing import List, Optional
import os
import difflib
from core.database import get_db
from api.dependencies.auth import get_current_user, require_roles, verify_project_access
from services.document_service import DocumentService
from agents.scope_extraction_agent import ScopeExtractionAgent
from repositories.baseline_repository import BaselineRepository
from services.chroma_service import ChromaService
from services.embedding_service import EmbeddingService
from services.scope_section_detector import ScopeSectionDetector
from services.scope_candidate_extractor import ScopeCandidateExtractor
from services.scope_classifier import ScopeClassifier
from services.scope_deduplicator import ScopeDeduplicator
import mysql.connector

router = APIRouter()

@router.post("/extract")
def extract_baseline(project_id: int, document_id: int, current_user: dict = Depends(require_roles(["ADMIN", "ENGAGEMENT_MANAGER"])), db: mysql.connector.connection.MySQLConnection = Depends(get_db)):
    verify_project_access(project_id, current_user, db)
    
    doc = BaselineRepository.get_document(db, document_id, project_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
        
    if doc["document_type"] not in ["EL", "IFA"]:
        raise HTTPException(status_code=400, detail="Only EL and IFA can be used for baseline extraction")
        
    try:
        ext = os.path.splitext(doc["storage_key"])[1].lower()
        chunks = DocumentService.parse_document(doc["storage_key"], ext)
        
        # Pipeline Step 1: Detect Sections deterministically
        chunks_with_sections = ScopeSectionDetector.detect_sections(chunks)
        
        # Pipeline Step 2: Extract Candidates deterministically
        raw_candidates = ScopeCandidateExtractor.extract_candidates(chunks_with_sections, document_id)
        
        # Pipeline Step 3: Small LLM Classification with Hybrid Retrieval Evidence
        import concurrent.futures

        classified_candidates = []
        # Process candidates concurrently to drastically reduce processing time
        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
            # Create a helper function that passes project_id along with the candidate
            def classify(candidate):
                return ScopeClassifier.classify_candidate(project_id, candidate)
                
            classified_candidates = list(executor.map(classify, raw_candidates))
            
        # Pipeline Step 4: Fuzzy Deduplication
        deduped_candidates = ScopeDeduplicator.deduplicate(classified_candidates)
        
        # Format for downstream smart diff and saving
        extracted_data = {
            "scope_items": deduped_candidates,
            "deliverables": [],
            "stakeholders": []
        }
        
        # Check if there is an existing DRAFT baseline for the project
        existing_draft = BaselineRepository.get_draft_baseline(db, project_id)
        
        if existing_draft:
            baseline_id = existing_draft["id"]
            BaselineRepository.update_baseline_source_document(db, baseline_id, document_id)
            BaselineRepository.delete_stakeholders_by_project(db, project_id)
        else:
            # Get max version to auto-increment it for the new draft
            max_v = BaselineRepository.get_max_baseline_version(db, project_id)
            next_version = max_v + 1
            
            # Create draft baseline
            baseline_id = BaselineRepository.create_baseline(db, project_id, next_version, document_id)
            
            # Copy items from latest APPROVED baseline to carry forward historical data
            latest_approved = BaselineRepository.get_latest_approved_baseline(db, project_id)
            if latest_approved:
                app_baseline_id = latest_approved["id"]
                # Copy scope items
                BaselineRepository.copy_scope_items(db, app_baseline_id, baseline_id)
                
                # Copy deliverables
                BaselineRepository.copy_deliverables(db, app_baseline_id, baseline_id)

            BaselineRepository.delete_stakeholders_by_project(db, project_id)
        # Check if this project has an approved baseline to compare against
        has_approved = BaselineRepository.get_latest_approved_baseline(db, project_id) is not None

        # Smart Diffing (UPSERT)
        existing_scope_items = BaselineRepository.get_scope_items_for_diff(db, baseline_id)
        
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
                status_change_tag = None
                old_type = existing_item["scope_type"]
                # Only show 'Changed from X to Y' if there is an approved version
                if has_approved and old_type != item_type:
                    status_change_tag = f"Changed from {old_type} to {item_type}"
                    
                BaselineRepository.update_scope_item(
                    db=db,
                    item_id=existing_item["id"],
                    description=item.get("description", ""),
                    scope_type=item_type,
                    source_document_id=document_id,
                    source_page=item.get("source_page"),
                    source_section=item.get("source_section"),
                    evidence_text=item.get("evidence_text", ""),
                    confidence=item.get("confidence", 0.5),
                    status_change_tag=status_change_tag,
                    deadline=item.get("deadline")
                )
            else:
                BaselineRepository.insert_scope_item_extracted(
                    db=db,
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
                    deadline=item.get("deadline")
                )
            
        # UPSERT deliverables
        existing_deliverables = BaselineRepository.get_deliverables_for_diff(db, baseline_id)
        
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
                    db=db,
                    item_id=existing_deliv["id"],
                    description=item.get("description", ""),
                    deadline=deadline,
                    owner=item.get("owner"),
                    source_document_id=document_id
                )
            else:
                BaselineRepository.insert_deliverable(
                    db=db,
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
                db=db,
                project_id=project_id,
                name=stakeholder.get("name", "Unknown"),
                email=stakeholder.get("email"),
                role=stakeholder.get("role"),
                responsibility=stakeholder.get("responsibility")
            )
            
        # Update project status
        BaselineRepository.update_project_monitoring_status(db, project_id, 'BASELINE_PENDING_REVIEW')
        db.commit()
        
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Baseline extraction failed: {e}")
        
    return {"success": True, "message": "Draft baseline extracted", "data": {"baseline_id": baseline_id}}

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
        source_document_id=source_document_id
    )
    db.commit()
    
    try:
        # Sync to Vector DB so it can be queried by agents
        text_to_embed = f"{item.name}: {item.description or ''}\nReasoning: {item.evidence_text or ''}"
        embeddings = EmbeddingService.encode_batch([text_to_embed])
        chunk = {
            "chunk_index": item_id, # Use item_id to make it unique
            "text": text_to_embed,
            "page_number": 0
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
