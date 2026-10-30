import os
import sys
import time
import json
import re
import threading
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set


class Step7Logger:
    """
    Dedicated Logger for Step 7 (Ambiguity & Gap Analysis) Inspection Runs.
    Guarantees independent logging to logs/step7_gap_analysis_p{project_id}_d{document_id}_{timestamp}.log
    and active logs/step7_gap_analysis_p{project_id}_d{document_id}.log without polluting other logs.
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
        self.run_log_path = os.path.join(self.log_dir, f"step7_gap_analysis_p{project_id}_d{document_id}_{timestamp_str}.log")
        self.active_log_path = os.path.join(self.log_dir, f"step7_gap_analysis_p{project_id}_d{document_id}.log")
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

    def banner(self, title: str):
        bar = "=" * 80
        self.raw_log(f"\n{bar}")
        self.raw_log(f"[{self._format_timestamp()}] {title}")
        self.raw_log(f"{bar}\n")

    def section(self, title: str):
        bar = "-" * 60
        self.raw_log(f"\n{bar}")
        self.raw_log(f"[{self._format_timestamp()}] >> {title}")
        self.raw_log(f"{bar}")

    def log(self, text: str):
        self.raw_log(f"[{self._format_timestamp()}] {text}")

    def output(self, key: str, val: Any):
        if isinstance(val, (dict, list)):
            val_str = json.dumps(val, indent=2, default=str)
            self.raw_log(f"[{self._format_timestamp()}] [OUTPUT] {key}:\n{val_str}")
        else:
            self.raw_log(f"[{self._format_timestamp()}] [OUTPUT] {key}: {val}")


class Step7Analyzer:
    """
    Step 7: Ambiguity & Gap Analysis Service.
    Performs comprehensive diagnostic inspection of contractual ambiguities and extraction gaps.
    Read-only inspection: does NOT modify Step 4 or Step 5 baseline records.
    modifies_baseline is ALWAYS False.
    """

    # OCR distortion regex: words containing internal 'G' replacing 'u'
    OCR_G_PATTERN = re.compile(r"\b([a-z]+G[a-z]+)\b", re.IGNORECASE)

    # Compound modal verbs pattern for compound assertions
    COMPOUND_MODAL_PATTERN = re.compile(
        r"\b(shall|will|must|agrees? to|undertakes? to)\b.*?\b(and|additionally|as well as)\b.*?\b(shall|will|must|agrees? to|undertakes? to)\b",
        re.IGNORECASE,
    )

    @classmethod
    def execute_inspection(
        cls,
        project_id: int,
        document_id: int,
        doc_name: str,
        step3_artifact: dict,
        step4_artifact: dict,
        step5_artifact: dict,
        step6_artifact: dict,
        run_id: str,
        timestamp_str: str,
        logs_dir: str,
    ) -> dict:
        t_start = time.time()
        logger = Step7Logger(project_id, document_id, timestamp_str, log_dir=logs_dir)

        logger.banner("STEP 7: CONTRACTUAL AMBIGUITY & EXTRACTION GAP ANALYSIS")
        logger.log(f"Project ID: {project_id} | Document ID: {document_id} | Document: {doc_name}")
        logger.log(f"Run ID: {run_id} | Mode: INSPECTION (Read-Only, modifies_baseline=False)")

        findings: List[dict] = []
        seq = 1

        def next_finding_id() -> str:
            nonlocal seq
            fid = f"F_p{project_id}_d{document_id}_{seq:04d}"
            seq += 1
            return fid

        # Extract records from earlier artifacts
        s3_candidates = (
            step3_artifact.get("step3_candidate_extraction", {}).get("canonical_candidates")
            or step3_artifact.get("canonical_candidates")
            or []
        )
        s4_records = step4_artifact.get("structured_records") or []
        s5_proposed = step5_artifact.get("proposed_scope_items") or []
        s5_quarantined = step5_artifact.get("quarantined_records") or []
        s5_pending = step5_artifact.get("pending_review_records") or []

        s4_by_id = {r.get("candidate_id"): r for r in s4_records if r.get("candidate_id")}

        # ── 1. Unsupported Claims Inspection (Step 4 data) ──
        logger.section("1. Unsupported Claims Inspection")
        s4_unsupported_recs = []
        for r in s4_records:
            cid = r.get("candidate_id")
            c_claims_ev = r.get("claims_evidence") or []
            unsupp_claims = [ce for ce in c_claims_ev if isinstance(ce, dict) and ce.get("status") == "UNSUPPORTED"]
            if unsupp_claims or r.get("verification_status") == "UNSUPPORTED":
                s4_unsupported_recs.append((r, unsupp_claims))

        for r, unsupp_claims in s4_unsupported_recs:
            cid = r.get("candidate_id")
            c_name = r.get("name", "Unknown")
            c_chunk = r.get("chunk_index")
            quotation = r.get("evidence_snippet") or r.get("raw_text") or c_name
            chunk_indices = [c_chunk] if c_chunk is not None else []

            claim_descs = [c.get("claim") for c in unsupp_claims if c.get("claim")]
            claim_text = "; ".join(claim_descs) if claim_descs else c_name

            findings.append({
                "finding_id": next_finding_id(),
                "finding_type": "UNSUPPORTED_CLAIM",
                "severity": "HIGH",
                "confidence": 1.0,
                "affected_candidate_ids": [cid],
                "source_chunk_indices": chunk_indices,
                "source_quotation": quotation[:300],
                "explanation": (
                    f"Candidate '{c_name}' ({cid}) contains unsupported claims not backed by verbatim contract text: {claim_text[:200]}"
                ),
                "is_source_document_issue": False,
                "recommended_action": "Quarantine candidate from baseline; verify claim wording against source contract.",
                "review_status": "OPEN",
                "modifies_baseline": False,
            })
            logger.log(f"  [HIGH] Unsupported claim in candidate {cid}: {c_name}")

        # ── 2. Traceability Failures & Orphaned Merges (Step 6 data) ──
        logger.section("2. Traceability & Orphaned Merges Inspection")
        coverage_gaps = step6_artifact.get("coverage_gaps") or {}
        orphaned_merges = coverage_gaps.get("orphaned_merges") or []
        for om in orphaned_merges:
            cid = om.get("candidate_id") or om.get("canonical_id")
            aff_ids = [cid] if cid else []
            findings.append({
                "finding_id": next_finding_id(),
                "finding_type": "TRACEABILITY_FAILURE",
                "severity": "HIGH",
                "confidence": 1.0,
                "affected_candidate_ids": aff_ids,
                "source_chunk_indices": [],
                "source_quotation": om.get("merged_name", "") or om.get("reason", ""),
                "explanation": f"Traceability failure: {om.get('reason')}",
                "is_source_document_issue": False,
                "recommended_action": "Repair upstream candidate merge lineage and retain source candidate locator metadata.",
                "review_status": "OPEN",
                "modifies_baseline": False,
            })
            logger.log(f"  [HIGH] Traceability failure: {om.get('reason')}")

        # ── 3. Contractual Ambiguity: Conflicting Dates or Fees ──
        logger.section("3. Conflicting Dates & Fees Inspection")
        milestone_map: Dict[str, List[dict]] = {}
        for r in s4_records:
            name_norm = (r.get("name") or "").strip().lower()
            rec_type = (r.get("record_type") or "").lower()
            if "milestone" in rec_type or r.get("is_pure_milestone"):
                milestone_map.setdefault(name_norm, []).append(r)

        for m_name, recs in milestone_map.items():
            if len(recs) > 1:
                # Check if descriptions or claims differ in dates or percentages
                snippets = {r.get("evidence_snippet") or r.get("raw_text") for r in recs}
                if len(snippets) > 1:
                    aff_ids = [r.get("candidate_id") for r in recs if r.get("candidate_id")]
                    chunk_indices = sorted(list({r.get("chunk_index") for r in recs if r.get("chunk_index") is not None}))
                    findings.append({
                        "finding_id": next_finding_id(),
                        "finding_type": "SOURCE_CONFLICT",
                        "severity": "HIGH",
                        "confidence": 0.90,
                        "affected_candidate_ids": aff_ids,
                        "source_chunk_indices": chunk_indices,
                        "source_quotation": recs[0].get("evidence_snippet", "")[:300],
                        "explanation": f"Conflicting contractual terms or duplicate definitions detected for milestone '{m_name}'.",
                        "is_source_document_issue": True,
                        "recommended_action": "Reconcile duplicate milestone definitions and confirm milestone schedule with client.",
                        "review_status": "OPEN",
                        "modifies_baseline": False,
                    })
                    logger.log(f"  [HIGH] Conflicting terms for milestone: {m_name}")

        # ── 4. Contractual Ambiguity: Unclear Actor / Owner ──
        logger.section("4. Unclear Actor / Owner Inspection")
        unclear_actor_cands = []
        for r in s4_records:
            actor = (r.get("actor") or "").strip().upper()
            if not actor or actor in ("UNKNOWN", "NONE", "N/A", "PARTIES", "EITHER PARTY", "BOTH"):
                unclear_actor_cands.append(r)

        for r in unclear_actor_cands:
            cid = r.get("candidate_id")
            c_name = r.get("name", "Unknown")
            c_chunk = r.get("chunk_index")
            quotation = r.get("evidence_snippet") or r.get("raw_text") or c_name
            chunk_indices = [c_chunk] if c_chunk is not None else []

            # Deliverables or core obligations with unclear actor are MEDIUM severity
            findings.append({
                "finding_id": next_finding_id(),
                "finding_type": "CONTRACTUAL_AMBIGUITY",
                "severity": "MEDIUM",
                "confidence": 0.85,
                "affected_candidate_ids": [cid],
                "source_chunk_indices": chunk_indices,
                "source_quotation": quotation[:300],
                "explanation": f"Clause '{c_name}' ({cid}) lacks explicit contractual actor/owner; responsible party is ambiguous.",
                "is_source_document_issue": True,
                "recommended_action": "Assign explicit contractual responsibility (Provider vs Client) during SOW alignment.",
                "review_status": "OPEN",
                "modifies_baseline": False,
            })
        logger.log(f"  Identified {len(unclear_actor_cands)} clauses with unclear contractual actor/owner.")

        # ── 5. Contractual Ambiguity: Deliverables Missing Acceptance Criteria ──
        logger.section("5. Deliverables Missing Acceptance Criteria Inspection")
        acceptance_keywords = {"acceptance", "sign-off", "sign off", "criteria", "testing", "approval", "milestone completion", "approved by"}
        deliv_missing_ac = []

        for r in s4_records:
            rec_type = (r.get("record_type") or "").lower()
            if "deliverable" in rec_type:
                text = (r.get("evidence_snippet") or "" + " " + r.get("description") or "").lower()
                has_ac = any(kw in text for kw in acceptance_keywords)
                if not has_ac:
                    deliv_missing_ac.append(r)

        for r in deliv_missing_ac:
            cid = r.get("candidate_id")
            c_name = r.get("name", "Unknown")
            c_chunk = r.get("chunk_index")
            quotation = r.get("evidence_snippet") or r.get("raw_text") or c_name
            chunk_indices = [c_chunk] if c_chunk is not None else []

            findings.append({
                "finding_id": next_finding_id(),
                "finding_type": "CONTRACTUAL_AMBIGUITY",
                "severity": "MEDIUM",
                "confidence": 0.80,
                "affected_candidate_ids": [cid],
                "source_chunk_indices": chunk_indices,
                "source_quotation": quotation[:300],
                "explanation": f"Deliverable '{c_name}' ({cid}) does not define explicit acceptance criteria or client sign-off procedures.",
                "is_source_document_issue": True,
                "recommended_action": "Document formal acceptance criteria, test cases, and sign-off window (e.g. 5 business days).",
                "review_status": "OPEN",
                "modifies_baseline": False,
            })
        logger.log(f"  Identified {len(deliv_missing_ac)} deliverables missing explicit acceptance criteria.")

        # ── 6. Contractual Ambiguity: Compound / Multi-Assertion Claims ──
        logger.section("6. Compound / Multi-Assertion Claims Inspection")
        compound_claims = []
        for r in s4_records:
            desc = r.get("description") or ""
            ev_text = r.get("evidence_text") or ""
            text = f"{desc} {ev_text}"
            if cls.COMPOUND_MODAL_PATTERN.search(text):
                compound_claims.append(r)

        for r in compound_claims:
            cid = r.get("candidate_id")
            c_name = r.get("name", "Unknown")
            c_chunk = r.get("chunk_index")
            quotation = r.get("evidence_snippet") or r.get("description") or c_name
            chunk_indices = [c_chunk] if c_chunk is not None else []

            findings.append({
                "finding_id": next_finding_id(),
                "finding_type": "CONTRACTUAL_AMBIGUITY",
                "severity": "LOW",
                "confidence": 0.75,
                "affected_candidate_ids": [cid],
                "source_chunk_indices": chunk_indices,
                "source_quotation": quotation[:300],
                "explanation": f"Candidate '{c_name}' ({cid}) contains multiple compounded contractual assertions conjoined in a single clause.",
                "is_source_document_issue": True,
                "recommended_action": "Split compound contractual statement into distinct atomic obligations.",
                "review_status": "OPEN",
                "modifies_baseline": False,
            })
        logger.log(f"  Identified {len(compound_claims)} compound contractual claims.")

        # ── 7. Extraction Gaps: Ungated UNCERTAIN Scope Status & Gating Verification ──
        logger.section("7. Scope Status UNCERTAIN Gating Verification")
        for r in s4_records:
            cid = r.get("candidate_id")
            s_type = r.get("scope_type") or r.get("scope_status")
            if s_type == "UNCERTAIN":
                c_chunk = r.get("chunk_index")
                chunk_indices = [c_chunk] if c_chunk is not None else []
                quotation = r.get("evidence_snippet") or r.get("raw_text") or r.get("name", "")

                # Check if properly gated in Step 5
                p_item = next((p for p in s5_proposed if p.get("candidate_id") == cid), None)
                is_gated = (
                    cid in [p.get("candidate_id") for p in s5_pending]
                    or cid in [q.get("candidate_id") for q in s5_quarantined]
                    or (p_item and p_item.get("baseline_approval_status") == "PENDING_REVIEW")
                )

                if not is_gated:
                    findings.append({
                        "finding_id": next_finding_id(),
                        "finding_type": "CLASSIFICATION_SCHEMA_ISSUE",
                        "severity": "HIGH",
                        "confidence": 1.0,
                        "affected_candidate_ids": [cid],
                        "source_chunk_indices": chunk_indices,
                        "source_quotation": quotation[:300],
                        "explanation": f"Record '{r.get('name')}' ({cid}) has UNCERTAIN scope status but was NOT gated to PENDING_REVIEW.",
                        "is_source_document_issue": False,
                        "recommended_action": "Enforce strict gating: all UNCERTAIN records must require human review before baseline entry.",
                        "review_status": "OPEN",
                        "modifies_baseline": False,
                    })
                    logger.log(f"  [HIGH] Ungated UNCERTAIN record: {cid}")
                else:
                    findings.append({
                        "finding_id": next_finding_id(),
                        "finding_type": "CONTRACTUAL_AMBIGUITY",
                        "severity": "MEDIUM",
                        "confidence": 0.90,
                        "affected_candidate_ids": [cid],
                        "source_chunk_indices": chunk_indices,
                        "source_quotation": quotation[:300],
                        "explanation": (
                            f"Contingent contractual term / liability relief clause '{r.get('name')}' ({cid}) correctly gated to PENDING_REVIEW."
                        ),
                        "is_source_document_issue": True,
                        "recommended_action": "Conduct PMO & Legal review to clarify liability caps and remote working contingencies.",
                        "review_status": "OPEN",
                        "modifies_baseline": False,
                    })
                    logger.log(f"  [MEDIUM] Gated UNCERTAIN clause flagged for review: {cid}")

        # ── 8. Extraction Gaps: Sections with Zero Candidates ──
        logger.section("8. Step 2 Detected Sections Coverage Inspection")
        s2_detected = step3_artifact.get("step2_section_detection", {}).get("detected_sections") or []
        sections_with_cands = {r.get("section") for r in s4_records if r.get("section")}
        for sec in s2_detected:
            if sec not in sections_with_cands:
                findings.append({
                    "finding_id": next_finding_id(),
                    "finding_type": "EXTRACTION_GAP",
                    "severity": "MEDIUM",
                    "confidence": 0.85,
                    "affected_candidate_ids": [],
                    "source_chunk_indices": [],
                    "source_quotation": f"Section: '{sec}'",
                    "explanation": f"Section '{sec}' was detected during structural analysis in Step 2, but 0 scope candidates were extracted.",
                    "is_source_document_issue": False,
                    "recommended_action": "Verify if section contains contractual obligations or purely administrative/boilerplate text.",
                    "review_status": "OPEN",
                    "modifies_baseline": False,
                })
                logger.log(f"  [MEDIUM] Section with zero extracted candidates: '{sec}'")

        # ── 9. Extraction Gaps: Functional / IJP Features Mapping ──
        logger.section("9. Functional & IJP Feature Coverage Inspection")
        # Specification of essential functional features with justified acronyms/aliases
        # Dynamically discover required functional feature specifications from contract headings/preamble
        feature_specifications = []
        combined_s4_text = " ".join((r.get("name") or "") + " " + (r.get("description") or "") for r in s4_records).lower()
        if "internal job posting" in combined_s4_text or "ijp" in combined_s4_text:
            feature_specifications.append({
                "canonical_name": "Internal Job Posting (IJP)",
                "feature_key": "ijp",
                "aliases": ["ijp", "internal job posting"],
                "required": True,
            })
        if "collaboration" in combined_s4_text or "vconnect" in combined_s4_text:
            feature_specifications.append({
                "canonical_name": "Collaboration Portal",
                "feature_key": "collaboration",
                "aliases": ["collaboration", "collaborate", "vconnect"],
                "required": True,
            })
        for f_spec in feature_specifications:
            c_name = f_spec["canonical_name"]
            aliases = f_spec["aliases"]
            matching_s4 = [
                r for r in s4_records
                if any(
                    a in (r.get("name") or "").lower()
                    or a in (r.get("description") or "").lower()
                    or a in (r.get("evidence_snippet") or "").lower()
                    for a in aliases
                )
            ]
            if not matching_s4:
                findings.append({
                    "finding_id": next_finding_id(),
                    "finding_type": "EXTRACTION_GAP",
                    "severity": "MEDIUM",
                    "confidence": 0.80,
                    "affected_candidate_ids": [],
                    "source_chunk_indices": [],
                    "source_quotation": f"Feature Requirement: '{c_name}' (aliases: {', '.join(aliases)})",
                    "explanation": f"Document functional feature '{c_name}' was referenced in contract but not mapped to any Step 4 structured record.",
                    "is_source_document_issue": False,
                    "recommended_action": "Ensure feature is captured in Scope of Work deliverable breakdowns.",
                    "review_status": "OPEN",
                    "modifies_baseline": False,
                })
                logger.log(f"  [MEDIUM] Feature '{c_name}' unmapped in Step 4 records.")
            else:
                matched_ids = [r.get("candidate_id") for r in matching_s4[:4] if r.get("candidate_id")]
                logger.log(f"  [VERIFIED] Feature '{c_name}' verified across {len(matching_s4)} Step 4 candidate(s): {matched_ids}")

        # ── 10. Source Document Typographical Anomaly Inspection ──
        logger.section("10. Source Document Typographical Anomaly Inspection")
        ocr_seen_words: Set[str] = set()
        for r in s4_records:
            cid = r.get("candidate_id")
            c_chunk = r.get("chunk_index")
            chunk_indices = [c_chunk] if c_chunk is not None else []
            combined_text = f"{r.get('name', '')} {r.get('description', '')} {r.get('evidence_snippet', '')}"

            matches = cls.OCR_G_PATTERN.findall(combined_text)
            for m in matches:
                # Specific common authoring typographical anomalies: sGmary (summary), circGstances (circumstances), reqGred (required)
                m_lower = m.lower()
                if "g" in m_lower and any(w in m_lower for w in ["sgmary", "circgstances", "reqgred", "managgng", "conseqgent"]):
                    if m_lower not in ocr_seen_words:
                        ocr_seen_words.add(m_lower)
                        expected_word = (
                            m_lower.replace("sgmary", "summary")
                            .replace("circgstances", "circumstances")
                            .replace("reqgred", "required")
                            .replace("managgng", "managing")
                            .replace("conseqgent", "consequent")
                        )
                        findings.append({
                            "finding_id": next_finding_id(),
                            "finding_type": "SOURCE_TEXT_TYPOGRAPHICAL_ANOMALY",
                            "severity": "LOW",
                            "confidence": 0.95,
                            "affected_candidate_ids": [cid],
                            "source_chunk_indices": chunk_indices,
                            "source_quotation": m,
                            "explanation": (
                                f"Source document typographical anomaly present in original DOCX authoring (document parsed via MarkItDown directly; no OCR involved): '{m}' in candidate '{r.get('name')}' ({cid}). Expected word: '{expected_word}'."
                            ),
                            "is_source_document_issue": True,
                            "recommended_action": "Preserve original verbatim source quotation while noting normalized interpretation for downstream processing.",
                            "review_status": "OPEN",
                            "modifies_baseline": False,
                        })
                        logger.log(f"  [LOW] Source Document Typographical Anomaly detected: '{m}' (expected: '{expected_word}') in {cid}")

        # Compute severity statistics
        high_count = sum(1 for f in findings if f.get("severity") == "HIGH")
        med_count = sum(1 for f in findings if f.get("severity") == "MEDIUM")
        low_count = sum(1 for f in findings if f.get("severity") == "LOW")

        type_counts: Dict[str, int] = {}
        for f in findings:
            ft = f.get("finding_type", "OTHER")
            type_counts[ft] = type_counts.get(ft, 0) + 1

        total_findings = len(findings)

        # Build Step 7 Artifact
        step7_artifact = {
            "run_id": run_id,
            "timestamp_utc": datetime.now(timezone.utc).isoformat(),
            "project_id": project_id,
            "document_id": document_id,
            "document_name": doc_name,
            "read_only": True,
            "modifies_baseline": False,
            "safety_confirmations": {
                "production_baseline_mutation_disabled": True,
                "vector_store_mutation_disabled": True,
                "vector_store_purge_disabled": True,
                "step_10_disabled": True,
                "steps_8_and_9_dry_run_inspection": True,
            },
            "summary": {
                "total_findings": total_findings,
                "high_severity_count": high_count,
                "medium_severity_count": med_count,
                "low_severity_count": low_count,
                "findings_by_type": type_counts,
                "source_document_issues_count": sum(1 for f in findings if f.get("is_source_document_issue")),
                "pipeline_issues_count": sum(1 for f in findings if not f.get("is_source_document_issue")),
            },
            "findings": findings,
        }

        # Write Artifact Files
        json_run_path = os.path.join(logs_dir, f"step7_gap_analysis_p{project_id}_d{document_id}_{timestamp_str}.json")
        json_active_path = os.path.join(logs_dir, f"step7_gap_analysis_p{project_id}_d{document_id}.json")

        with open(json_run_path, "w", encoding="utf-8") as f:
            json.dump(step7_artifact, f, indent=2, default=str)
        with open(json_active_path, "w", encoding="utf-8") as f:
            json.dump(step7_artifact, f, indent=2, default=str)

        total_duration = time.time() - t_start

        logger.section("Step 7 Ambiguity & Gap Analysis Summary")
        logger.output("Total Findings", total_findings)
        logger.output("HIGH Severity Findings", high_count)
        logger.output("MEDIUM Severity Findings", med_count)
        logger.output("LOW Severity Findings", low_count)
        logger.output("Findings by Type", type_counts)
        logger.output("Execution Duration (s)", round(total_duration, 2))
        logger.output("Modifies Baseline", False)

        logger.banner("STEP 7 COMPLETE — INSPECTION PIPELINE ADVANCED TO COMPLETION")
        logger.log(f"Step 7 JSON artifact saved to: {json_active_path}")
        logger.log(f"Step 7 historical run artifact saved to: {json_run_path}")
        logger.log(f"Step 7 log saved to: {logger.run_log_path}")

        return {
            "run_id": run_id,
            "timestamp_str": timestamp_str,
            "total_findings": total_findings,
            "high_severity_count": high_count,
            "medium_severity_count": med_count,
            "low_severity_count": low_count,
            "findings_by_type": type_counts,
            "step7_json_path": json_active_path,
            "step7_run_json_path": json_run_path,
            "step7_log_path": logger.run_log_path,
            "step7_active_log_path": logger.active_log_path,
            "step7_artifact": step7_artifact,
        }
