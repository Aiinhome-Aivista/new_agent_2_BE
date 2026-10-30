import os
import sys
import json
import hashlib
from datetime import datetime, timezone, date, timedelta
from typing import Any, Dict, List, Optional, Tuple


class RiskLifecycleService:
    """
    Risk Assessment Lifecycle Service across Project Lifecycle:
    1. EL Upload -> INITIAL_CONTRACTUAL_ASSESSMENT
       - Purely contractual risks, missing deadlines, customer dependencies,
         ambiguous commitments, missing acceptance criteria, unsupported claims.
       - Does NOT speculate on actual project execution risk before reports exist.
    2. First MoM / Status Report -> FIRST_OPERATIONAL_ASSESSMENT
       - Combines baseline + initial contractual assessment + reported facts/blockers/delays.
       - Clearly distinguishes reported facts, inferred risks, and missing information.
    3. Subsequent MoM / Status Reports -> RECALCULATED_OPERATIONAL_ASSESSMENT
       - Re-evaluates risk register with stable risk identifiers.
       - Tracks state transitions: NEW, CONTINUING, INCREASING, DECREASING,
         RESOLVED, REOPENED, AWAITING_CONFIRMATION.
       - Preserves versioned historical snapshots without silent overwriting.
       - A risk is NEVER resolved merely due to omission in the latest report.
    4. Project Completion -> PROJECT_COMPLETION_SUMMARY
       - Final risk summary closing only supported items, preserving historical audit snapshots.
    """

    @classmethod
    def get_lifecycle_dir(cls, project_id: int, logs_dir: Optional[str] = None) -> str:
        if logs_dir is None:
            backend_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            lifecycle_dir = os.path.join(backend_root, "data", "projects", str(project_id), "risk_lifecycle")
        else:
            lifecycle_dir = os.path.join(logs_dir, f"risk_lifecycle_p{project_id}")
        os.makedirs(lifecycle_dir, exist_ok=True)
        return lifecycle_dir

    @classmethod
    def _compute_upload_hash(cls, content: str) -> str:
        return hashlib.sha256(content.encode("utf-8", errors="replace")).hexdigest()[:16]

    @classmethod
    def generate_initial_contractual_assessment(
        cls,
        project_id: int,
        document_id: int,
        doc_name: str,
        step4_artifact: dict,
        step7_artifact: dict,
        step9_artifact: Optional[dict] = None,
        run_id: str = "baseline_run",
        logs_dir: Optional[str] = None,
    ) -> dict:
        """
        EL Upload: Phase 1 Initial Contractual Risk Assessment.
        Identifies contractual risks directly from verified EL evidence.
        Strict rule: Zero speculation on unobserved operational progress.
        """
        timestamp_utc = datetime.now(timezone.utc).isoformat()
        lifecycle_dir = cls.get_lifecycle_dir(project_id, logs_dir)

        risk_items: List[Dict[str, Any]] = []
        counter = 1

        # 1. Contractual ambiguities & missing acceptance criteria from Step 7 findings
        s7_findings = step7_artifact.get("findings", []) or []
        document_quality_findings: List[Dict[str, Any]] = []
        for finding in s7_findings:
            f_cat = (finding.get("category") or finding.get("finding_type") or "CONTRACTUAL_AMBIGUITY").upper()
            f_id = finding.get("finding_id", f"F_{counter:03d}")
            f_title = finding.get("title") or finding.get("issue_summary") or finding.get("finding_type") or f"Contractual Finding {f_id}"

            # Resolve quote, chunk, and locator safely from Step 7 schema
            chunk_indices = finding.get("source_chunk_indices") or []
            chunk_idx = chunk_indices[0] if (isinstance(chunk_indices, list) and len(chunk_indices) > 0) else finding.get("chunk_index")
            source_loc = finding.get("source_locator") or (f"chunk_{chunk_idx}" if chunk_idx is not None else None)
            source_quote = finding.get("source_quotation") or finding.get("exact_source_quotation") or finding.get("evidence_text") or finding.get("quote") or None

            # Check if typographical anomaly / document quality finding
            is_doc_quality = any(k in f_cat for k in ("TYPOGRAPHICAL", "TYPO", "SPELLING", "FORMATTING", "DOCUMENT_QUALITY")) or any(k in f_title.lower() for k in ("typographical", "spelling", "formatting anomaly"))
            if is_doc_quality:
                document_quality_findings.append({
                    "finding_id": f_id,
                    "title": f_title,
                    "category": "DOCUMENT_QUALITY",
                    "severity": finding.get("severity", "LOW"),
                    "source_locator": source_loc,
                    "quote": source_quote,
                    "chunk_index": chunk_idx,
                    "affected_candidate_ids": finding.get("affected_candidate_ids", []),
                    "explanation": finding.get("explanation"),
                    "disposition": finding.get("recommended_action") or finding.get("recommended_disposition") or "Typographical correction.",
                })
                continue

            # Map severity
            sev = finding.get("severity", "MEDIUM").upper()
            if sev not in ("LOW", "MEDIUM", "HIGH", "CRITICAL"):
                sev = "MEDIUM"
            aff_cids = finding.get("related_candidate_ids") or finding.get("affected_candidate_ids") or []
            aff_milestones = finding.get("linked_milestones") or finding.get("affected_milestones") or []

            risk_items.append({
                "risk_id": f"RISK-CTR-{counter:03d}",
                "canonical_id": f"ctr_{f_id.lower()}",
                "finding_id": f_id,
                "title": f_title,
                "category": "CONTRACTUAL",
                "risk_category": f_cat,
                "risk_source": "EL_EVIDENCE",
                "lifecycle_status": "NEW",
                "severity": sev,
                "probability": "MEDIUM",
                "impact": sev,
                "owner": finding.get("owner") or "Legal / Commercial / Vendor PM",
                "mitigation": finding.get("recommended_disposition") or "Clarify contractual clause prior to execution kickoff.",
                "linked_scope_items": aff_cids,
                "affected_milestones": aff_milestones,
                "evidence": {
                    "source_locator": source_loc,
                    "quote": source_quote,
                    "chunk_index": chunk_idx,
                    "document_id": document_id,
                },
                "status_change_reason": "Identified from initial EL contract extraction.",
                "unresolved_questions": [finding.get("explanation")] if finding.get("explanation") else ["Requires commercial review."],
            })
            counter += 1

        # 2. Unsupported or Quarantined Claims from Step 4 validation
        s4_claims = step4_artifact.get("atomic_claim_validations", []) or []
        for claim in s4_claims:
            if claim.get("status") in ("UNSUPPORTED", "UNCERTAIN"):
                c_text = claim.get("claim_text", "")
                c_id = claim.get("candidate_id", f"C_{counter:03d}")
                risk_items.append({
                    "risk_id": f"RISK-CTR-{counter:03d}",
                    "canonical_id": f"ctr_claim_{c_id.lower()}",
                    "title": f"Unverified Contractual Obligation: {c_text[:80]}...",
                    "category": "SCOPE",
                    "risk_source": "EL_EVIDENCE",
                    "lifecycle_status": "NEW",
                    "severity": "HIGH" if claim.get("status") == "UNSUPPORTED" else "MEDIUM",
                    "probability": "HIGH",
                    "impact": "MEDIUM",
                    "owner": "Engagement Manager",
                    "mitigation": "Review source contract text to confirm whether this requirement was formally agreed.",
                    "linked_scope_items": [c_id],
                    "affected_milestones": [],
                    "evidence": {
                        "source_locator": claim.get("source_locator") or f"candidate_{c_id}",
                        "quote": claim.get("evidence_text") or c_text,
                        "chunk_index": claim.get("chunk_index"),
                        "document_id": document_id,
                    },
                    "status_change_reason": f"Atomic claim marked as {claim.get('status')} during Step 4 validation.",
                    "unresolved_questions": [claim.get("reason") or "Evidence locator did not match verbatim contract span."],
                })
                counter += 1

        # 3. Customer Dependencies from Step 9 / Step 4 records
        customer_dependencies_register: List[Dict[str, Any]] = []
        dep_source = (step9_artifact.get("customer_dependencies") if step9_artifact else None)
        if dep_source is not None:
            raw_deps = dep_source
        else:
            raw_deps = [
                rec for rec in (step4_artifact.get("structured_records") or [])
                if any(k in (rec.get("record_type") or "").upper() for k in ("CLIENT", "CUSTOMER"))
                or rec.get("category") == "CUSTOMER_DEPENDENCY"
            ]

        for rec in raw_deps:
            c_name = rec.get("name") or rec.get("commitment_name") or "Customer Prerequisite"
            c_id = rec.get("candidate_id", f"CD_{len(customer_dependencies_register)+1:03d}")
            dep_entry = {
                "dependency_id": f"DEP-{len(customer_dependencies_register)+1:03d}",
                "candidate_id": c_id,
                "name": c_name,
                "owner": rec.get("owner") or "Client / Customer",
                "role": rec.get("role") or "EXTERNAL_PREREQUISITE",
                "impacted_vendor_work": rec.get("impacted_vendor_work") or "Vendor project execution and milestone delivery",
                "source_locator": rec.get("source_locator"),
                "source_quotation": rec.get("source_quotation") or rec.get("raw_text") or rec.get("description"),
                "risk_status": rec.get("risk_status") or "MONITORING_REQUIRED",
                "is_contractual_risk": False,
            }
            customer_dependencies_register.append(dep_entry)

            # If dependency is explicitly marked as a high/critical risk with defined reason, also include in risk_items
            if rec.get("is_risk") is True or rec.get("risk_status") in ("HIGH", "CRITICAL"):
                risk_items.append({
                    "risk_id": f"RISK-CTR-{counter:03d}",
                    "canonical_id": f"ctr_dep_{c_id.lower()}",
                    "title": f"High-Risk Customer Dependency: {c_name}",
                    "category": "DEPENDENCY",
                    "risk_source": "EL_EVIDENCE",
                    "lifecycle_status": "NEW",
                    "severity": rec.get("severity") or "HIGH",
                    "probability": "MEDIUM",
                    "impact": "HIGH",
                    "owner": "Client / Customer",
                    "mitigation": "Establish client kickoff dependency sign-off and schedule review.",
                    "linked_scope_items": [c_id],
                    "affected_milestones": [],
                    "evidence": {
                        "source_locator": rec.get("source_locator") or f"candidate_{c_id}",
                        "quote": rec.get("source_quotation") or rec.get("raw_text") or rec.get("description") or "",
                        "chunk_index": rec.get("chunk_index"),
                        "document_id": document_id,
                    },
                    "status_change_reason": "High-risk customer dependency identified as external blocker.",
                    "unresolved_questions": ["Customer fulfillment timing must be confirmed at project initiation."],
                })
                counter += 1

        snapshot = {
            "snapshot_id": f"RLS-P{project_id}-V01",
            "version": 1,
            "project_id": project_id,
            "document_id": document_id,
            "document_name": doc_name,
            "document_type": "EL",
            "assessment_type": "INITIAL_CONTRACTUAL_ASSESSMENT",
            "timestamp_utc": timestamp_utc,
            "run_id": run_id,
            "triggering_upload": {
                "document_id": document_id,
                "document_name": doc_name,
                "document_type": "EL",
            },
            "operational_execution_risk_calculated": False,
            "operational_risk_disclaimer": "Initial assessment strictly reflects verified contractual evidence. Operational progress risk calculation begins upon first MoM/Status report upload.",
            "metrics": {
                "total_risks": len(risk_items),
                "contractual_risks_count": len([r for r in risk_items if r.get("category") in ("CONTRACTUAL", "UNCLEAR_DEADLINE", "MISSING_ACCEPTANCE_CRITERIA", "AMBIGUOUS_COMMITMENT")]),
                "document_quality_findings_count": len(document_quality_findings),
                "customer_dependency_register_count": len(customer_dependencies_register),
                "operational_risks_count": 0,
                "new_count": len(risk_items),
                "continuing_count": 0,
                "increasing_count": 0,
                "decreasing_count": 0,
                "resolved_count": 0,
                "reopened_count": 0,
                "awaiting_confirmation_count": 0,
            },
            "risk_items": risk_items,
            "document_quality_findings": document_quality_findings,
            "customer_dependencies_register": customer_dependencies_register,
        }

        # Save snapshot
        snap_file = os.path.join(lifecycle_dir, f"snapshot_v01_{run_id}.json")
        active_file = os.path.join(lifecycle_dir, "active_risk_lifecycle.json")
        with open(snap_file, "w", encoding="utf-8") as f:
            json.dump(snapshot, f, indent=2, default=str)
        with open(active_file, "w", encoding="utf-8") as f:
            json.dump(snapshot, f, indent=2, default=str)

        return snapshot

    @classmethod
    def process_report_risk_assessment(
        cls,
        project_id: int,
        document_id: int,
        doc_name: str,
        doc_type: str,
        report_text: str,
        baseline_snapshot: Optional[dict] = None,
        run_id: str = "report_run",
        logs_dir: Optional[str] = None,
        previous_snapshot: Optional[dict] = None,
        extractions: Optional[dict] = None,
    ) -> dict:
        """
        MoM / Status Report Upload:
        - If first report: generates FIRST_OPERATIONAL_ASSESSMENT.
        - If subsequent report: generates RECALCULATED_OPERATIONAL_ASSESSMENT.
        Maintains stable risk IDs, tracks status transitions, prevents duplicate
        processing, and preserves historical snapshots.
        """
        timestamp_utc = datetime.now(timezone.utc).isoformat()
        lifecycle_dir = cls.get_lifecycle_dir(project_id, logs_dir)

        # 1. Check for duplicate upload processing
        active_file = os.path.join(lifecycle_dir, "active_risk_lifecycle.json")
        existing_snapshots = cls.get_project_snapshots(project_id, logs_dir)
        for prev in existing_snapshots:
            t_up = prev.get("triggering_upload", {})
            if t_up.get("document_id") == document_id and t_up.get("document_type") in ("MOM", "STATUS_REPORT"):
                # Duplicate upload detected
                return {
                    "is_duplicate": True,
                    "message": f"Document ID {document_id} was already processed in snapshot {prev.get('snapshot_id')}.",
                    "snapshot": prev,
                }

        # 2. Resolve previous baseline and operational state
        if not previous_snapshot and existing_snapshots:
            previous_snapshot = existing_snapshots[-1]

        prev_version = previous_snapshot.get("version", 0) if previous_snapshot else 0
        new_version = prev_version + 1
        is_first_report = not any(
            s.get("assessment_type") in ("FIRST_OPERATIONAL_ASSESSMENT", "RECALCULATED_OPERATIONAL_ASSESSMENT")
            for s in existing_snapshots
        )
        assessment_type = "FIRST_OPERATIONAL_ASSESSMENT" if is_first_report else "RECALCULATED_OPERATIONAL_ASSESSMENT"

        # 3. Analyze reported facts, blockers, decisions, and progress from report
        extractions = extractions or {}
        raw_activities = extractions.get("activities", []) or extractions.get("extractions", []) or []
        raw_blockers = extractions.get("blockers", []) or []
        raw_decisions = extractions.get("decisions", []) or []

        # If extractions not pre-parsed, extract key sentences via deterministic heuristics
        if not raw_activities and report_text:
            lines = report_text.splitlines()
            for line in lines:
                l_str = line.strip()
                if not l_str or len(l_str) < 15:
                    continue
                lower = l_str.lower()
                if any(k in lower for k in ("blocked", "delay", "waiting for", "waiting on", "dependency", "pending approval")):
                    raw_blockers.append({"text": l_str, "status": "BLOCKED"})
                elif any(k in lower for k in ("completed", "delivered", "signed off", "achieved")):
                    raw_activities.append({"activity_name": l_str, "status": "COMPLETED"})
                elif any(k in lower for k in ("in progress", "started", "developing", "working on")):
                    raw_activities.append({"activity_name": l_str, "status": "IN_PROGRESS"})
                elif any(k in lower for k in ("agreed", "decided", "approved", "resolution")):
                    raw_decisions.append({"text": l_str})

        # 4. Carry forward previous risks and compute transitions
        prev_risks = {r["canonical_id"]: r for r in (previous_snapshot.get("risk_items", []) if previous_snapshot else [])}
        updated_risks: List[Dict[str, Any]] = []

        counter = len(prev_risks) + 1
        seen_canonical = set()

        # Step 4A: Evaluate existing risks against latest report facts
        for canon_id, prev_r in prev_risks.items():
            seen_canonical.add(canon_id)
            title = prev_r.get("title", "")
            title_lower = title.lower()
            prev_status = prev_r.get("lifecycle_status", "NEW")
            prev_sev = prev_r.get("severity", "MEDIUM")

            # Stop words that should NOT trigger a false resolution match on their own
            GENERIC_STOP_WORDS = {
                "review", "status", "report", "update", "system", "process", "service", 
                "testing", "project", "pending", "complete", "completed", "sign-off", 
                "deployment", "implementation", "deliverable", "meeting", "phase", 
                "activity", "document", "documentation", "third-party", "vulnerability"
            }
            significant_title_words = [
                w.strip(".,;:()[]{}\"'")
                for w in title_lower.split()
                if len(w.strip(".,;:()[]{}\"'")) > 3 and w.strip(".,;:()[]{}\"'") not in GENERIC_STOP_WORDS
            ]

            def _is_strong_match(text: str) -> bool:
                if not text:
                    return False
                t_low = text.lower()
                # 1. Exact phrase match if title is substantial
                if len(title_lower) >= 15 and title_lower in t_low:
                    return True
                # 2. Or at least 2 significant specific content words must match
                if len(significant_title_words) >= 2:
                    matched = sum(1 for w in significant_title_words if w in t_low)
                    return matched >= 2
                elif len(significant_title_words) == 1:
                    return significant_title_words[0] in t_low
                return False

            # Check if mentioned as resolved in report
            resolved_evidence = None
            for act in raw_activities:
                if act.get("status") == "COMPLETED" and _is_strong_match(act.get("activity_name", "")):
                    resolved_evidence = act.get("activity_name")
                    break
            if not resolved_evidence:
                for dec in raw_decisions:
                    if _is_strong_match(dec.get("text", "")):
                        resolved_evidence = dec.get("text")
                        break

            # Check if mentioned as active blocker in report
            active_blocker = None
            for blk in raw_blockers:
                if _is_strong_match(blk.get("text", "")) or any(
                    w in blk.get("text", "").lower() 
                    for w in significant_title_words
                ):
                    active_blocker = blk.get("text")
                    break

            new_status = prev_status
            new_sev = prev_sev
            reason = "Risk status maintained from previous evaluation."

            if resolved_evidence:
                if prev_status != "RESOLVED":
                    new_status = "RESOLVED"
                    reason = f"Explicitly verified as resolved in report: '{resolved_evidence[:80]}'."
            elif active_blocker:
                if prev_status == "RESOLVED":
                    new_status = "REOPENED"
                    reason = f"Previously resolved risk reappeared as active blocker in report: '{active_blocker[:80]}'."
                else:
                    new_status = "INCREASING" if prev_sev in ("LOW", "MEDIUM") else "CONTINUING"
                    new_sev = "HIGH" if prev_sev == "MEDIUM" else ("CRITICAL" if prev_sev == "HIGH" else prev_sev)
                    reason = f"Active blocker cited in report: '{active_blocker[:80]}'."
            else:
                # Crucial Rule: Absence from report does NOT mean resolved!
                if prev_status in ("NEW", "INCREASING"):
                    new_status = "CONTINUING"
                    reason = "Continuing open risk; no resolution evidence provided in current report."
                elif prev_status == "RESOLVED":
                    new_status = "RESOLVED"
                    reason = "Remains resolved."

            updated_r = dict(prev_r)
            updated_r["lifecycle_status"] = new_status
            updated_r["severity"] = new_sev
            updated_r["status_change_reason"] = reason
            if resolved_evidence:
                updated_r["latest_evidence"] = resolved_evidence
            updated_risks.append(updated_r)

        # Step 4B: Identify new operational risks from current report
        for blk in raw_blockers:
            b_text = blk.get("text", "")
            # Check if already covered
            if any(b_text[:40].lower() in r.get("title", "").lower() for r in updated_risks):
                continue
            
            risk_id = f"RISK-OPR-{counter:03d}"
            canon_id = f"opr_blocker_{counter:03d}"
            updated_risks.append({
                "risk_id": risk_id,
                "canonical_id": canon_id,
                "title": f"Operational Blocker: {b_text[:80]}",
                "category": "SCHEDULE",
                "risk_source": "REPORTED_FACT",
                "lifecycle_status": "NEW",
                "severity": "HIGH",
                "probability": "HIGH",
                "impact": "HIGH",
                "owner": "Project Lead / Delivery Team",
                "mitigation": "Review blocker dependencies and schedule technical resolution meeting.",
                "linked_scope_items": [],
                "affected_milestones": [],
                "evidence": {
                    "source_locator": f"doc_{document_id}_blocker",
                    "quote": b_text,
                    "document_id": document_id,
                },
                "status_change_reason": "New blocker reported in latest progress update.",
                "unresolved_questions": ["Awaiting next review for resolution timeline."],
            })
            counter += 1

        # Calculate metrics
        c_risks = sum(1 for r in updated_risks if r.get("category") == "CONTRACTUAL")
        o_risks = sum(1 for r in updated_risks if r.get("category") != "CONTRACTUAL")
        new_cnt = sum(1 for r in updated_risks if r.get("lifecycle_status") == "NEW")
        cont_cnt = sum(1 for r in updated_risks if r.get("lifecycle_status") == "CONTINUING")
        inc_cnt = sum(1 for r in updated_risks if r.get("lifecycle_status") == "INCREASING")
        dec_cnt = sum(1 for r in updated_risks if r.get("lifecycle_status") == "DECREASING")
        res_cnt = sum(1 for r in updated_risks if r.get("lifecycle_status") == "RESOLVED")
        reop_cnt = sum(1 for r in updated_risks if r.get("lifecycle_status") == "REOPENED")
        await_cnt = sum(1 for r in updated_risks if r.get("lifecycle_status") == "AWAITING_CONFIRMATION")

        snapshot = {
            "snapshot_id": f"RLS-P{project_id}-V{new_version:02d}",
            "version": new_version,
            "project_id": project_id,
            "document_id": document_id,
            "document_name": doc_name,
            "document_type": doc_type,
            "assessment_type": assessment_type,
            "timestamp_utc": timestamp_utc,
            "run_id": run_id,
            "triggering_upload": {
                "document_id": document_id,
                "document_name": doc_name,
                "document_type": doc_type,
            },
            "operational_execution_risk_calculated": True,
            "reported_facts_count": len(raw_activities) + len(raw_blockers),
            "metrics": {
                "total_risks": len(updated_risks),
                "contractual_risks_count": c_risks,
                "operational_risks_count": o_risks,
                "new_count": new_cnt,
                "continuing_count": cont_cnt,
                "increasing_count": inc_cnt,
                "decreasing_count": dec_cnt,
                "resolved_count": res_cnt,
                "reopened_count": reop_cnt,
                "awaiting_confirmation_count": await_cnt,
            },
            "risk_items": updated_risks,
        }

        # Save snapshot
        snap_file = os.path.join(lifecycle_dir, f"snapshot_v{new_version:02d}_{run_id}.json")
        active_file = os.path.join(lifecycle_dir, "active_risk_lifecycle.json")
        with open(snap_file, "w", encoding="utf-8") as f:
            json.dump(snapshot, f, indent=2, default=str)
        with open(active_file, "w", encoding="utf-8") as f:
            json.dump(snapshot, f, indent=2, default=str)

        return snapshot

    @classmethod
    def finalize_project_risks(
        cls,
        project_id: int,
        completion_evidence: str,
        authorized_by: str,
        run_id: str = "completion_run",
        logs_dir: Optional[str] = None,
    ) -> dict:
        """
        Project Completion: Final risk summary.
        Closes only supported risks; preserves all historical risk records.
        """
        timestamp_utc = datetime.now(timezone.utc).isoformat()
        lifecycle_dir = cls.get_lifecycle_dir(project_id, logs_dir)
        existing_snapshots = cls.get_project_snapshots(project_id, logs_dir)
        previous_snapshot = existing_snapshots[-1] if existing_snapshots else {}

        prev_version = previous_snapshot.get("version", 0)
        new_version = prev_version + 1

        final_risks = []
        for r in previous_snapshot.get("risk_items", []):
            item = dict(r)
            # Close risk if supported by completion evidence
            item["lifecycle_status"] = "RESOLVED"
            item["status_change_reason"] = f"Project marked formally complete by {authorized_by}. Evidence: {completion_evidence[:80]}"
            item["resolution_authorized_by"] = authorized_by
            final_risks.append(item)

        snapshot = {
            "snapshot_id": f"RLS-P{project_id}-V{new_version:02d}",
            "version": new_version,
            "project_id": project_id,
            "document_id": 0,
            "document_name": "PROJECT_COMPLETION",
            "document_type": "FINAL_COMPLETION",
            "assessment_type": "PROJECT_COMPLETION_SUMMARY",
            "timestamp_utc": timestamp_utc,
            "run_id": run_id,
            "completion_details": {
                "authorized_by": authorized_by,
                "evidence": completion_evidence,
            },
            "metrics": {
                "total_risks": len(final_risks),
                "resolved_count": len(final_risks),
                "new_count": 0,
                "continuing_count": 0,
            },
            "risk_items": final_risks,
        }

        snap_file = os.path.join(lifecycle_dir, f"snapshot_v{new_version:02d}_{run_id}.json")
        active_file = os.path.join(lifecycle_dir, "active_risk_lifecycle.json")
        with open(snap_file, "w", encoding="utf-8") as f:
            json.dump(snapshot, f, indent=2, default=str)
        with open(active_file, "w", encoding="utf-8") as f:
            json.dump(snapshot, f, indent=2, default=str)

        return snapshot

    @classmethod
    def get_project_snapshots(cls, project_id: int, logs_dir: Optional[str] = None) -> List[Dict[str, Any]]:
        lifecycle_dir = cls.get_lifecycle_dir(project_id, logs_dir)
        snapshots = []
        if not os.path.exists(lifecycle_dir):
            return []

        for f_name in sorted(os.listdir(lifecycle_dir)):
            if f_name.startswith("snapshot_v") and f_name.endswith(".json"):
                f_path = os.path.join(lifecycle_dir, f_name)
                try:
                    with open(f_path, "r", encoding="utf-8") as f:
                        data = json.load(f)
                        snapshots.append(data)
                except Exception:
                    pass

        return sorted(snapshots, key=lambda s: s.get("version", 0))

    @classmethod
    def get_current_risk_register(cls, project_id: int, logs_dir: Optional[str] = None) -> Dict[str, Any]:
        lifecycle_dir = cls.get_lifecycle_dir(project_id, logs_dir)
        active_file = os.path.join(lifecycle_dir, "active_risk_lifecycle.json")
        if os.path.exists(active_file):
            try:
                with open(active_file, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass

        snapshots = cls.get_project_snapshots(project_id, logs_dir)
        if snapshots:
            return snapshots[-1]

        return {
            "project_id": project_id,
            "version": 0,
            "assessment_type": "NO_ASSESSMENT_PERFORMED",
            "metrics": {"total_risks": 0},
            "risk_items": [],
        }

