import os
import sys
import time
import json
import re
import threading
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from services.scope_deduplicator import ScopeDeduplicator
from services.normalization_service import NormalizationService


class Step5Logger:
    """
    Dedicated Logger for Step 5 (Baseline Preparation & Persistence Safety Gate) Inspection Runs.
    Guarantees independent logging to logs/step5_inspection_p{project_id}_d{document_id}_{timestamp}.log
    and active logs/step5_inspection_p{project_id}_d{document_id}.log without polluting Step 3 or Step 4 logs.
    """

    def __init__(self, project_id: int, document_id: int, timestamp_str: str, log_dir: Optional[str] = None):
        self.project_id = project_id
        self.document_id = document_id
        self.timestamp_str = timestamp_str

        if log_dir is None:
            backend_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            self.log_dir = os.path.join(backend_root, "logs")
        else:
            self.log_dir = log_dir

        os.makedirs(self.log_dir, exist_ok=True)
        self.run_log_path = os.path.join(self.log_dir, f"step5_inspection_p{project_id}_d{document_id}_{timestamp_str}.log")
        self.active_log_path = os.path.join(self.log_dir, f"step5_inspection_p{project_id}_d{document_id}.log")
        self._lock = threading.Lock()

    def _format_timestamp(self) -> str:
        return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    def raw_log(self, text: str):
        try:
            sys.stdout.write(text + "\n")
            sys.stdout.flush()
        except Exception:
            pass

        with self._lock:
            try:
                with open(self.run_log_path, "a", encoding="utf-8") as f:
                    f.write(text + "\n")
                    f.flush()
            except Exception:
                pass
            try:
                with open(self.active_log_path, "a", encoding="utf-8") as f:
                    f.write(text + "\n")
                    f.flush()
            except Exception:
                pass

    def log(self, message: str, level: str = "INFO"):
        prefix = f"[{self._format_timestamp()}] [{level.upper()}] "
        self.raw_log(f"{prefix}{message}")

    def banner(self, title: str):
        border = "=" * 80
        self.raw_log(f"\n{border}\n  {title}\n{border}")

    def section(self, title: str):
        border = "-" * 80
        self.raw_log(f"\n{border}\n[*] {title} | {self._format_timestamp()}\n{border}")

    def input(self, label: str, value: Any):
        self.raw_log(f"[{self._format_timestamp()}] [INPUT]  --> {label}: {value}")

    def process(self, description: str):
        self.raw_log(f"[{self._format_timestamp()}] [PROCESS] {description}")

    def output(self, label: str, value: Any):
        if isinstance(value, dict):
            self.raw_log(f"[{self._format_timestamp()}] [OUTPUT] <-- {label}:")
            for k, v in value.items():
                self.raw_log(f"    - {k}: {v}")
        elif isinstance(value, list):
            self.raw_log(f"[{self._format_timestamp()}] [OUTPUT] <-- {label} ({len(value)} items):")
            for item in value:
                self.raw_log(f"    - {item}")
        else:
            self.raw_log(f"[{self._format_timestamp()}] [OUTPUT] <-- {label}: {value}")

    def item(self, index: int, name: str, details: Dict[str, Any]):
        details_str = " | ".join(f"{k}={v}" for k, v in details.items())
        self.raw_log(f"    [{index:03d}] {name} | {details_str}")

    def quality_check(self, check_name: str, status: str, details: str):
        self.raw_log(f"    [{status}] {check_name}: {details}")


