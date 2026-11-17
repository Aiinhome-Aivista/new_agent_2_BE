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

import os
import sys
import time
import json
import threading
import re
from datetime import datetime, timezone, date, timedelta
from typing import Any, Dict, List, Optional, Tuple

from services.recurring_deliverable_service import RecurringDeliverableService
from services.party_resolver import PartyResolver
from services.deadline_inference_service import DeadlineInferenceService
from core.baseline_config import TRIGGER_PATTERNS, GATING_RULES, WORKING_DAY_BUFFER


class Step9Logger:
    """
    Dedicated Logger for Step 9 (Commitments, Dependencies, & Risk Analysis)
    Inspection Runs. Guarantees independent logging to:
    logs/step9_recurring_p{project_id}_d{document_id}_{timestamp}.log
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

        try:
            os.makedirs(self.log_dir, exist_ok=True)
        except Exception:
            pass
        self.run_log_path = os.path.join(self.log_dir, f"step9_recurring_p{project_id}_d{document_id}_{timestamp_str}.log")
        self.active_log_path = os.path.join(self.log_dir, f"step9_recurring_p{project_id}_d{document_id}.log")
        self._lock = threading.Lock()

    def _format_timestamp(self) -> str:
        return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    def raw_log(self, text: str):
        if os.getenv("ENABLE_STEP_PRINTS", "true").lower() in ("true", "1", "yes"):
            try:
                sys.stdout.write(text + "\n")
                sys.stdout.flush()
            except Exception:
                try:
                    enc = getattr(sys.stdout, "encoding", None) or "utf-8"
                    safe_text = text.encode(enc, errors="replace").decode(enc)
                    sys.stdout.write(safe_text + "\n")
                    sys.stdout.flush()
                except Exception:
                    pass

        if os.getenv("ENABLE_DISK_LOGS", "true").lower() in ("true", "1", "yes"):
            try:
                with self._lock:
                    if not os.path.exists(self.log_dir):
                        os.makedirs(self.log_dir, exist_ok=True)
                    if hasattr(self, "active_log_path") and self.active_log_path:
                        with open(self.active_log_path, "a", encoding="utf-8") as f_act:
                            f_act.write(text + "\n")
                    if hasattr(self, "run_log_path") and self.run_log_path:
                        with open(self.run_log_path, "a", encoding="utf-8") as f_run:
                            f_run.write(text + "\n")
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


    def error(self, context: str, error_msg: str, traceback_str: Optional[str] = None):
        ts = self._format_timestamp()
        sep = "!" * 80
        err_block = (
            f"\n{sep}\n"
            f"[{ts}] [ERROR] Problem in {self.__class__.__name__} at: {context}\n"
            f"Error Message: {error_msg}\n"
        )
        if traceback_str:
            err_block += f"Traceback:\n{traceback_str}\n"
        err_block += f"{sep}\n"
        self.raw_log(err_block)
        pass


class Step9Inspector:
    """
    Step 9: Commitments, Dependencies, and Risk Analysis Inspector.
    Executes in inspection/dry-run mode:
    1. Dependency-aware safety gate: verifies Step 4, Step 5, & Step 8 prerequisites.
    2. Consolidates contractual commitments: RECURRING, ONE_TIME, TRIGGER_BASED.
    3. Clearly distinguishes Customer Responsibilities (prerequisites for vendor work)
       from Vendor Out-of-Scope Exclusions.
    4. Simulates schedule expansion across project engagement period with ZERO database mutations.
    5. Step 10 is enabled for read-only synthesis and UI presentation.
    """

    @classmethod
    def execute_inspection(
        cls,
        project_id: int,
        document_id: int,
        doc_name: str,
        step4_artifact: dict,
        step5_artifact: dict,
        step8_artifact: dict,
        run_id: str,
        timestamp_str: str,
        logs_dir: str,
        mode: str = "DRY_RUN",
    ) -> dict:
        t_start = time.time()
        logger = Step9Logger(project_id, document_id, timestamp_str, log_dir=logs_dir)

        logger.banner("STEP 9: COMMITMENTS, DEPENDENCIES, & RISK ANALYSIS (INSPECTION / DRY-RUN)")
        logger.log(f"Project ID: {project_id} | Document ID: {document_id} | Document: {doc_name}")
        logger.log(f"Run ID: {run_id} | Mode: {mode} (actual_writes=0, zero mutations)")

        # ── 1. Prerequisite Safety Gate ──
        logger.section("1. Prerequisite Safety Gate Verification")
        s4_verdict = step4_artifact.get("final_verdict", "UNKNOWN")
        s4_passed = s4_verdict in ("STEP_4_VALIDATION_PASSED", "STEP_4_VALIDATION_PASSED_WITH_WARNINGS")
        s5_gate_passed = (
            step5_artifact.get("validation_gate_passed")
            if step5_artifact.get("validation_gate_passed") is not None
            else step5_artifact.get("validation_gate", {}).get("passed", False)
        )
        s8_status = step8_artifact.get("status", "UNKNOWN")

        gate_blocked = False
        blocked_reasons = []

        if not s4_passed:
            gate_blocked = True
            blocked_reasons.append(f"Step 4 validation gate failed (verdict: {s4_verdict})")

        if not s5_gate_passed:
            gate_blocked = True
            blocked_reasons.append("Step 5 validation gate failed (persistence blocked)")

        if s8_status == "BLOCKED":
            gate_blocked = True
            blocked_reasons.append(f"Step 8 was blocked: {step8_artifact.get('blocked_reason', 'Upstream gate failure')}")

        if gate_blocked:
            logger.log(f"  [BLOCKED] Prerequisite gate failure: {'; '.join(blocked_reasons)}")
            logger.log("  Step 9 running in BLOCKED diagnostic mode — operational recurring actions suspended.")
            blocked_artifact = {
                "run_id": run_id,
                "timestamp_utc": datetime.now(timezone.utc).isoformat(),
                "project_id": project_id,
                "document_id": document_id,
                "document_name": doc_name,
                "step_name": "Step 9: Commitments, Dependencies, and Risk Analysis",
                "status": "BLOCKED",
                "execution_mode": mode,
                "blocked_reason": "; ".join(blocked_reasons),
                "prerequisite_status": {
                    "step4_verdict": s4_verdict,
                    "step5_validation_gate_passed": s5_gate_passed,
                    "step8_status": s8_status,
                },
                "safety_confirmations": {
                    "production_baseline_mutation_disabled": True,
                    "production_recurring_writes_disabled": True,
                    "actual_writes_performed": 0,
                    "step_10_disabled": False,
                },
                "consolidated_commitments": [],
                "customer_dependencies": [],
                "vendor_out_of_scope_exclusions": [],
                "recurring_commitments_preview": [],
                "simulated_occurrences_preview": [],
                "summary": {
                    "total_commitments": 0,
                    "recurring_count": 0,
                    "one_time_count": 0,
                    "trigger_based_count": 0,
                    "customer_dependencies_count": 0,
                    "vendor_exclusions_count": 0,
                    "actual_writes": 0,
                    "status": "BLOCKED",
                },
            }
            json_run_path = os.path.join(logs_dir, f"step9_recurring_p{project_id}_d{document_id}_{timestamp_str}.json")
            json_active_path = os.path.join(logs_dir, f"step9_recurring_p{project_id}_d{document_id}.json")
            if os.getenv("ENABLE_DISK_ARTIFACTS", "true").lower() in ("true", "1", "yes"):
                with open(json_run_path, "w", encoding="utf-8") as f:
                    json.dump(blocked_artifact, f, indent=2, default=str)
                with open(json_active_path, "w", encoding="utf-8") as f:
                    json.dump(blocked_artifact, f, indent=2, default=str)
            logger.banner("STEP 9 BLOCKED DIAGNOSTIC COMPLETE — ADVANCING TO STEP 10")
            return {
                "status": "BLOCKED",
                "step9_artifact": blocked_artifact,
                "step9_run_json_path": json_run_path,
                "step9_active_json_path": json_active_path,
                "step9_log_path": logger.run_log_path,
            }

        logger.log("  [PASSED] Prerequisite safety checks passed. Step 4, Step 5, and Step 8 verified.")

        # ── 2. Consolidate Contractual Commitments & Classify Types ──
        logger.section("2. Contractual Commitments Consolidation")
        proposed_items = step5_artifact.get("proposed_scope_items_preview") or step5_artifact.get("proposed_scope_items") or []
        structured_records = step4_artifact.get("structured_records") or []

        commitments: List[Dict[str, Any]] = []
        customer_dependencies: List[Dict[str, Any]] = []
        client_responsibilities: List[Dict[str, Any]] = []
        contract_period_records: List[Dict[str, Any]] = []
        governance_roles: List[Dict[str, Any]] = []
        feature_descriptions: List[Dict[str, Any]] = []
        vendor_exclusions: List[Dict[str, Any]] = []
        scope_delivery_obligations: List[Dict[str, Any]] = []
        recurring_commitments: List[Dict[str, Any]] = []
        trigger_commitments: List[Dict[str, Any]] = []
        milestone_acceptance_obligations: List[Dict[str, Any]] = []
        seen_cand_ids = set()

        # Contract dates (FIX 4 & FIX 6 generic extraction)
        contract_start_str = step8_artifact.get("contract_start_date")
        if not contract_start_str:
            for r in structured_records + proposed_items:
                combined_txt = ((r.get("raw_text") or "") + " " + (r.get("name") or "")).lower()
                parsed_d = DeadlineInferenceService.parse_date_string(combined_txt)
                if parsed_d:
                    contract_start_str = parsed_d
                    break
        if not contract_start_str:
            contract_start_str = date.today().isoformat()

        try:
            base_date = datetime.strptime(contract_start_str, "%Y-%m-%d").date()
        except Exception:
            base_date = date.today()

        for item in proposed_items + structured_records:
            cid = item.get("candidate_id")
            if cid and cid in seen_cand_ids:
                continue
            if cid:
                seen_cand_ids.add(cid)

            name = item.get("name") or "Commitment"
            desc = item.get("description") or item.get("raw_text") or ""
            comb = f"{name} {desc}".lower()
            rec_type = (item.get("record_type") or item.get("category") or "").upper()
            scope_type = (item.get("scope_type") or item.get("scope_status") or "IN_SCOPE").upper()

            # ── First: Detect genuine Recurring Commitments and Trigger-based SLAs ──
            is_weekly = "weekly status" in comb or ("weekly" in comb and "report" in comb)
            is_fortnightly = "fortnight" in comb
            is_monthly_steering = ("steering committee" in comb and ("month" in comb or "compulsorily meet" in comb or "at least once" in comb or "organize steering" in comb))
            is_feedback_sla = ("3 working days" in comb or ("feedback" in comb and ("deemed to have been accepted" in comb or "within the stipulated time" in comb)))

            if is_weekly or is_fortnightly or is_monthly_steering:
                if is_weekly:
                    cadence = "weekly"
                    c_name = "Weekly Status Reporting"
                    resp = "Vendor Delivery Team"
                    rule = "Source text specifies weekly status reports circulated to key stakeholders."
                elif is_fortnightly:
                    cadence = "fortnightly"
                    c_name = "Fortnightly Key Stakeholder Updates"
                    resp = "Vendor Project Manager"
                    rule = "Source text specifies keeping key stakeholders informed on a fortnightly basis."
                else:
                    cadence = "monthly"
                    c_name = "Monthly Steering Committee Meetings"
                    resp = "Joint Steering Committee"
                    rule = "Source text specifies Steering Committee compulsorily meets at least once a month."

                comm_id = f"comm_p{project_id}_d{document_id}_{len(commitments) + 1:03d}"
                comm_record = {
                    "commitment_id": comm_id,
                    "candidate_id": cid,
                    "title": name or c_name,
                    "name": name or c_name,
                    "commitment_name": name or c_name,
                    "description": desc,
                    "commitment_type": "RECURRING",
                    "frequency": cadence.upper(),
                    "cadence": cadence,
                    "responsible_party": resp,
                    "owner": "JOINT" if "Joint" in resp else "VENDOR",
                    "proposed_next_occurrence": (base_date + (timedelta(days=7) if cadence == "weekly" else (timedelta(days=14) if cadence == "fortnightly" else timedelta(days=30)))).isoformat(),
                    "source_locator": item.get("source_locator") or "chunk_0",
                    "source_quotation": item.get("raw_text") or desc,
                    "risk_status": "LOW",
                    "extraction_confidence": 0.9,
                    "current_status": "ACTIVE",
                    "rule": rule,
                }
                commitments.append(comm_record)
                recurring_commitments.append(comm_record)
                logger.log(f"  [RECURRING COMMITMENT] {cid}: '{name}' ({cadence.upper()})")
                continue

            if is_feedback_sla:
                comm_id = f"comm_p{project_id}_d{document_id}_{len(commitments) + 1:03d}"
                comm_record = {
                    "commitment_id": comm_id,
                    "candidate_id": cid,
                    "title": name or "Client Deliverable Feedback & Deemed Acceptance SLA",
                    "name": name or "Client Deliverable Feedback & Deemed Acceptance SLA",
                    "commitment_name": name or "Client Deliverable Feedback & Deemed Acceptance SLA",
                    "description": desc,
                    "commitment_type": "TRIGGER_BASED",
                    "frequency": "EVENT_DRIVEN",
                    "trigger_event": "Deliverable Submission",
                    "sla_duration": 3,
                    "sla_unit": "WORKING_DAYS",
                    "working_day_rule": True,
                    "consequence": "Deemed accepted by client if no feedback received within stipulated time",
                    "responsible_party": "Client / Customer",
                    "owner": "CLIENT",
                    "proposed_next_occurrence": "ON_TRIGGER",
                    "source_locator": item.get("source_locator") or "chunk_0",
                    "source_quotation": item.get("raw_text") or desc,
                    "risk_status": "MONITORING_REQUIRED",
                    "extraction_confidence": 0.95,
                    "current_status": "ACTIVE",
                }
                commitments.append(comm_record)
                trigger_commitments.append(comm_record)
                logger.log(f"  [TRIGGER-BASED SLA] {cid}: '{name}' (3-working-day feedback SLA)")
                continue

            # ── Separation Rule: Customer Responsibilities vs Vendor Out-of-Scope vs Context ──
            item_role = (item.get("role") or "").upper()
            is_exclusion = (
                item_role == "EXCLUSION"
                or scope_type == "OUT_OF_SCOPE"
                or any(k in comb for k in ("out of scope", "not included", "will be treated as a change request", "google will not be responsible", "not responsible for", "exclusion"))
            )
            is_context = (
                item_role == "CONTEXT"
                or any(k in name.lower() for k in ("covered legal entity", "onsite location", "engagement period", "contract validity"))
            )
            is_vendor_feature = (
                item_role == "VENDOR_WORK"
                or item.get("work_item_eligible", False)
                or any(k in name.lower() for k in ("application success text", "user view"))
            )

            if is_exclusion:
                ve_record = {
                    "candidate_id": cid,
                    "name": name,
                    "description": desc,
                    "owner": "Vendor Excluded",
                    "role": "EXCLUSION",
                    "scope_type": "OUT_OF_SCOPE",
                    "change_request_required": True,
                    "source_locator": item.get("source_locator"),
                    "source_quotation": item.get("raw_text") or desc,
                }
                vendor_exclusions.append(ve_record)
                logger.log(f"  [VENDOR EXCLUSION] {cid}: '{name}' (Explicit contractual exclusion)")
                continue

            if is_context:
                contract_period_records.append({
                    "candidate_id": cid,
                    "name": name,
                    "record_type": "CONTEXT_METADATA",
                    "description": desc,
                    "source_locator": item.get("source_locator"),
                    "source_quotation": item.get("raw_text") or desc,
                })
                logger.log(f"  [CONTEXT METADATA] {cid}: '{name}' (Descriptive context / metadata; not a client duty)")
                continue

            if is_vendor_feature:
                logger.log(f"  [VENDOR FEATURE] {cid}: '{name}' (Vendor delivery feature; excluded from client responsibilities)")
                continue

            is_client_resp = (
                item_role == "CLIENT_DUTY"
                or any(k in rec_type for k in ("CLIENT", "CUSTOMER"))
                or any(k in comb for k in ("client responsibility", "customer responsibility", "client will provide", "customer will provide", "client to furnish", "client must", "customer must"))
            )

            if is_client_resp:
                DEPENDENCY_EVIDENCE_MARKERS = (
                    "prerequisite", "dependency", "depends on", "prior to",
                    "before commencement", "before start", "required before",
                    "blocking", "shall provide before", "condition precedent",
                    "access to", "provisioning of", "credentials", "environment access",
                    "network access", "client prerequisite", "infrastructure",
                    "cloud resources", "authorization", "authorizations", "sign-off",
                    "signoff", "content", "contents", "data access", "test data",
                )
                is_dependency_rec_type = rec_type in ("CUSTOMER_DEPENDENCY", "CLIENT_DEPENDENCY", "CUSTOMER_PREREQUISITE", "CLIENT_PREREQUISITE", "DEPENDENCY")
                has_dependency_evidence = is_dependency_rec_type or any(m in comb for m in DEPENDENCY_EVIDENCE_MARKERS)

                # Classify dependency type per spec
                comb_l = comb.lower()
                if any(k in comb_l for k in ("cloud", "infrastructure", "server", "hosting", "credentials", "login", "network", "firewall", "git", "repo", "environment", "vpn", "access to")):
                    dep_type = "ENVIRONMENT_ACCESS"
                elif any(k in comb_l for k in ("database", "data source", "schema", "test data", "source system", "api key", "data access")):
                    dep_type = "DATA_ACCESS"
                elif any(k in comb_l for k in ("wireframe", "mockup", "design", "branding", "specification", "ui/ux")):
                    dep_type = "DESIGN_INPUT"
                elif any(k in comb_l for k in ("sign-off", "signoff", "approval", "acceptance", "review and approve", "uat approval")):
                    dep_type = "SIGN_OFF"
                elif any(k in comb_l for k in ("sme", "stakeholder", "personnel", "spoc", "staff", "resource availability", "interview")):
                    dep_type = "STAFFING"
                elif any(k in comb_l for k in ("content", "document", "copy", "guideline", "template", "business requirement")):
                    dep_type = "CONTENT"
                else:
                    dep_type = "ENVIRONMENT_ACCESS"

                if has_dependency_evidence:
                    cd_record = {
                        "candidate_id": cid,
                        "name": name,
                        "description": desc,
                        "owner": "CLIENT",
                        "role": "EXTERNAL_PREREQUISITE",
                        "dependency_type": dep_type,
                        "impacted_vendor_work": "Vendor project execution and milestone delivery",
                        "source_locator": item.get("source_locator"),
                        "source_quotation": item.get("raw_text") or desc,
                        "risk_status": "MONITORING_REQUIRED",
                        "dependency_rationale": "Source text establishes client obligation as prerequisite/blocker for vendor activities.",
                    }
                    customer_dependencies.append(cd_record)
                    logger.log(f"  [CUSTOMER DEPENDENCY] {cid}: '{name}' ({dep_type} - Prerequisite for Vendor work)")
                else:
                    cr_record = {
                        "candidate_id": cid,
                        "name": name,
                        "description": desc,
                        "owner": "CLIENT",
                        "role": "CLIENT_RESPONSIBILITY",
                        "source_locator": item.get("source_locator"),
                        "source_quotation": item.get("raw_text") or desc,
                        "is_blocking_dependency": False,
                    }
                    client_responsibilities.append(cr_record)
                    logger.log(f"  [CLIENT RESPONSIBILITY] {cid}: '{name}' (General client obligation)")
                continue

            # ── Non-Commitment Exclusions: Period Metadata, Roles, Feature Descriptions ──
            name_lower = name.lower()
            if any(k in name_lower for k in ("engagement period", "contract validity period", "validity period", "contract period")):
                contract_period_records.append({
                    "candidate_id": cid,
                    "name": name,
                    "record_type": "CONTRACT_PERIOD_METADATA",
                    "description": desc,
                    "source_locator": item.get("source_locator"),
                    "source_quotation": item.get("raw_text") or desc,
                })
                logger.log(f"  [CONTRACT METADATA] {cid}: '{name}' (Period metadata; not a delivery commitment)")
                continue

            if any(k in name_lower for k in ("escalation point", "project director escalation")) or rec_type in ("ROLE", "GOVERNANCE_ROLE"):
                governance_roles.append({
                    "candidate_id": cid,
                    "name": name,
                    "record_type": "GOVERNANCE_ROLE",
                    "description": desc,
                    "source_locator": item.get("source_locator"),
                    "source_quotation": item.get("raw_text") or desc,
                })
                logger.log(f"  [GOVERNANCE ROLE] {cid}: '{name}' (Role description; not a delivery commitment)")
                continue

            if "employee compliance reporting" in name_lower:
                feature_descriptions.append({
                    "candidate_id": cid,
                    "name": name,
                    "record_type": "FEATURE_DESCRIPTION",
                    "description": desc,
                    "source_locator": item.get("source_locator"),
                    "source_quotation": item.get("raw_text") or desc,
                })
                logger.log(f"  [FEATURE DESCRIPTION] {cid}: '{name}' (Feature description; not a delivery commitment)")
                continue

            # ── Commitment Classification: RECURRING, ONE_TIME, TRIGGER_BASED, MILESTONE ──
            cadence = RecurringDeliverableService._detect_recurrence_cadence(comb)
            if not cadence and item.get("recurrence") in ("Weekly", "Monthly", "Recurring"):
                cadence = item.get("recurrence").lower()

            is_trigger = any(k in comb for k in ("upon", "on demand", "as needed", "triggered by", "in the event of", "if requested"))
            is_ms_acc = any(k in comb for k in ("sign-off", "signoff", "acceptance", "uat approval", "go-live sign"))
            
            # Derive realistic responsible party
            if "steering committee" in comb or "governance" in comb or "pmo" in comb:
                responsible_party = "Project Steering Committee / PMO"
            elif any(k in rec_type for k in ("CLIENT", "CUSTOMER")):
                responsible_party = "Client / Customer"
            elif "vendor" in comb or "deliverable" in rec_type or scope_type == "IN_SCOPE":
                responsible_party = "Vendor Delivery Team"
            else:
                responsible_party = item.get("owner") or "Vendor Delivery Team"

            if cadence:
                comm_type = "RECURRING"
                frequency = cadence.upper()
                next_occ = (base_date + (timedelta(days=7) if cadence == "weekly" else timedelta(days=30))).isoformat()
            elif is_trigger:
                comm_type = "TRIGGER_BASED"
                frequency = "EVENT_DRIVEN"
                next_occ = "ON_TRIGGER"
            elif is_ms_acc:
                comm_type = "MILESTONE_ACCEPTANCE"
                frequency = "MILESTONE_GATED"
                next_occ = str(item.get("deadline_normalized") or item.get("deadline") or "MILESTONE_ANCHORED")
            elif is_trigger:
                comm_record = {
                    "candidate_id": cid,
                    "commitment_name": name,
                    "description": desc,
                    "commitment_type": "TRIGGER_BASED",
                    "frequency": "EVENT_DRIVEN",
                    "responsible_party": responsible_party,
                    "proposed_next_occurrence": "ON_TRIGGER",
                    "source_locator": item.get("source_locator"),
                    "source_quotation": item.get("raw_text") or desc,
                    "risk_status": "REVIEW_REQUIRED",
                }
                commitments.append(comm_record)
                trigger_commitments.append(comm_record)
            else:
                # Genuine separation: Deliverables / standard work items are tracked in scope_items,
                # NOT duplicated into the commitments register as fake single deliveries.
                scope_delivery_obligations.append({
                    "candidate_id": cid,
                    "name": name,
                    "description": desc,
                    "record_type": rec_type,
                    "scope_type": scope_type,
                })

        # Map genuine Table 3 payment milestones from step8_artifact as MILESTONE_GATED commitments
        payment_ms = [
            m for m in step8_artifact.get("milestones", [])
            if m.get("date_provenance") == "CONTRACT_CONFIRMED" or any(k in (m.get("name") or "").lower() for k in ("60%", "30%", "10%", "implement", "sign-off", "hypercare"))
        ]
        for idx, m in enumerate(payment_ms, 1):
            ms_name = m.get("name")
            if not any(c.get("commitment_name") == ms_name for c in commitments):
                ms_comm = {
                    "candidate_id": m.get("scope_candidate_id") or f"c_pay_ms_{idx}",
                    "name": ms_name,
                    "commitment_name": ms_name,
                    "description": f"Contractual payment milestone trigger: {ms_name} ({m.get('planned_date')})",
                    "commitment_type": "MILESTONE_ACCEPTANCE",
                    "frequency": "MILESTONE_GATED",
                    "responsible_party": "Client / Customer",
                    "owner": "CLIENT",
                    "proposed_next_occurrence": str(m.get("planned_date") or "MILESTONE_ANCHORED"),
                    "source_locator": m.get("source_locator") or "table_payment_milestones",
                    "source_quotation": m.get("source_quotation") or ms_name,
                    "linked_milestones": [ms_name],
                    "risk_status": "LOW",
                }
                commitments.append(ms_comm)
                milestone_acceptance_obligations.append(ms_comm)

        # Scan full text corpus for trigger-based commercial terms (FIX 6)
        full_text_corpus = " ".join(
            (r.get("raw_text") or "") + " " + (r.get("description") or "")
            for r in structured_records + proposed_items
        )
        for tp in TRIGGER_PATTERNS:
            t_type = tp["term_type"]
            t_pat = tp["pattern"]
            m_term = re.search(t_pat, full_text_corpus, re.IGNORECASE)
            if m_term:
                days_or_weeks = m_term.group(1) if m_term.groups() else ""
                t_title = t_type.replace("_", " ").title()
                t_comm_id = f"comm_p{project_id}_d{document_id}_{len(commitments) + 1:03d}"
                t_rec = {
                    "commitment_id": t_comm_id,
                    "candidate_id": f"trigger_{t_type.lower()}",
                    "title": t_title,
                    "name": t_title,
                    "commitment_name": t_title,
                    "description": m_term.group(0),
                    "commitment_type": "TRIGGER_BASED",
                    "term_type": t_type,
                    "trigger_event": t_type.replace("_", " ").lower(),
                    "frequency": "EVENT_DRIVEN",
                    "deadline_grace_period": f"{days_or_weeks} days/weeks",
                    "consequence": tp.get("stated_consequence"),
                    "responsible_party": "Client / Customer" if tp.get("default_owner") == "CLIENT" else "Vendor Delivery Team",
                    "owner": tp.get("default_owner", "CLIENT"),
                    "proposed_next_occurrence": "ON_TRIGGER",
                    "source_locator": "contract_terms",
                    "source_quotation": m_term.group(0),
                    "risk_status": "MONITORING_REQUIRED",
                    "extraction_confidence": 0.95,
                    "current_status": "ACTIVE",
                }
                if not any(c.get("commitment_name") == t_title for c in commitments):
                    commitments.append(t_rec)
                    trigger_commitments.append(t_rec)
                    logger.log(f"  [COMMERCIAL TRIGGER COMMITMENT] {t_title}: '{m_term.group(0)}'")

        # Deduplicate commitments by cadence and subject (FIX 6)
        deduped_commitments = []
        seen_comm_keys = set()
        for c in commitments:
            c_subj = re.sub(r'[^a-z0-9]', '', (c.get("commitment_name") or c.get("name") or "").lower())
            key = (c.get("commitment_type"), c.get("frequency"), c_subj[:25])
            if key not in seen_comm_keys:
                seen_comm_keys.add(key)
                deduped_commitments.append(c)
        commitments = deduped_commitments

        # Deduplicate customer dependencies (FIX 5)
        deduped_deps = []
        seen_dep_keys = set()
        for d in customer_dependencies:
            norm_name = re.sub(r'[^a-z0-9]', '', (d.get("name") or "").lower())
            key = (d.get("dependency_type"), norm_name[:25])
            if key not in seen_dep_keys:
                seen_dep_keys.add(key)
                deduped_deps.append(d)
        customer_dependencies = deduped_deps

        # Gated items and required_by calculation for customer dependencies (FIX 5)
        scheduled_wis = step8_artifact.get("schedule_prediction_preview") or step8_artifact.get("work_items_schedule") or []
        for cd in customer_dependencies:
            dtype = cd.get("dependency_type", "ENVIRONMENT_ACCESS")
            gating_kw = GATING_RULES.get(dtype, [])
            gated = []
            for wi in scheduled_wis:
                wi_text = f"{wi.get('name', '')} {wi.get('description', '')}".lower()
                if dtype in ("ENVIRONMENT_ACCESS", "DATA_ACCESS", "STAFFING"):
                    gated.append(wi.get("candidate_id") or wi.get("name"))
                elif any(kw in wi_text for kw in gating_kw):
                    gated.append(wi.get("candidate_id") or wi.get("name"))
            cd["gated_items"] = gated[:10]

            gated_starts = []
            for wi in scheduled_wis:
                if (wi.get("candidate_id") in gated or wi.get("name") in gated) and wi.get("predicted_start_date"):
                    try:
                        gated_starts.append(datetime.strptime(wi["predicted_start_date"], "%Y-%m-%d").date())
                    except Exception:
                        pass
            if gated_starts:
                earliest_start = min(gated_starts)
                req_by = DeadlineInferenceService.add_working_days(earliest_start, -WORKING_DAY_BUFFER)
                cd["required_by"] = req_by.isoformat()
                cd["required_by_rationale"] = f"Calculated as {WORKING_DAY_BUFFER} working days before earliest gated start {earliest_start.isoformat()}."
            else:
                cd["required_by"] = base_date.isoformat()
                cd["required_by_rationale"] = "No gated work items resolved; defaulted to project start date."

        logger.output("Total Commitments Consolidated", len(commitments))
        logger.output("Scope Delivery Obligations", len(scope_delivery_obligations))
        logger.output("Explicit Recurring Commitments", len(recurring_commitments))
        logger.output("Explicit Trigger-Based Commitments", len(trigger_commitments))
        logger.output("Milestone Acceptance Obligations", len(milestone_acceptance_obligations))
        logger.output("Customer Dependencies Identified", len(customer_dependencies))
        logger.output("Vendor Out-of-Scope Exclusions", len(vendor_exclusions))

        # ── 3. Simulate Schedule Expansion (Dry-Run Preview) ──
        logger.section("3. Schedule Expansion Simulation (Dry-Run)")
        simulated_occurrences = []

        for rec in recurring_commitments:
            cadence = rec["frequency"].lower()
            num_occurrences = 4 if cadence == "monthly" else (14 if cadence == "weekly" else 2)

            for occ_num in range(1, num_occurrences + 1):
                if cadence == "weekly":
                    occ_date = base_date + timedelta(weeks=occ_num)
                    label = f"Week {occ_num}"
                elif cadence == "monthly":
                    occ_date = base_date + timedelta(days=30 * occ_num)
                    label = f"Month {occ_num}"
                elif cadence == "quarterly":
                    occ_date = base_date + timedelta(days=90 * occ_num)
                    label = f"Q{occ_num}"
                else:
                    occ_date = base_date + timedelta(days=14 * occ_num)
                    label = f"Occurrence {occ_num}"

                simulated_occurrences.append({
                    "parent_candidate_id": rec["candidate_id"],
                    "commitment_name": rec["commitment_name"],
                    "cadence": cadence,
                    "occurrence_number": occ_num,
                    "occurrence_label": label,
                    "planned_date": occ_date.isoformat(),
                    "status": "PROPOSED",
                })

        logger.output("Total Proposed Occurrences Simulated", len(simulated_occurrences))

        # ── 4. Construct Step 9 Artifact ──
        total_duration = time.time() - t_start
        status = "PASSED" if commitments else "PASSED_WITH_WARNINGS"

        rec_count = len(recurring_commitments)
        one_time_count = len(scope_delivery_obligations)
        trigger_count = len(trigger_commitments)
        ms_acc_count = len(milestone_acceptance_obligations)

        step9_artifact = {
            "run_id": run_id,
            "timestamp_utc": datetime.now(timezone.utc).isoformat(),
            "project_id": project_id,
            "document_id": document_id,
            "document_name": doc_name,
            "step_name": "Step 9: Commitments, Dependencies, and Risk Analysis",
            "status": status,
            "execution_mode": mode,
            "safety_confirmations": {
                "production_baseline_mutation_disabled": True,
                "production_recurring_writes_disabled": True,
                "actual_writes_performed": 0,
                "step_10_disabled": False,
                "execution_enabled": True,
                "read_only": True,
                "production_writes_enabled": False,
            },
            "consolidated_commitments": commitments,
            "scope_delivery_obligations": scope_delivery_obligations,
            "recurring_commitments": recurring_commitments,
            "trigger_based_commitments": trigger_commitments,
            "milestone_acceptance_obligations": milestone_acceptance_obligations,
            "customer_dependencies": customer_dependencies,
            "client_responsibilities": client_responsibilities,
            "contract_period_records": contract_period_records,
            "governance_roles": governance_roles,
            "feature_descriptions": feature_descriptions,
            "vendor_out_of_scope_exclusions": vendor_exclusions,
            "recurring_commitments_preview": recurring_commitments,
            "simulated_occurrences_preview": simulated_occurrences,
            "summary": {
                "total_commitments": len(commitments),
                "scope_delivery_count": one_time_count,
                "recurring_count": rec_count,
                "one_time_count": one_time_count,
                "trigger_based_count": trigger_count,
                "milestone_acceptance_count": ms_acc_count,
                "customer_dependencies_count": len(customer_dependencies),
                "client_responsibilities_count": len(client_responsibilities),
                "contract_period_records_count": len(contract_period_records),
                "governance_roles_count": len(governance_roles),
                "vendor_exclusions_count": len(vendor_exclusions),
                "simulated_occurrences_count": len(simulated_occurrences),
                "actual_writes": 0,
                "duration_seconds": round(total_duration, 2),
            },
        }

        # Write Artifact Files
        json_run_path = os.path.join(logs_dir, f"step9_recurring_p{project_id}_d{document_id}_{timestamp_str}.json")
        json_active_path = os.path.join(logs_dir, f"step9_recurring_p{project_id}_d{document_id}.json")

        if os.getenv("ENABLE_DISK_ARTIFACTS", "true").lower() in ("true", "1", "yes"):
            with open(json_run_path, "w", encoding="utf-8") as f:
                json.dump(step9_artifact, f, indent=2, default=str)
            with open(json_active_path, "w", encoding="utf-8") as f:
                json.dump(step9_artifact, f, indent=2, default=str)

        logger.section("Step 9 Summary")
        logger.output("Execution Status", status)
        logger.output("Total Commitments Consolidated", len(commitments))
        logger.output("Customer Dependencies", len(customer_dependencies))
        logger.output("Vendor Out-of-Scope Exclusions", len(vendor_exclusions))
        logger.output("Actual DB Writes", 0)
        logger.output("Execution Duration (s)", round(total_duration, 2))
        logger.banner("STEP 9 COMPLETE — ADVANCING TO STEP 10 FINAL EL SYNTHESIS")

        return {
            "status": status,
            "step9_artifact": step9_artifact,
            "step9_run_json_path": json_run_path,
            "step9_active_json_path": json_active_path,
            "step9_log_path": logger.run_log_path,
        }
