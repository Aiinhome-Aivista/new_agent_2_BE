# pyrefly: ignore [missing-import]
import sys
if sys.stdout and hasattr(sys.stdout, 'reconfigure'):
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass
if sys.stderr and hasattr(sys.stderr, 'reconfigure'):
    try:
        sys.stderr.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

# pyrefly: ignore [missing-import]
from fastapi import APIRouter, Depends, HTTPException, status, Body
from pydantic import BaseModel
from typing import List, Optional
import os
import difflib
import re
import time
from core.database import get_db, get_db_connection
from core.helpers import safe_json_loads, safe_json_dumps, is_title_match, find_best_match
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
from services.baseline_logger import BaselineLogger

# pyrefly: ignore [missing-import]
import mysql.connector
from repositories.document_repository import DocumentRepository
import json
# pyrefly: ignore [missing-import]
from fastapi import Query
import threading
import uuid
import tempfile
from services.storage_service import StorageService
from services.project_planning_service import ProjectPlanningService

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
        print("!!! Background baseline pipeline failed to connect to database !!!")
        return

    logger = BaselineLogger(project_id, document_id)

    def get_valid_db_conn():
        nonlocal thread_conn
        try:
            if thread_conn and thread_conn.is_connected():
                thread_conn.ping(reconnect=True, attempts=3, delay=1)
                return thread_conn
        except Exception:
            pass
        try:
            new_conn = get_db_connection()
            if new_conn:
                thread_conn = new_conn
                logger.log("Successfully refreshed MySQL connection.", level="INFO")
        except Exception as conn_err:
            logger.log(f"Failed to reconnect to database: {conn_err}", level="ERROR")
        return thread_conn

    def emit(step: str, progress: int):
        logger.log(f"[PROGRESS {progress}%] {step}")
        conn = get_valid_db_conn()
        if not conn:
            return
        try:
            upd_cursor = conn.cursor()
            upd_cursor.execute(
                "UPDATE documents SET processing_progress = %s, processing_step = %s WHERE id = %s",
                (progress, step, document_id)
            )
            conn.commit()
            upd_cursor.close()
        except Exception as ex:
            logger.log(f"Failed to update baseline progress in DB for {step}: {ex}", level="WARN")

    t_start = time.time()
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

        doc_name = doc.get('document_name', doc.get('storage_key', 'Unknown'))
        doc_type = doc.get('document_type', 'EL')
        storage_key = doc.get('storage_key', 'Unknown')
        log_paths = logger.get_log_paths()

        logger.banner(">> [BASELINE EXTRACTION PIPELINE] Starting Scope Extraction")
        logger.input("Project ID", project_id)
        logger.input("Document ID", document_id)
        logger.input("Document Name", doc_name)
        logger.input("Document Type", doc_type)
        logger.input("Extraction Mode", mode)
        logger.input("Storage Key", storage_key)
        logger.log(f"Log File (Cumulative): {log_paths['unified_log']}")
        logger.log(f"Log File (Run Specific): {log_paths['run_log']}")

        # ── Detect inspection mode BEFORE any destructive operation ──
        # Inspection mode must not delete or alter previously persisted project data
        # or production vector records.
        INSPECTION_MODE = os.getenv("BASELINE_INSPECTION_MODE", "true").lower() in ("true", "1", "yes")
        if INSPECTION_MODE:
            logger.banner("INSPECTION MODE ENABLED — Destructive cleanup and production persistence SKIPPED")
            logger.process("Inspection mode: skipping VectorDB supersede cleanup to protect existing vector data.")
        else:
            # Automatic VectorDB version control: remove vector chunks of any
            # previous, superseded EL/IFA documents for this project.
            logger.process("Purging VectorDB chunks of superseded EL/IFA documents for this project...")
            _supersede_previous_vector_versions(thread_conn, project_id, document_id)

        ext = os.path.splitext(doc["storage_key"])[1].lower()
        temp_path = os.path.join(tempfile.gettempdir(), f"temp_{uuid.uuid4()}{ext}")

        # ── Step 1: Document Parsing & Text Extraction ──
        t_step1 = time.time()
        logger.step_start(1, "Document Parsing & Text Extraction")
        logger.input("Storage Key", doc["storage_key"])
        logger.input("Target Extension", ext)
        logger.process("Downloading file from storage to temporary workspace...")
        try:
            StorageService.download_to_temp_file(doc["storage_key"], temp_path)
            file_size_kb = os.path.getsize(temp_path) / 1024 if os.path.exists(temp_path) else 0
            logger.input("Downloaded File Size", f"{file_size_kb:.1f} KB")
            logger.process("Running MarkItDown / Docling parser and header-aware chunk splitter...")
            chunks = DocumentService.parse_document(temp_path, ext)
        finally:
            if os.path.exists(temp_path):
                os.remove(temp_path)
        step1_time = time.time() - t_step1
        total_chars = sum(len(c.get("text", "")) for c in chunks)
        logger.output("Total Chunks Created", len(chunks))
        logger.output("Total Characters Extracted", total_chars)
        if chunks:
            sample_preview = chunks[0].get("text", "")[:120].replace("\n", " ")
            logger.output("Chunk 0 Sample", f'"{sample_preview}..."')

        # ── Terminal Log: First 20 Raw Chunks (Step 1) ──
        preview_count_s1 = min(20, len(chunks))
        logger.raw_log(f"\n    >> [Step 1: First {preview_count_s1} of {len(chunks)} Raw Extracted Chunks] <<")
        for i in range(preview_count_s1):
            c = chunks[i]
            c_idx = c.get("chunk_index", i)
            c_page = c.get("page_number")
            page_info = f"page={c_page}" if c_page is not None else "page=N/A"
            c_text = (c.get("text") or "").strip()
            c_len = len(c_text)
            snippet = c_text[:160].replace("\r", " ").replace("\n", " ").strip()
            logger.raw_log(f"    [Chunk {i+1:02d}/20] chunk_index={c_idx} | {page_info} | length={c_len} chars")
            logger.raw_log(f"      Text: \"{snippet}...\"")
        logger.raw_log("")

        # Check / Ensure ChromaDB vector indexing for Phase 1 Vector Heatmap
        try:
            coll = ChromaService.get_collection()
            if coll:
                existing_probe = coll.get(
                    where={"$and": [{"project_id": {"$eq": project_id}}, {"document_id": {"$eq": document_id}}]},
                    limit=1,
                )
                if not (existing_probe.get("ids") or []):
                    logger.process("Document chunks not yet indexed in ChromaDB; indexing for vector heatmap...")
                    RAGService.index_document(project_id, document_id, doc_name, doc_type, chunks)
                    logger.output("ChromaDB Vector Indexing", f"Indexed {len(chunks)} chunks into vector store.")
                else:
                    logger.output("ChromaDB Vector Indexing", "Document already indexed in vector store.")
        except Exception as idx_err:
            logger.log(f"Notice: Non-fatal vector indexing check: {idx_err}", level="WARN")

        logger.step_end(1, "Document Parsing & Text Extraction", step1_time)

        # ── Step 2: Scope Section Detection ──
        t_step2 = time.time()
        emit("Detecting Scope Sections", 15)
        logger.step_start(2, "Scope Section Detection")
        logger.input("Input Chunks Count", len(chunks))
        logger.process("Executing deterministic structural heading detection on chunk boundaries...")
        chunks_with_sections, section_stats = ScopeSectionDetector.detect_sections_with_stats(chunks)
        step2_time = time.time() - t_step2

        logger.output("Categories Defined in Taxonomy", section_stats["categories_defined"])
        logger.output("Distinct Categories Detected in Document", section_stats["categories_detected"])
        logger.output("Total Sectioned Segments Created", len(chunks_with_sections))
        for sec in section_stats["detected_sections"]:
            cnt = sum(1 for c in chunks_with_sections if c.get("section") == sec)
            logger.raw_log(f"    - Section '{sec}': {cnt} segment(s)")

        # ── Terminal Log: First 20 Section-Tagged Chunks (Step 2) ──
        preview_count_s2 = min(20, len(chunks_with_sections))
        logger.raw_log(f"\n    >> [Step 2: First {preview_count_s2} of {len(chunks_with_sections)} Section-Tagged Chunks] <<")
        for i in range(preview_count_s2):
            c = chunks_with_sections[i]
            c_idx = c.get("chunk_index", i)
            seg_idx = c.get("segment_index", 0)
            sec_name = c.get("section", "General")
            conf_val = c.get("section_confidence", 1.0)
            inferred_str = "(Inferred)" if c.get("is_inferred") else "(Structural Heading)"
            c_text = (c.get("text") or "").strip()
            c_len = len(c_text)
            snippet = c_text[:160].replace("\r", " ").replace("\n", " ").strip()
            logger.raw_log(f"    [ScopeSection] chunk_index={c_idx} seg={seg_idx} belongs to Section: '{sec_name}' [{inferred_str}, conf={conf_val:.2f}] | length={c_len} chars")
            logger.raw_log(f"      Text: \"{snippet}...\"")
        logger.raw_log("")
        logger.step_end(2, "Scope Section Detection", step2_time)

        # ── Step 3: Candidate Scope Extraction ──
        t_step3 = time.time()
        emit("Extracting Scope Candidates", 30)
        logger.step_start(3, "Candidate Scope Extraction")
        logger.input("Sectioned Chunks Count", len(chunks_with_sections))
        logger.input("Extraction Mode", mode)
        coverage_audit = {}
        if mode == DEEP_SCAN:
            logger.process("Running DEEP_SCAN (Map-Reduce + Heatmap + Action/Entity Protection)...")
            from services.deep_scan_extractor import DeepScanExtractor
            raw_candidates, coverage_audit = DeepScanExtractor.extract_candidates_with_coverage(
                project_id=project_id,
                document_id=document_id,
                document_type=doc.get("document_type", "EL"),
                parsed_chunks=chunks,
                sectioned_chunks=chunks_with_sections,
                logger=logger,
            )
        else:
            logger.process("Running QUICK deterministic extractor (bullets, numbered lists, table rows, clauses)...")
            raw_candidates = ScopeCandidateExtractor.extract_candidates(chunks_with_sections, document_id)
        step3_time = time.time() - t_step3

        logger.output("Canonical Candidates Extracted", len(raw_candidates))
        if coverage_audit:
            ret_h = coverage_audit.get("retrieval_health", {})
            if ret_h:
                logger.output("ChromaDB Vector Retrieval Health", {
                    "Status": ret_h.get("retrieval_status", "UNKNOWN"),
                    "Queries Total": ret_h.get("queries_total", 0),
                    "Queries Successful": ret_h.get("queries_successful", 0),
                    "Queries Failed": ret_h.get("queries_failed", 0),
                    "Hot Chunks Count": ret_h.get("hot_chunks_count", 0),
                    "Fallback Used": ret_h.get("used_fallback", False),
                })
            logger.output("MAP-to-REDUCE Candidate Coverage Audit", {
                "Total MAP Raw Candidates": coverage_audit.get("total_map_candidates", 0),
                "Canonical Candidates Produced": coverage_audit.get("total_canonical_candidates", 0),
                "Retained Distinct Count": coverage_audit.get("retained_count", 0),
                "Consolidated Merged Count": coverage_audit.get("merged_count", 0),
                "Excluded Count": coverage_audit.get("excluded_count", 0),
                "Unresolved Count": coverage_audit.get("unresolved_count", 0),
                "Accounting Reconciled": "YES" if (coverage_audit.get("map_accounting_reconciled") and coverage_audit.get("canonical_reconciled")) else "NO",
                "Coverage Accounting": f"{coverage_audit.get('coverage_percentage', 100.0)}%",
            })

        for idx, cand in enumerate(raw_candidates, 1):
            logger.item(
                idx,
                cand.get("name", ""),
                {
                    "Section": cand.get("section", "General"),
                    "Type": "Pure Milestone" if cand.get("is_pure_milestone") else "Clause",
                    "Locator": cand.get("source_locator", "N/A"),
                },
            )
        logger.step_end(3, "Candidate Scope Extraction", step3_time)

        # ── [INSPECTION MODE: CLEAN STOP AFTER STEP 3] ──
        # Inspection mode was detected above. Stop immediately after Step 3.
        # Generate diagnostic artifact, log full coverage audit, and DO NOT
        # write unclassified candidates to production baseline tables.

        if INSPECTION_MODE:
            import json
            from datetime import datetime, timezone
            logs_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "logs"))
            os.makedirs(logs_dir, exist_ok=True)
            timestamp_str = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
            inspection_artifact_path = os.path.join(logs_dir, f"baseline_inspection_p{project_id}_d{document_id}.json")
            run_artifact_path = os.path.join(logs_dir, f"baseline_inspection_p{project_id}_d{document_id}_{timestamp_str}.json")

            prov_stats = coverage_audit.get("provenance", {}) if coverage_audit else {}
            diagnostic_artifact = {
                "project_id": project_id,
                "document_id": document_id,
                "document_name": doc.get("document_name", "demo_EL.docx"),
                "timestamp_utc": datetime.now(timezone.utc).isoformat(),
                "extraction_mode": mode,
                "inspection_mode_safety": {
                    "early_termination_after": "Step 3 (Deep Scan Extraction)",
                    "steps_executed": ["Step 1: Document Parsing", "Step 2: Section Detection", "Step 3: Deep Scan Extraction"],
                    "steps_skipped": ["Step 4: Classification", "Step 5-10: Baseline Persistence & Scheduling"],
                    "vector_supersede_purged": False,
                    "production_baseline_mutated": False,
                },
                "step1_document_parsing": {
                    "total_chunks_extracted": len(chunks),
                    "total_characters_extracted": total_chars,
                    "sample_preview": chunks[0].get("text", "")[:200] if chunks else "",
                    "chunks_sample": [
                        {
                            "chunk_index": c.get("chunk_index"),
                            "page_number": c.get("page_number"),
                            "length": len(c.get("text", "")),
                            "snippet": c.get("text", "")[:180],
                        }
                        for c in chunks[:15]
                    ],
                },
                "step2_section_detection": {
                    "categories_defined": section_stats.get("categories_defined", 16),
                    "categories_detected": section_stats.get("categories_detected", len(section_stats.get("detected_sections", []))),
                    "detected_sections": section_stats.get("detected_sections", []),
                    "unmapped_headings": section_stats.get("unmapped_headings", []),
                    "total_segments": len(chunks_with_sections),
                    "all_segments": [
                        {
                            "chunk_index": c.get("chunk_index"),
                            "segment_index": c.get("segment_index", 0),
                            "section": c.get("section"),
                            "confidence": c.get("section_confidence", 1.0),
                            "is_inferred": c.get("is_inferred", False),
                            "parent_section": c.get("parent_section"),
                            "section_source": c.get("section_source"),
                            "snippet": c.get("text", "")[:150],
                        }
                        for c in chunks_with_sections
                    ],
                },
                "step3_candidate_extraction": {
                    "canonical_candidates_count": len(raw_candidates),
                    "retrieval_health": coverage_audit.get("retrieval_health", {}),
                    "accounting_reconciled": bool(coverage_audit.get("map_accounting_reconciled") and coverage_audit.get("canonical_reconciled")),
                    "coverage_audit": coverage_audit,
                    "provenance_summary": {
                        "verified_count": prov_stats.get("verified_count", 0),
                        "partially_verified_count": prov_stats.get("partially_verified_count", 0),
                        "unresolved_count": prov_stats.get("unresolved_count", 0),
                        "verified_percentage": prov_stats.get("verified_percentage", 100.0),
                    },
                    "section_breakdown": coverage_audit.get("section_breakdown", DeepScanExtractor.compute_section_breakdown(raw_candidates)),
                    "canonical_candidates": raw_candidates,
                },
            }
            # Save latest document-scoped artifact and historical run artifact
            with open(inspection_artifact_path, "w", encoding="utf-8") as f_art:
                json.dump(diagnostic_artifact, f_art, indent=2, default=str)
            try:
                with open(run_artifact_path, "w", encoding="utf-8") as f_run:
                    json.dump(diagnostic_artifact, f_run, indent=2, default=str)
            except Exception:
                pass

            sec_bk = diagnostic_artifact["step3_candidate_extraction"]["section_breakdown"]
            logger.log("\n  >> [Step 3: Canonical Candidates Section Breakdown] <<")
            for sec, count in sec_bk.get("counts", {}).items():
                logger.log(f"    - Section '{sec}': {count} candidate(s)")
            logger.log(f"    Total Section Counts Sum: {sec_bk.get('total_counted')} (Reconciled with canonical count: {sec_bk.get('section_sum_equals_canonical_count')})")
            if sec_bk.get("multi_section_count", 0) > 0:
                logger.log(f"    Multi-Section Candidates: {sec_bk.get('multi_section_count')} (Tracked without double counting)")

            logger.banner("INSPECTION MODE COMPLETE — PIPELINE STOPPED AFTER STEP 3")
            logger.process(f"Diagnostic inspection artifact saved to: {inspection_artifact_path}")
            logger.process(f"Historical run artifact saved to: {run_artifact_path}")
            logger.output("Step 3 Canonical Candidates Extracted", len(raw_candidates))
            logger.output("Step 3 Canonical Candidates Verified", prov_stats.get("verified_count", 0))
            if prov_stats.get("partially_verified_count", 0) > 0:
                logger.output("Step 3 Candidates Partially Verified", prov_stats.get("partially_verified_count", 0))
            if prov_stats.get("unresolved_count", 0) > 0:
                logger.output("Step 3 Candidates Unresolved", prov_stats.get("unresolved_count", 0))
            logger.output("MAP-to-REDUCE Coverage", f"{coverage_audit.get('coverage_percentage', 100.0)}%")
            logger.output("Provenance Verification Rate", f"{prov_stats.get('verified_percentage', 100.0)}%")

            conn = get_valid_db_conn()
            if conn:
                try:
                    with conn.cursor() as cur:
                        cur.execute(
                            "UPDATE documents SET processing_status = 'COMPLETED', processing_progress = 100, "
                            "processing_step = 'Inspection Complete (Step 3)', processing_error = NULL WHERE id = %s",
                            (document_id,),
                        )
                        conn.commit()
                except Exception as db_err:
                    logger.log(f"Notice updating document inspection status: {db_err}", level="WARN")

            emit("Inspection Complete (Step 3)", 100)
            return
        else:
            # ── Step 4: Candidate Scope Classification ──
            t_step4 = time.time()
            emit("Classifying Scope Items", 50)
            logger.step_start(4, "Candidate Scope Classification")
            logger.input("Raw Candidates Count", len(raw_candidates))
            logger.process("Applying deterministic section heading pre-filter (confidence=1.0) and LLM batch classifier...")
            classified_candidates = ScopeClassifier.classify_candidates_batch(project_id, raw_candidates)
            step4_time = time.time() - t_step4

            class_counts = {}
            for c in classified_candidates:
                st = c.get("scope_type", "UNCERTAIN")
                class_counts[st] = class_counts.get(st, 0) + 1

            logger.output("Classified Items Breakdown", class_counts)
            for idx, c in enumerate(classified_candidates, 1):
                conf_pct = int((c.get("confidence") or 0.5) * 100)
                ev = (c.get("evidence_text") or "")[:80].replace("\n", " ")
                logger.item(idx, c.get('name', ''), {"Class": c.get("scope_type"), "Conf": f"{conf_pct}%", "Evidence": f'"{ev}..."' if ev else "None"})
            logger.step_end(4, "Candidate Scope Classification", step4_time)

            # ── Step 5: Fuzzy Deduplication & Scope Consolidation ──
            t_step5 = time.time()
            emit("Deduplicating Candidates", 70)
            logger.step_start(5, "Fuzzy Deduplication & Scope Consolidation")
            logger.input("Candidates Before Dedup", len(classified_candidates))
            logger.process("Applying difflib SequenceMatcher fuzzy consolidation (threshold=0.80)...")
            deduped_candidates = ScopeDeduplicator.deduplicate(classified_candidates)
            step5_time = time.time() - t_step5

            merged_count = len(classified_candidates) - len(deduped_candidates)
            logger.output("Consolidated Candidates Count", len(deduped_candidates))
            logger.output("Duplicate Items Merged", merged_count)
            for idx, c in enumerate(deduped_candidates, 1):
                logger.item(idx, c.get('name', ''), {"Class": c.get("scope_type"), "Section": c.get("section")})
            logger.step_end(5, "Fuzzy Deduplication & Scope Consolidation", step5_time)

            # ── Step 6: Milestone & Deadline Extraction ──
            t_step6 = time.time()
            emit("Extracting Milestones & Deadlines", 85)
            logger.step_start(6, "Milestone & Deadline Extraction")
            logger.input("Input Candidates Count", len(deduped_candidates))

            proj_start_date = None
            try:
                from datetime import datetime as dt_cls, date as d_cls
                p_cur = thread_conn.cursor(dictionary=True)
                p_cur.execute("SELECT start_date FROM projects WHERE id = %s", (project_id,))
                p_row = p_cur.fetchone()
                p_cur.close()
                if p_row and p_row.get("start_date"):
                    sd = p_row["start_date"]
                    if hasattr(sd, "date"):
                        proj_start_date = sd.date()
                    elif isinstance(sd, d_cls):
                        proj_start_date = sd
                    elif isinstance(sd, str):
                        proj_start_date = dt_cls.strptime(sd[:10], "%Y-%m-%d").date()
            except Exception as e:
                logger.log(f"Could not fetch project start date for relative deadlines: {e}", level="WARN")

            logger.input("Project Start Date", str(proj_start_date or 'None'))
            logger.process("Matching absolute dates and calculating relative week intervals (Weeks 1-N)...")
            enriched_candidates = MilestoneDeadlineExtractor.extract(deduped_candidates, project_start=proj_start_date)
            step6_time = time.time() - t_step6

            timeline_items = [c for c in enriched_candidates if c.get("milestone") or c.get("deadline_text") or c.get("deadline")]
            logger.output("Candidates Enriched", len(enriched_candidates))
            logger.output("Items with Timeline / Deadline", len(timeline_items))
            for idx, c in enumerate(timeline_items, 1):
                d_text = c.get("deadline_text") or "N/A"
                d_norm = c.get("deadline") or "N/A"
                m_val = c.get("milestone") or c.get("name")
                method = c.get("extraction_method", "N/A")
                logger.item(idx, m_val, {"Raw Date": d_text, "Normalized ISO": d_norm, "Method": method})
            logger.step_end(6, "Milestone & Deadline Extraction", step6_time)

        # ── Step 7: Title Normalization & Scope Items DB Persistence ──
        t_step7 = time.time()
        emit("Saving Baseline Draft", 95)
        logger.step_start(7, "Title Normalization & Scope Items DB Persistence")
        logger.input("Enriched Candidates Count", len(enriched_candidates))
        logger.process("Normalizing deliverables and milestones to remove contractual verbs...")
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

        thread_conn = get_valid_db_conn()
        existing_draft = BaselineRepository.get_draft_baseline(thread_conn, project_id)
        latest_approved = BaselineRepository.get_latest_approved_baseline(thread_conn, project_id)

        if existing_draft:
            baseline_id = existing_draft["id"]
            logger.process(f"Updating existing draft baseline #{baseline_id}...")
            BaselineRepository.update_baseline_source_document(thread_conn, baseline_id, document_id)
            BaselineRepository.delete_scope_items_by_baseline(thread_conn, baseline_id)
            BaselineRepository.delete_stakeholders_by_project(thread_conn, project_id)
            if latest_approved:
                app_baseline_id = latest_approved["id"]
                BaselineRepository.copy_scope_items(thread_conn, app_baseline_id, baseline_id)
        else:
            max_v = BaselineRepository.get_max_baseline_version(thread_conn, project_id)
            next_version = max_v + 1
            logger.process(f"Creating new draft baseline version {next_version}...")
            baseline_id = BaselineRepository.create_baseline(thread_conn, project_id, next_version, document_id)
            if latest_approved:
                app_baseline_id = latest_approved["id"]
                BaselineRepository.copy_scope_items(thread_conn, app_baseline_id, baseline_id)
            BaselineRepository.delete_stakeholders_by_project(thread_conn, project_id)

        has_approved = BaselineRepository.get_latest_approved_baseline(thread_conn, project_id) is not None
        existing_scope_items = BaselineRepository.get_scope_items_for_diff(thread_conn, baseline_id)

        valid_scope_types = {"IN_SCOPE", "OUT_OF_SCOPE", "UNCERTAIN"}
        valid_categories = {"FUNCTIONAL", "TECHNICAL", "DELIVERABLE", "OUT_OF_SCOPE", "MILESTONE"}
        valid_completion_statuses = {"ACTIVE", "COMPLETED", "CANCELLED"}

        saved_scope_count = 0
        for item in extracted_data.get("scope_items", []):
            item_name = (item.get("name") or "Unknown")[:490]
            raw_type = str(item.get("scope_type", "UNCERTAIN")).upper().strip()
            if raw_type in valid_scope_types:
                item_type = raw_type
            elif "OUT" in raw_type or "RESPONSIBILITY" in raw_type or "ASSUMPTION" in raw_type:
                item_type = "OUT_OF_SCOPE"
            elif "IN" in raw_type or "SCOPE" in raw_type:
                item_type = "IN_SCOPE"
            else:
                item_type = "UNCERTAIN"

            raw_cat = str(item.get("category", "")).upper().strip()
            if raw_cat in valid_categories:
                item_category = raw_cat
            elif item.get("is_pure_milestone", False) or item.get("section") == "Milestones" or "MILESTONE" in raw_cat:
                item_type = "IN_SCOPE"
                item_category = "MILESTONE"
            elif item_type == "OUT_OF_SCOPE":
                item_category = "OUT_OF_SCOPE"
            elif item.get("section") == "Deliverables" or "DELIVERABLE" in raw_cat:
                item_category = "DELIVERABLE"
            elif "TECH" in raw_cat:
                item_category = "TECHNICAL"
            else:
                item_category = "FUNCTIONAL"

            raw_status = str(item.get("milestone_status", "")).upper().strip()
            completion_status = "ACTIVE"
            if "COMPLETED" in raw_status or "DONE" in raw_status:
                completion_status = "COMPLETED"
            elif "CANCEL" in raw_status:
                completion_status = "CANCELLED"
            if completion_status not in valid_completion_statuses:
                completion_status = "ACTIVE"

            existing_item, best_ratio = find_best_match(item_name, existing_scope_items, key="name", threshold=0.8)
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
                item["_db_id"] = existing_item["id"]
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
            saved_scope_count += 1

        thread_conn.commit()
        step7_time = time.time() - t_step7
        logger.output("Draft Baseline ID", baseline_id)
        logger.output("Total Scope Items Stored in DB", saved_scope_count)
        logger.step_end(7, "Title Normalization & Scope Items DB Persistence", step7_time)

        # ── Step 8: Project Milestones & DAG Dependency Building ──
        t_step8 = time.time()
        emit("Building Milestone Dependencies", 97)
        logger.step_start(8, "Project Milestones & DAG Dependency Building")
        cursor = thread_conn.cursor()
        cursor.execute("DELETE FROM project_milestones WHERE project_id = %s AND baseline_id = %s", (project_id, baseline_id))

        milestone_dict = {}
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

        logger.input("Distinct Milestones Identified", len(milestone_dict))
        for s_idx, (m_norm, m_data) in enumerate(sorted_milestones, 1):
            logger.item(s_idx, m_data["name"], {"Seq": s_idx, "Planned Date": m_data["planned_date"] or "TBD", "Status": m_data["status"]})

        logger.process("Running LLM dependency extractor across milestone deliverables...")
        extracted_deps = MilestoneDependencyExtractor.extract_dependencies(extracted_data.get("scope_items", []), doc["document_text"] if "document_text" in doc else "")

        edges = []
        for dep in extracted_deps:
            parent_name = NormalizationService.normalize_milestone(dep["parent_milestone"], None)
            child_name = NormalizationService.normalize_milestone(dep["child_milestone"], None)
            if parent_name in name_to_id and child_name in name_to_id:
                edges.append((name_to_id[parent_name], name_to_id[child_name]))

        logger.process(f"Validating DAG acyclicity for {len(edges)} dependency edge(s)...")
        dag_valid = True
        try:
            if edges:
                MilestoneDependencyService.validate_dag(edges)
        except ValueError:
            dag_valid = False

        if dag_valid and edges:
            logger.output("DAG Validation", "PASSED (No cyclic loops detected)")
            for p, c in edges:
                cursor.execute(
                    "INSERT INTO milestone_dependencies (project_id, parent_milestone_id, child_milestone_id, dependency_type) VALUES (%s, %s, %s, 'FINISH_TO_START')",
                    (project_id, p, c)
                )
                logger.raw_log(f"    - Edge: Milestone ID {p} -> Milestone ID {c} (FINISH_TO_START)")
        else:
            if not edges:
                logger.output("DAG Extraction", "No explicit dependency pairs extracted by LLM. Generating sequential fallback...")
            else:
                logger.output("DAG Validation", "FAILED (Circular loop detected). Generating sequential fallback...")
            MilestoneDependencyService.generate_sequential_dependencies(cursor, project_id)
            logger.output("Sequential Fallback", "Applied successfully")
        cursor.close()
        step8_time = time.time() - t_step8
        logger.step_end(8, "Project Milestones & DAG Dependency Building", step8_time)

        # ── Step 9: Recurring Commitments Detection & Expansion ──
        t_step9 = time.time()
        emit("Detecting Recurring Commitments", 97)
        logger.step_start(9, "Recurring Commitments Detection")
        try:
            proj_cursor = thread_conn.cursor(dictionary=True)
            proj_cursor.execute("SELECT id, start_date, end_date FROM projects WHERE id = %s", (project_id,))
            project_record = proj_cursor.fetchone() or {}
            proj_cursor.close()

            logger.input("Project Schedule Window", f"Start: {project_record.get('start_date')} | End: {project_record.get('end_date')}")
            logger.process("Scanning scope items for periodic recurrence keywords (monthly, quarterly, weekly)...")
            RecurringDeliverableService.process_recurring_commitments(
                db=thread_conn,
                baseline_id=baseline_id,
                project_id=project_id,
                scope_items=extracted_data.get("scope_items", []),
                project=project_record,
            )
            logger.output("Recurring Deliverables Check", "Completed successfully")
        except Exception as rec_err:
            import traceback
            logger.log(f"Non-fatal error during recurring commitment processing: {rec_err}", level="WARN")
            traceback.print_exc()
        step9_time = time.time() - t_step9
        logger.step_end(9, "Recurring Commitments Detection", step9_time)


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

        # ── Step 9.5: Project Planning & Schedule Estimation (NEW) ──
        t_step_plan = time.time()
        emit("Project Planning & Schedule Estimation", 98)
        logger.step_start(10, "Project Planning & Schedule Estimation")
        planning_conflicts = []
        planning_summary = {}
        try:
            from datetime import datetime as dt_cls, date as d_cls
            import json as _json

            # Fetch project dates for planning
            plan_cursor = thread_conn.cursor(dictionary=True)
            plan_cursor.execute("SELECT start_date, end_date FROM projects WHERE id = %s", (project_id,))
            proj_row = plan_cursor.fetchone() or {}
            plan_cursor.close()

            def _to_date(v):
                if v is None:
                    return None
                if hasattr(v, 'date'):
                    return v.date()
                if isinstance(v, d_cls):
                    return v
                try:
                    return dt_cls.strptime(str(v)[:10], "%Y-%m-%d").date()
                except Exception:
                    return None

            plan_start = _to_date(proj_row.get("start_date"))
            plan_end = _to_date(proj_row.get("end_date"))

            # Build explicit milestones list for the conflict detector
            explicit_milestones = [
                {
                    "name": m_data["name"],
                    "date": m_data.get("planned_date"),
                    "type": "MILESTONE",
                }
                for _, m_data in sorted_milestones
                if m_data.get("planned_date")
            ]

            logger.input("Plan Start Date", str(plan_start or 'None (will use today)'))
            logger.input("Plan End Date", str(plan_end or 'None'))
            logger.input("Explicit Milestones for Conflict Check", len(explicit_milestones))
            logger.input("IN_SCOPE Items to Estimate", sum(
                1 for i in extracted_data.get("scope_items", []) if i.get("scope_type") == "IN_SCOPE"
            ))
            logger.process("Running ProjectPlanningService.plan_baseline()...")

            planning_result = ProjectPlanningService.plan_baseline(
                scope_items=extracted_data.get("scope_items", []),
                project_start=plan_start,
                project_end=plan_end,
                explicit_milestones=explicit_milestones,
                resource_headcount=5,   # default from EL; could be extracted from Resources section
                use_llm=True,
            )

            planning_conflicts = planning_result.conflicts_as_dicts()
            planning_summary = planning_result.summary_dict()

            # Persist estimated planning fields back to scope_items in DB
            plan_update_cursor = thread_conn.cursor()
            persisted_plan_count = 0
            for p_item in planning_result.estimated_items:
                db_id = p_item.get("_db_id")
                if not db_id:
                    continue
                plan_assumptions_json = _json.dumps(p_item.get("plan_assumptions") or [])
                plan_update_cursor.execute("""
                    UPDATE scope_items
                    SET estimated_start_date = %s,
                        estimated_deadline = %s,
                        date_provenance = %s,
                        estimation_confidence = %s,
                        estimation_basis = %s,
                        plan_assumptions = %s,
                        estimated_effort_low = %s,
                        estimated_effort_likely = %s,
                        estimated_effort_high = %s,
                        baseline_approval_status = 'PENDING_REVIEW'
                    WHERE id = %s
                """, (
                    p_item.get("estimated_start_date"),
                    p_item.get("estimated_deadline"),
                    p_item.get("date_provenance", "AI_ESTIMATE"),
                    p_item.get("estimation_confidence", "LOW"),
                    (p_item.get("estimation_basis") or "")[:500],
                    plan_assumptions_json[:2000],
                    p_item.get("estimated_effort_low"),
                    p_item.get("estimated_effort_likely"),
                    p_item.get("estimated_effort_high"),
                    db_id,
                ))
                persisted_plan_count += 1
            thread_conn.commit()
            plan_update_cursor.close()

            step_plan_time = time.time() - t_step_plan
            logger.output("Items Estimated", planning_result.plan_summary.get("estimated_item_count", 0))
            logger.output("Schedule Conflicts Detected", len(planning_conflicts))
            logger.output("Plan Fields Persisted", persisted_plan_count)
            for idx, conflict in enumerate(planning_conflicts, 1):
                logger.item(idx, conflict.get("title", ""), {
                    "ID": conflict.get("conflict_id", ""),
                    "Severity": conflict.get("severity", ""),
                })
            logger.step_end(10, "Project Planning & Schedule Estimation", step_plan_time)
        except Exception as plan_err:
            import traceback as _tb
            step_plan_time = time.time() - t_step_plan
            logger.log(
                f"[WARN] Project Planning step failed (non-fatal): {plan_err}\n{_tb.format_exc()}",
                level="WARN"
            )
            logger.step_end(10, "Project Planning & Schedule Estimation", step_plan_time)

        BaselineRepository.update_project_monitoring_status(thread_conn, project_id, 'BASELINE_PENDING_REVIEW')

        # Mark as COMPLETED
        upd2 = thread_conn.cursor()
        upd2.execute(
            "UPDATE documents SET processing_status = 'COMPLETED', processing_progress = 100, processing_step = 'Completed', processing_error = NULL WHERE id = %s",
            (document_id,)
        )
        thread_conn.commit()
        upd2.close()

        total_time = time.time() - t_start

        in_scope_saved = sum(1 for item in extracted_data.get("scope_items", []) if item.get("scope_type") == "IN_SCOPE" or item.get("is_pure_milestone"))
        out_of_scope_saved = sum(1 for item in extracted_data.get("scope_items", []) if item.get("scope_type") == "OUT_OF_SCOPE")
        assumptions_saved = sum(1 for item in extracted_data.get("scope_items", []) if item.get("section") == "Assumptions" or item.get("source_section") == "Assumptions")

        # ── Step 11: Final Completion & Summary ──
        logger.banner(">> [BASELINE EXTRACTION COMPLETE] Project Summary")
        logger.output("Project ID", project_id)
        logger.output("Document ID", document_id)
        logger.output("Baseline Draft ID", f"#{baseline_id}")
        logger.output("Total Pipeline Runtime", f"{total_time:.2f} seconds")
        logger.raw_log("\n  [TIMINGS BREAKDOWN]")
        logger.raw_log(f"    - Step 1  (Document Parsing & Text Extraction): {step1_time:.2f}s")
        logger.raw_log(f"    - Step 2  (Scope Section Detection):            {step2_time:.2f}s")
        logger.raw_log(f"    - Step 3  (Candidate Scope Extraction):         {step3_time:.2f}s")
        logger.raw_log(f"    - Step 4  (Scope Item Classification):          {step4_time:.2f}s")
        logger.raw_log(f"    - Step 5  (Fuzzy Deduplication):                {step5_time:.2f}s")
        logger.raw_log(f"    - Step 6  (Milestone & Deadline Extraction):    {step6_time:.2f}s")
        logger.raw_log(f"    - Step 7  (Title Normalization & DB Save):      {step7_time:.2f}s")
        logger.raw_log(f"    - Step 8  (Milestones & DAG Dependencies):      {step8_time:.2f}s")
        logger.raw_log(f"    - Step 9  (Recurring Commitments):              {step9_time:.2f}s")
        logger.raw_log(f"    - Step 10 (Project Planning & Schedule Est.):   {step_plan_time:.2f}s")
        logger.raw_log("\n  [SCOPE METRICS SUMMARY]")
        logger.raw_log(f"    - In-Scope Deliverables:            {in_scope_saved}")
        logger.raw_log(f"    - Out-of-Scope Exclusions:          {out_of_scope_saved}")
        logger.raw_log(f"    - Document Assumptions:             {assumptions_saved}")
        logger.raw_log(f"    - Project Milestones Created:       {len(milestone_dict)}")
        logger.raw_log(f"    - Validated DAG Dependencies:       {len(edges)}")
        logger.raw_log(f"    - Planning Estimates Generated:     {planning_summary.get('estimated_item_count', 0)}")
        logger.raw_log(f"    - Schedule Conflicts Detected:      {len(planning_conflicts)}")
        logger.raw_log(f"    - Monitoring Status:                BASELINE_PENDING_REVIEW")
        logger.raw_log(f"    - Document Status:                  COMPLETED")
        logger.raw_log("\n  [PERSISTENT LOG FILES]")
        logger.raw_log(f"    - Unified Log File:   {log_paths['unified_log']}")
        logger.raw_log(f"    - Run-Specific Log:   {log_paths['run_log']}")
        logger.raw_log("=" * 80 + "\n")

    except Exception as e:
        import traceback
        tb_str = traceback.format_exc()
        logger.error("run_baseline_pipeline", str(e), tb_str)
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