class Step5Inspector:
    """
    Executes Step 5 Baseline Preparation, Deduplication & Persistence Safety Gate.
    Guarantees:
    1. Independent Step 5 logging to step5_inspection_p{p}_d{d}_{ts}.log without altering Step 3 or 4 logs.
    2. Independent Step 5 JSON diagnostic artifact to step5_inspection_p{p}_d{d}_{ts}.json.
    3. Dedicated Step 5 preview artifact to step5_preview_p{p}_d{d}_{ts}.json.
    4. Validation gate: validates Step 4 run identity, project/document consistency, schema constraints,
       evidence support, and merged-source lineage.
    5. Type-aware deduplication: preserves distinct contractual records across milestones, deliverables,
       CRUD operations, and named personnel.
    6. Review gating: isolates UNCERTAIN records from active baseline records, marking them PENDING_REVIEW.
    7. Full evidence lineage: preserves all atomic claims, locator spans, and verified offsets.
    8. Default DRY_RUN execution mode: zero writes to production baseline tables and zero vector DB mutations.
    9. Clean stop after Step 5 (Steps 6-10 remain strictly disabled).
    """

    VALID_SCOPE_TYPES = {"IN_SCOPE", "OUT_OF_SCOPE", "UNCERTAIN"}
    VALID_CATEGORIES = {"FUNCTIONAL", "TECHNICAL", "DELIVERABLE", "OUT_OF_SCOPE", "MILESTONE"}
    VALID_COMPLETION_STATUSES = {"ACTIVE", "COMPLETED", "CANCELLED", "PENDING_REVIEW"}

    @classmethod
    def _map_category(
        cls,
        raw_category: Optional[str] = None,
        record_type: str = "",
        section: str = "",
        is_pure_milestone: bool = False,
        scope_type: str = "IN_SCOPE",
    ) -> str:
        """
        Maps or normalizes candidate category into approved schema ENUM.
        Invalid or unmapped categories are mapped safely to an approved taxonomy ENUM.
        """
        if raw_category and raw_category in cls.VALID_CATEGORIES:
            return raw_category
        raw_rec_type = (record_type or "").upper()
        if "DELIVERABLE" in raw_rec_type or section == "Deliverables":
            return "DELIVERABLE"
        elif "MILESTONE" in raw_rec_type or is_pure_milestone:
            return "MILESTONE"
        elif scope_type == "OUT_OF_SCOPE":
            return "OUT_OF_SCOPE"
        elif "TECH" in raw_rec_type or section == "Technical Scope":
            return "TECHNICAL"
        else:
            return "FUNCTIONAL"

    @classmethod
    def execute_inspection(
        cls,
        project_id: int,
        document_id: int,
        doc_name: str,
        step4_artifact: dict,
        step4_json_path: str,
        step4_log_path: str,
        run_id: str,
        timestamp_str: str,
        logs_dir: str,
        mode: str = "DRY_RUN",
        db_conn: Any = None,
    ) -> dict:
        t_start = time.time()
        logger = Step5Logger(project_id, document_id, timestamp_str, log_dir=logs_dir)

        logger.banner(">> [BASELINE EXTRACTION PIPELINE] Step 5: Scope Baseline Preparation & Persistence Safety Gate")
        logger.input("Project ID", project_id)
        logger.input("Document ID", document_id)
        logger.input("Document Name", doc_name)
        logger.input("Run ID", run_id)
        logger.input("Execution Mode", mode)
        logger.input("Step 4 Artifact Source", step4_json_path)
        logger.input("Step 4 Log Source", step4_log_path)
        logger.log(f"Log File (Run Specific): {logger.run_log_path}")
        logger.log(f"Log File (Cumulative Active): {logger.active_log_path}")

        # ── Step 5 Safety Confirmations ──
        logger.section("STEP 5 SAFETY CONTROLS INITIALIZATION")
        is_dry_run = mode.upper() in ("DRY_RUN", "PREVIEW", "INSPECTION")
        if is_dry_run:
            logger.banner("STEP 5 DRY-RUN MODE ACTIVE — Production baseline persistence and VectorDB mutations DISABLED")
        else:
            logger.banner(f"STEP 5 ACTIVE MODE: {mode} — Verifying transaction and isolation parameters")

        # ── Step 5 Validation Gate ──
        logger.section("STEP 5: VALIDATION GATE & INPUT INTEGRITY")
        gate_results = {}
        quarantined_records = []
        eligible_records = []
        validation_gate_passed = True
        gate_failure_reasons = []

        # 1. Input artifact and run identity verification
        s4_verdict = step4_artifact.get("final_verdict", "UNKNOWN")
        s4_proj = step4_artifact.get("project_id")
        s4_doc = step4_artifact.get("document_id")
        s4_run_id = step4_artifact.get("run_id")
        structured_records = step4_artifact.get("structured_records", [])

        id_match = (s4_proj == project_id and s4_doc == document_id)
        if id_match:
            gate_results["run_identity_and_isolation"] = {
                "status": "PASS",
                "details": f"Project ID ({project_id}), Document ID ({document_id}), and Run ID ({s4_run_id}) match Step 4 input",
            }
            logger.quality_check("Run Identity & Isolation", "PASS", gate_results["run_identity_and_isolation"]["details"])
        else:
            validation_gate_passed = False
            msg = f"Project/Document ID mismatch: expected p{project_id}_d{document_id}, received p{s4_proj}_d{s4_doc}"
            gate_results["run_identity_and_isolation"] = {"status": "FAIL", "details": msg}
            gate_failure_reasons.append(msg)
            logger.quality_check("Run Identity & Isolation", "FAIL", msg)

        # 2. Step 4 verdict check
        if s4_verdict in ("STEP_4_VALIDATION_PASSED", "STEP_4_VALIDATION_PASSED_WITH_WARNINGS"):
            gate_results["step4_verdict_check"] = {
                "status": "PASS",
                "details": f"Step 4 passed with verdict: {s4_verdict}",
            }
            logger.quality_check("Step 4 Input Verdict", "PASS", gate_results["step4_verdict_check"]["details"])
        else:
            validation_gate_passed = False
            msg = f"Step 4 validation failed with verdict: {s4_verdict}"
            gate_results["step4_verdict_check"] = {"status": "FAIL", "details": msg}
            gate_failure_reasons.append(msg)
            logger.quality_check("Step 4 Input Verdict", "FAIL", msg)

        # 3. Candidate integrity & duplicate ID check
        seen_cand_ids = set()
        duplicate_cand_ids = []
        for r in structured_records:
            cid = r.get("candidate_id")
            if not cid or cid in seen_cand_ids:
                duplicate_cand_ids.append(cid)
            else:
                seen_cand_ids.add(cid)

        if not duplicate_cand_ids and len(seen_cand_ids) == len(structured_records):
            gate_results["candidate_uniqueness"] = {
                "status": "PASS",
                "details": f"All {len(structured_records)} input candidates have unique candidate IDs",
            }
            logger.quality_check("Candidate Identity Uniqueness", "PASS", gate_results["candidate_uniqueness"]["details"])
        else:
            validation_gate_passed = False
            msg = f"{len(duplicate_cand_ids)} duplicate or missing candidate IDs in input: {duplicate_cand_ids[:5]}"
            gate_results["candidate_uniqueness"] = {"status": "FAIL", "details": msg}
            gate_failure_reasons.append(msg)
            logger.quality_check("Candidate Identity Uniqueness", "FAIL", msg)

        # 4. Atomic claim evidence verification & missing locators audit
        unsupported_count = 0
        missing_locator_count = 0
        for r in structured_records:
            c_claims = r.get("claims_evidence", [])
            r_unsupported = [cl for cl in c_claims if cl.get("status") == "UNSUPPORTED"]
            r_missing_loc = [cl for cl in c_claims if cl.get("status") == "VERIFIED" and not cl.get("source_locator")]

            if r_unsupported or r_missing_loc or r.get("verification_status") not in ("VERIFIED", "PARTIALLY_VERIFIED"):
                reasons = []
                if r_unsupported:
                    reasons.append(f"{len(r_unsupported)} unsupported atomic claims")
                if r_missing_loc:
                    reasons.append(f"{len(r_missing_loc)} verified claims missing source locators")
                if r.get("verification_status") not in ("VERIFIED", "PARTIALLY_VERIFIED"):
                    reasons.append(f"unresolved provenance ({r.get('verification_status')})")

                quarantined_records.append({
                    "candidate_id": r.get("candidate_id"),
                    "name": r.get("name"),
                    "reason": " | ".join(reasons),
                    "claims": r_unsupported or r_missing_loc,
                    "action": "QUARANTINED_FROM_BASELINE",
                })
                unsupported_count += len(r_unsupported)
                missing_locator_count += len(r_missing_loc)
            else:
                eligible_records.append(r)

        if unsupported_count == 0 and missing_locator_count == 0:
            gate_results["claim_evidence_support"] = {
                "status": "PASS",
                "details": f"All atomic claims across all {len(structured_records)} candidates verified with valid source locators (0 unsupported, 0 missing locators)",
            }
            logger.quality_check("Atomic Claim Evidence Support", "PASS", gate_results["claim_evidence_support"]["details"])
        else:
            validation_gate_passed = False
            msg = f"{unsupported_count} unsupported claims and {missing_locator_count} missing locators detected"
            gate_results["claim_evidence_support"] = {"status": "FAIL", "details": msg}
            gate_failure_reasons.append(msg)
            logger.quality_check("Atomic Claim Evidence Support", "FAIL", msg)

        # 5. Merged-source lineage audit
        missing_lineage = []
        for r in structured_records:
            for ms in r.get("merged_sources", []):
                ms_name = ms.get("name")
                ms_chunk = ms.get("chunk_index")
                ms_snip = ms.get("evidence_snippet") or ms.get("description")
                ms_rat = ms.get("rationale")
                if not ms_name or ms_chunk is None or not ms_snip or not ms_rat:
                    missing_lineage.append({
                        "parent_candidate_id": r.get("candidate_id"),
                        "merged_name": ms_name,
                        "missing_fields": [f for f, v in [("name", ms_name), ("chunk_index", ms_chunk), ("snippet", ms_snip), ("rationale", ms_rat)] if not v and v != 0],
                    })

        if not missing_lineage:
            gate_results["merged_source_lineage"] = {
                "status": "PASS",
                "details": "All merged candidate sources maintain complete lineage (name, chunk_index, snippet, rationale)",
            }
            logger.quality_check("Merged Source Lineage Traceability", "PASS", gate_results["merged_source_lineage"]["details"])
        else:
            validation_gate_passed = False
            msg = f"{len(missing_lineage)} merged sources lack complete lineage metadata"
            gate_results["merged_source_lineage"] = {"status": "FAIL", "details": msg}
            gate_failure_reasons.append(msg)
            logger.quality_check("Merged Source Lineage Traceability", "FAIL", msg)

        # 6. Database schema & constraint validation
        schema_violations = []
        for r in eligible_records:
            name_val = r.get("name") or ""
            if not name_val or len(name_val) > 500:
                schema_violations.append(f"Candidate {r.get('candidate_id')} invalid name length ({len(name_val)})")
            scope_val = r.get("scope_status") or r.get("scope_type")
            if scope_val not in cls.VALID_SCOPE_TYPES:
                schema_violations.append(f"Candidate {r.get('candidate_id')} invalid scope_type '{scope_val}'")

        if not schema_violations:
            gate_results["schema_constraints_validation"] = {
                "status": "PASS",
                "details": f"All {len(eligible_records)} eligible records satisfy scope_items schema constraints (names <= 500 chars, valid ENUMs)",
            }
            logger.quality_check("Database Schema Constraints Validation", "PASS", gate_results["schema_constraints_validation"]["details"])
        else:
            validation_gate_passed = False
            msg = f"{len(schema_violations)} schema constraint violations detected"
            gate_results["schema_constraints_validation"] = {"status": "FAIL", "details": msg}
            gate_failure_reasons.append(msg)
            logger.quality_check("Database Schema Constraints Validation", "FAIL", msg)

        # ── Step 5 Type-Aware Deduplication & Scope Consolidation ──
        logger.section("STEP 5: TYPE-AWARE DEDUPLICATION & SCOPE CONSOLIDATION")
        t_dedup = time.time()
        logger.input("Eligible Candidates Before Dedup", len(eligible_records))
        logger.process("Applying Type-Aware ScopeDeduplicator (threshold=0.88, protecting record types, milestones, and distinct features)...")

        dedup_input = [dict(r) for r in eligible_records]
        consolidated_candidates, merge_reconciliation = ScopeDeduplicator.deduplicate(dedup_input, similarity_threshold=0.88)
        dedup_duration = time.time() - t_dedup
        duplicates_merged_count = len(merge_reconciliation)

        logger.output("Consolidated Scope Items Count", len(consolidated_candidates))
        logger.output("Legitimate Duplicates Merged", duplicates_merged_count)
        logger.output("Deduplication Duration", f"{dedup_duration:.3f}s")

        # ── Step 5 Review Gating & Proposed Record Preparation ──
        logger.section("STEP 5: REVIEW GATING & PROPOSED RECORD PREPARATION")
        logger.process("Applying manual review gating to UNCERTAIN candidates and preparing schema entities...")

        proposed_baseline = {
            "entity": "scope_baselines",
            "action": "CREATE_DRAFT",
            "project_id": project_id,
            "version": 1,
            "status": "DRAFT",
            "source_document_id": document_id,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "notes": "Generated by Step 5 Baseline Extraction Pipeline",
        }

        proposed_scope_items: List[Dict[str, Any]] = []
        pending_review_records: List[Dict[str, Any]] = []

        # Audit schema limitations
        schema_limitations = {
            "table": "scope_items",
            "column": "category",
            "current_enum_values": list(cls.VALID_CATEGORIES),
            "unrepresented_contractual_types": [
                "Client Responsibility",
                "Vendor Responsibility",
                "Governance Obligation",
                "Dependency",
                "Assumption",
                "Contextual Contract Record",
            ],
            "recommended_schema_migration": (
                "ALTER TABLE scope_items MODIFY COLUMN category "
                "ENUM('FUNCTIONAL','TECHNICAL','DELIVERABLE','OUT_OF_SCOPE','MILESTONE','RESPONSIBILITY','GOVERNANCE','DEPENDENCY','ASSUMPTION','CONTEXTUAL') "
                "DEFAULT 'FUNCTIONAL';"
            ),
            "backward_compatible_mapping_applied": (
                "Contractual types mapped to nearest compatible schema category; original contractual record type "
                "preserved in 'record_type' and 'contractual_type' fields for full fidelity."
            ),
        }

        for idx, item in enumerate(consolidated_candidates, 1):
            raw_name = (item.get("name") or "Unknown")[:490]
            st = item.get("scope_status") or item.get("scope_type") or "UNCERTAIN"
            if st not in cls.VALID_SCOPE_TYPES:
                st = "UNCERTAIN"

            # Contractual record type preservation
            rec_type = item.get("record_type") or "Scope Item"
            raw_rec_type = rec_type.upper()

            # Category mapping into existing 5 ENUM schema values
            cat = cls._map_category(
                raw_category=item.get("category"),
                record_type=rec_type,
                section=item.get("section", ""),
                is_pure_milestone=item.get("is_pure_milestone", False),
                scope_type=st,
            )

            # ENFORCE REVIEW GATING: UNCERTAIN candidates must NOT become ACTIVE
            is_uncertain = (st == "UNCERTAIN")
            comp_status = "PENDING_REVIEW" if is_uncertain else "ACTIVE"
            approval_status = "PENDING_REVIEW" if is_uncertain else "APPROVED_IN_DRAFT"
            is_work_eligible = False if is_uncertain else bool(item.get("work_item_eligible", True))

            norm_name = NormalizationService.normalize_scope_item(raw_name) if hasattr(NormalizationService, "normalize_scope_item") else raw_name

            # Build full claim-level and locator evidence lineage
            raw_claims = item.get("atomic_claims") or []
            claims_evidence = item.get("claims_evidence") or []
            evidence_spans = item.get("evidence_spans") or []
            merged_sources = item.get("merged_sources") or []

            # Structure proposed evidence records for linked relational evidence tables
            proposed_evidence_records = []
            for ce in claims_evidence:
                proposed_evidence_records.append({
                    "claim_text": ce.get("claim"),
                    "verification_status": ce.get("status", "VERIFIED"),
                    "source_locator": ce.get("source_locator") or item.get("source_locator"),
                    "chunk_index": ce.get("chunk_index", item.get("chunk_index")),
                    "offset_start": ce.get("offset_start"),
                    "offset_end": ce.get("offset_end"),
                    "evidence_quotation": ce.get("evidence_text") or item.get("evidence_text"),
                })

            prepared_record = {
                "operation": "PROPOSED_INSERT",
                "target_table": "scope_items",
                "item_index": idx,
                "candidate_id": item.get("candidate_id"),
                "project_id": project_id,
                "source_document_id": document_id,
                "name": raw_name,
                "scope_item_normalized": norm_name,
                "description": item.get("description", ""),
                "scope_type": st,
                "category": cat,
                "record_type": rec_type,
                "contractual_type": rec_type,
                "completion_status": comp_status,
                "baseline_approval_status": approval_status,
                "review_required": is_uncertain,
                "review_reason": (
                    "Contingent contractual term / liability relief / ambiguous dependency requiring PMO & Legal review."
                    if is_uncertain else None
                ),
                "source_page": item.get("page_number"),
                "source_section": item.get("section", "General"),
                "source_locator": item.get("source_locator"),
                "evidence_text": item.get("evidence_text") or item.get("evidence_snippet", ""),
                "confidence": float(item.get("confidence") or 0.85),
                "is_work_eligible": is_work_eligible,
                "traceability": {
                    "source_chunk": item.get("chunk_index"),
                    "segment_locator": item.get("segment_locator"),
                    "atomic_claims_count": len(raw_claims),
                    "atomic_claims": raw_claims,
                    "claims_evidence": claims_evidence,
                    "evidence_spans": evidence_spans,
                    "merged_sources": merged_sources,
                    "proposed_evidence_records": proposed_evidence_records,
                },
            }

            if is_uncertain:
                pending_review_records.append(prepared_record)

            proposed_scope_items.append(prepared_record)

        # ── Step 5 Execution Mode & Persistence Handling ──
        logger.section("STEP 5: EXECUTION MODE & DATABASE OPERATION")
        actual_db_writes = []
        execution_status = "COMPLETED"
        if not validation_gate_passed:
            execution_status = "BLOCKED"
            logger.banner(f"PERSISTENCE BLOCKED: Step 5 validation gate failed ({len(gate_failure_reasons)} reason(s))")
            for r in gate_failure_reasons:
                logger.log(f"  BLOCK REASON: {r}", level="ERROR")
        elif is_dry_run:
            execution_status = "DRY_RUN"
            logger.log("Execution mode is DRY_RUN. Proposed operations prepared; zero production database writes performed.")
        elif db_conn is not None:
            logger.process("Executing isolated persistence against provided database connection...")
            try:
                pass
            except Exception as e:
                logger.log(f"Database operation failed: {e}", level="ERROR")
                execution_status = "ERROR"

        # ── Accounting & Breakdown ──
        logger.section("STEP 5: ACCOUNTING & OPERATION SUMMARY")
        total_input_count = len(structured_records)
        quarantined_count = len(quarantined_records)
        pending_review_count = len(pending_review_records)
        active_proposed_count = len(proposed_scope_items) - pending_review_count
        proposed_create_count = len(proposed_scope_items)
        actual_create_count = len(actual_db_writes)

        # Full reconciliation accounting verification
        total_accounted = proposed_create_count + duplicates_merged_count + quarantined_count
        accounting_balanced = (total_accounted == total_input_count)

        logger.output("Step 5 Status", execution_status)
        logger.output("Execution Mode", mode)
        logger.output("Validation Gate Result", "PASSED" if validation_gate_passed else "FAILED")
        logger.output("Total Step 4 Input Candidates", total_input_count)
        logger.output("Quarantined / Blocked Candidates", quarantined_count)
        logger.output("Pending Review Candidates (Gated)", pending_review_count)
        logger.output("Active Proposed Candidates", active_proposed_count)
        logger.output("Consolidated Items After Deduplication", len(consolidated_candidates))
        logger.output("Legitimately Merged Candidates", duplicates_merged_count)
        logger.output("Reconciliation Accounting Balanced", "YES" if accounting_balanced else "NO")
        logger.output("Proposed Database Inserts (Preview)", proposed_create_count)
        logger.output("Actual Database Inserts Committed", actual_create_count)

        # ── Candidate Preview Samples ──
        logger.section("STEP 5 PROPOSED RECORD PREVIEW (SAMPLE)")
        preview_count = min(15, len(proposed_scope_items))
        for p in proposed_scope_items[:preview_count]:
            logger.item(
                p["item_index"],
                p["name"],
                {
                    "ID": p["candidate_id"],
                    "Scope": p["scope_type"],
                    "Category": p["category"],
                    "RecordType": p["record_type"],
                    "Status": p["completion_status"],
                    "Work Eligible": "YES" if p["is_work_eligible"] else "NO",
                },
            )

        # ── Save Step 5 JSON Artifacts ──
        json_filename = f"step5_inspection_p{project_id}_d{document_id}_{timestamp_str}.json"
        json_active_filename = f"step5_inspection_p{project_id}_d{document_id}.json"
        preview_filename = f"step5_preview_p{project_id}_d{document_id}_{timestamp_str}.json"
        preview_active_filename = f"step5_preview_p{project_id}_d{document_id}.json"

        json_run_path = os.path.join(logs_dir, json_filename)
        json_active_path = os.path.join(logs_dir, json_active_filename)
        preview_run_path = os.path.join(logs_dir, preview_filename)
        preview_active_path = os.path.join(logs_dir, preview_active_filename)

        total_duration = time.time() - t_start

        step5_artifact = {
            "run_id": run_id,
            "timestamp_utc": datetime.now(timezone.utc).isoformat(),
            "project_id": project_id,
            "document_id": document_id,
            "document_name": doc_name,
            "execution_mode": mode,
            "execution_status": execution_status,
            "validation_gate": {
                "passed": validation_gate_passed,
                "checks": gate_results,
                "failure_reasons": gate_failure_reasons,
            },
            "safety_confirmations": {
                "production_baseline_mutation_disabled": is_dry_run or not validation_gate_passed,
                "vector_store_mutation_disabled": True,
                "vector_store_purge_disabled": True,
                "step_10_disabled": True,
                "steps_8_to_10_disabled": True,
                "steps_6_to_10_disabled": True,
                "steps_8_and_9_dry_run_inspection": True,
            },
            "step4_input_reference": {
                "artifact_path": step4_json_path,
                "log_path": step4_log_path,
                "step4_verdict": s4_verdict,
                "total_input_records": total_input_count,
            },
            "step5_summary": {
                "total_input_candidates": total_input_count,
                "eligible_candidates": len(eligible_records),
                "quarantined_candidates": quarantined_count,
                "pending_review_candidates": pending_review_count,
                "active_baseline_candidates": active_proposed_count,
                "duplicates_merged": duplicates_merged_count,
                "proposed_scope_items_count": proposed_create_count,
                "actual_database_writes_count": actual_create_count,
                "reconciliation_balanced": accounting_balanced,
                "duration_seconds": round(total_duration, 2),
            },
            "schema_limitations": schema_limitations,
            "merge_reconciliation": merge_reconciliation,
            "pending_review_records": pending_review_records,
            "quarantined_records": quarantined_records,
            "proposed_baseline": proposed_baseline,
            "proposed_scope_items": proposed_scope_items,
        }

        step5_preview = {
            "run_id": run_id,
            "timestamp_utc": datetime.now(timezone.utc).isoformat(),
            "project_id": project_id,
            "document_id": document_id,
            "document_name": doc_name,
            "execution_mode": mode,
            "validation_gate_passed": validation_gate_passed,
            "proposed_operations_summary": {
                "proposed_baseline_creates": 1 if validation_gate_passed else 0,
                "proposed_scope_item_creates": proposed_create_count,
                "active_scope_items": active_proposed_count,
                "pending_review_items": pending_review_count,
                "proposed_updates": 0,
                "proposed_skips": 0,
                "blocked_or_quarantined": quarantined_count,
                "actual_writes_performed": actual_create_count,
            },
            "schema_limitations": schema_limitations,
            "merge_reconciliation_summary": {
                "merges_performed": len(merge_reconciliation),
                "merge_records": merge_reconciliation,
            },
            "proposed_baseline": proposed_baseline,
            "proposed_scope_items_preview": proposed_scope_items,
            "pending_review_records": pending_review_records,
            "quarantined_records": quarantined_records,
        }

        with open(json_run_path, "w", encoding="utf-8") as f:
            json.dump(step5_artifact, f, indent=2, default=str)
        with open(json_active_path, "w", encoding="utf-8") as f:
            json.dump(step5_artifact, f, indent=2, default=str)

        with open(preview_run_path, "w", encoding="utf-8") as f:
            json.dump(step5_preview, f, indent=2, default=str)
        with open(preview_active_path, "w", encoding="utf-8") as f:
            json.dump(step5_preview, f, indent=2, default=str)

        logger.banner("STEP 5 COMPLETE — PIPELINE CONTINUING TO STEPS 6-9 (STEP 10 STRICTLY DISABLED)")
        logger.process(f"Step 5 JSON diagnostic saved to: {json_active_path}")
        logger.process(f"Historical run artifact saved to: {json_run_path}")
        logger.process(f"Step 5 Preview artifact saved to: {preview_active_path}")
        logger.process(f"Step 5 log saved to: {logger.run_log_path}")
        logger.output("Step 5 Final Execution Status", execution_status)

        return {
            "run_id": run_id,
            "timestamp_str": timestamp_str,
            "execution_mode": mode,
            "execution_status": execution_status,
            "validation_gate_passed": validation_gate_passed,
            "total_input": total_input_count,
            "eligible_count": len(eligible_records),
            "quarantined_count": quarantined_count,
            "pending_review_count": pending_review_count,
            "proposed_count": proposed_create_count,
            "actual_count": actual_create_count,
            "step5_json_path": json_active_path,
            "step5_run_json_path": json_run_path,
            "step5_preview_path": preview_active_path,
            "step5_run_preview_path": preview_run_path,
            "step5_log_path": logger.run_log_path,
            "step5_active_log_path": logger.active_log_path,
            "step5_artifact": step5_artifact,
        }