class ScopeItemScheduleUpdate(BaseModel):
    milestone: Optional[str] = None
    deadline: Optional[str] = None
    deadline_text: Optional[str] = None
    dependencies: Optional[List[str]] = None

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
        
    scope_item_norm = NormalizationService.normalize_scope_item(item.name)
    milestone_norm = NormalizationService.normalize_milestone(item.milestone, scope_item_norm) if item.milestone else None
    deadline_norm = item.deadline if (item.deadline and re.match(r'^\d{4}-\d{2}-\d{2}', str(item.deadline))) else None
    deadline_txt = item.deadline_text or item.deadline or item.milestone
    
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
        deadline=deadline_norm,
        milestone=item.milestone,
        deadline_text=deadline_txt,
        scope_item_normalized=scope_item_norm,
        milestone_normalized=milestone_norm,
        deadline_original=deadline_txt,
        deadline_normalized=deadline_norm
    )
    
    # If milestone or deadline was specified for IN_SCOPE, register/link project_milestones
    if (item.milestone or deadline_norm) and item.scope_type == "IN_SCOPE":
        try:
            m_title = (item.milestone or item.name).strip()
            cursor = db.cursor(dictionary=True)
            cursor.execute("""
                SELECT id FROM project_milestones 
                WHERE project_id = %s AND baseline_id = %s AND LOWER(TRIM(name)) = LOWER(TRIM(%s))
            """, (project_id, baseline_id, m_title.lower()))
            existing_m = cursor.fetchone()
            if not existing_m:
                cursor.execute("""
                    SELECT COALESCE(MAX(sequence), 0) + 1 as next_seq FROM project_milestones
                    WHERE project_id = %s AND baseline_id = %s
                """, (project_id, baseline_id))
                seq_row = cursor.fetchone()
                next_seq = seq_row["next_seq"] if seq_row else 1
                cursor.execute("""
                    INSERT INTO project_milestones (project_id, baseline_id, name, sequence, status, planned_date)
                    VALUES (%s, %s, %s, %s, 'Planned', %s)
                """, (project_id, baseline_id, m_title, next_seq, deadline_norm))
                m_id = cursor.lastrowid
            else:
                m_id = existing_m["id"]
                if deadline_norm:
                    cursor.execute("UPDATE project_milestones SET planned_date = COALESCE(planned_date, %s) WHERE id = %s", (deadline_norm, m_id))
            
            cursor.execute("""
                INSERT INTO scope_milestone_mapping (scope_item_id, milestone_id, weight)
                VALUES (%s, %s, 1.0)
                ON DUPLICATE KEY UPDATE weight = VALUES(weight)
            """, (item_id, m_id))
            cursor.close()
        except Exception as e:
            print(f"Warning: Failed to create/link project milestone for manual scope item: {e}")
            
    db.commit()
    
    try:
        # Sync to Vector DB so it can be queried by agents
        text_to_embed = f"{item.name}: {item.description or ''}\nReasoning: {item.evidence_text or ''}"
        embeddings = EmbeddingService.encode_batch([text_to_embed])
        
        # Determine baseline_version (0 for draft)
        version_rec = BaselineRepository.get_latest_approved_baseline(db, project_id)
        version = version_rec["version"] if version_rec and "version" in version_rec else 0

        chunk = {
            "chunk_index": item_id,
            "text": text_to_embed,
            "page_number": 0,
            "scope_item_normalized": scope_item_norm,
            "scope_type": item.scope_type,
            "milestone_normalized": milestone_norm or "NULL",
            "deadline_original": deadline_txt or "NULL",
            "deadline_normalized": deadline_norm or "NULL",
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

@router.patch("/items/{item_id}/schedule")
def schedule_scope_item(
    project_id: int,
    item_id: int,
    data: ScopeItemScheduleUpdate,
    current_user: dict = Depends(require_roles(["ADMIN", "ENGAGEMENT_MANAGER", "PROJECT_LEAD"])),
    db: mysql.connector.connection.MySQLConnection = Depends(get_db)
):
    verify_project_access(project_id, current_user, db)
    
    cursor = db.cursor(dictionary=True)
    cursor.execute("SELECT * FROM scope_items WHERE id = %s AND project_id = %s", (item_id, project_id))
    scope_item = cursor.fetchone()
    if not scope_item:
        cursor.close()
        raise HTTPException(status_code=404, detail="Scope item not found")
        
    baseline_id = scope_item["baseline_id"]
    # Parse date to ISO format YYYY-MM-DD
    deadline_norm = None
    if data.deadline:
        s = str(data.deadline).strip()
        if re.match(r'^\d{4}-\d{2}-\d{2}', s):
            deadline_norm = s[:10]
        else:
            m = re.match(r'^(\d{1,2})[-/](\d{1,2})[-/](\d{4})', s)
            if m:
                d, mth, y = m.groups()
                deadline_norm = f"{y}-{int(mth):02d}-{int(d):02d}"

    milestone_val = data.milestone.strip() if data.milestone else (scope_item.get("milestone") or scope_item["name"])
    scope_item_norm = scope_item.get("scope_item_normalized") or NormalizationService.normalize_scope_item(scope_item["name"])
    milestone_norm = NormalizationService.normalize_milestone(milestone_val, scope_item_norm)
    deadline_text_val = data.deadline_text or data.deadline or milestone_val
    
    cursor.execute("""
        UPDATE scope_items 
        SET milestone = %s,
            milestone_normalized = %s,
            deadline = %s,
            deadline_normalized = %s,
            deadline_text = %s,
            deadline_original = %s
        WHERE id = %s AND project_id = %s
    """, (milestone_val, milestone_norm, deadline_norm, deadline_norm, deadline_text_val, deadline_text_val, item_id, project_id))
    
    # Sync or create corresponding project_milestones
    if milestone_val and scope_item["scope_type"] == "IN_SCOPE":
        cursor.execute("""
            SELECT id FROM project_milestones 
            WHERE project_id = %s AND baseline_id = %s AND LOWER(TRIM(name)) = LOWER(TRIM(%s))
        """, (project_id, baseline_id, milestone_val.lower()))
        existing_m = cursor.fetchone()
        if existing_m:
            if deadline_norm:
                cursor.execute("""
                    UPDATE project_milestones 
                    SET planned_date = %s
                    WHERE id = %s
                """, (deadline_norm, existing_m["id"]))
            m_id = existing_m["id"]
        else:
            cursor.execute("""
                SELECT COALESCE(MAX(sequence), 0) + 1 as next_seq FROM project_milestones
                WHERE project_id = %s AND baseline_id = %s
            """, (project_id, baseline_id))
            seq_row = cursor.fetchone()
            next_seq = seq_row["next_seq"] if seq_row else 1
            cursor.execute("""
                INSERT INTO project_milestones (project_id, baseline_id, name, sequence, status, planned_date)
                VALUES (%s, %s, %s, %s, 'Planned', %s)
            """, (project_id, baseline_id, milestone_val, next_seq, deadline_norm))
            m_id = cursor.lastrowid
            
        cursor.execute("""
            INSERT INTO scope_milestone_mapping (scope_item_id, milestone_id, weight)
            VALUES (%s, %s, 1.0)
            ON DUPLICATE KEY UPDATE weight = VALUES(weight)
        """, (item_id, m_id))

    # If dependencies were provided, store them in deliverable_progress
    if data.dependencies is not None:
        deps_json = json.dumps(data.dependencies)
        cursor.execute("""
            SELECT id FROM deliverable_progress WHERE scope_item_id = %s ORDER BY id DESC LIMIT 1
        """, (item_id,))
        prog_row = cursor.fetchone()
        if prog_row:
            cursor.execute("UPDATE deliverable_progress SET dependencies = %s WHERE id = %s", (deps_json, prog_row["id"]))
        else:
            cursor.execute("SELECT version FROM scope_baselines WHERE id = %s", (baseline_id,))
            b_row = cursor.fetchone()
            b_ver = b_row["version"] if b_row and b_row["version"] else 1
            cursor.execute("""
                INSERT INTO deliverable_progress (
                    scope_item_id, project_id, status_code, dependencies, 
                    progress_percentage, baseline_version, progress_source, risk_evaluation_id
                )
                VALUES (%s, %s, 'NOT_STARTED', %s, 0, %s, 'USER', NULL)
            """, (item_id, project_id, deps_json, b_ver))

    db.commit()
    cursor.close()
    
    updated_item = BaselineRepository.get_scope_item(db, item_id)
    return {"success": True, "message": "Deliverable scheduled on timeline successfully", "data": updated_item}

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

# Canonical domain-agnostic title matching algorithm imported from core.helpers
_is_title_match = is_title_match

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
    except Exception:
        return

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
        
    if not open_items:
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
        r = safe_json_loads(item.get("reasoning"))
            
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
    except Exception:
        pass

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
            r_obj = safe_json_loads(item.get("reasoning"))
            due_date_str = r_obj.get("due_date")
            if due_date_str:
                d_parsed = _parse_due_date(due_date_str)
                if d_parsed is not None:
                    days_until_due = d_parsed

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
        except Exception:
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
        r_json = safe_json_loads(item.get("reasoning"))
        if isinstance(r_json, dict) and r_json:
            if new_graph_role == "ROOT_CAUSE" and old_exec_status in ["BLOCKED", "IN_PROGRESS"]:
                if "business_impact" in r_json and isinstance(r_json["business_impact"], dict):
                    r_json["business_impact"]["immediate"] = "Prerequisites satisfied. Unblocked and ready for execution."
                r_json["executive_summary"] = f"Prerequisites completed; {title} is now unblocked and ready for implementation."
            updated_reasoning_str = safe_json_dumps(r_json)
        else:
            updated_reasoning_str = item.get("reasoning")

        # Step 7: Update tracker_items
        # RC2 FIX: risk_score is NOT updated here — it is set at creation and is stable.
        # execution_priority_score reflects graph position (changes as graph topology changes).
        # risk_score reflects severity (set by RiskScoringEngine at creation, never changed here).
        cursor.execute("""
            UPDATE tracker_items
            SET execution_priority_score = %s,
                graph_role = %s,
                execution_status = %s,
                recommended_action = %s,
                reasoning = %s
            WHERE id = %s
        """, (
            new_exec_score,
            new_graph_role,
            new_exec_status,
            new_rec_action,
            updated_reasoning_str,
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

    # ──────────────────────────────────────────────────────────────────────────
    # ⚖️ STEP 2E: Parent > Child Constraint Enforcement
    # ──────────────────────────────────────────────────────────────────────────
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
                src_rec["new_score"] = forced_score

    # ──────────────────────────────────────────────────────────────────────────
    # 🔢 STEP 2G: Re-stamp priority_order on all open items
    # Ensures priority_order strictly reflects the finalized execution_priority_score
    # ──────────────────────────────────────────────────────────────────────────
    cursor.execute("""
        SELECT id FROM tracker_items
        WHERE project_id = %s AND status = 'OPEN'
        ORDER BY COALESCE(execution_priority_score, 0) DESC, COALESCE(risk_score, 0) DESC, id ASC
    """, (project_id,))
    open_recalc_rows = cursor.fetchall() or []
    for rank_idx, r_row in enumerate(open_recalc_rows, start=1):
        r_id = r_row["id"] if isinstance(r_row, dict) else r_row[0]
        cursor.execute("UPDATE tracker_items SET priority_order = %s WHERE id = %s", (rank_idx, r_id))

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

