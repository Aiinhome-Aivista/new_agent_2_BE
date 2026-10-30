# pyrefly: ignore [missing-import]
import mysql.connector
from typing import List, Dict, Any, Optional

class BaselineRepository:
    @staticmethod
    def get_document(db: mysql.connector.connection.MySQLConnection, document_id: int, project_id: int) -> Optional[Dict[str, Any]]:
        cursor = db.cursor(dictionary=True)
        cursor.execute("SELECT * FROM documents WHERE id = %s AND project_id = %s", (document_id, project_id))
        doc = cursor.fetchone()
        cursor.close()
        return doc

    @staticmethod
    def get_other_elifa_documents(db: mysql.connector.connection.MySQLConnection, project_id: int, exclude_document_id: int) -> List[Dict[str, Any]]:
        """
        Returns all EL/IFA documents for the project EXCEPT the given one.
        Used to purge the Vector DB chunks of previous, superseded EL/IFA
        versions so RAG does not return conflicting answers from old versions.
        The relational records are intentionally left intact for history.
        """
        cursor = db.cursor(dictionary=True)
        cursor.execute(
            "SELECT id, document_name, document_type FROM documents "
            "WHERE project_id = %s AND document_type IN ('EL', 'IFA') AND id != %s",
            (project_id, exclude_document_id)
        )
        rows = cursor.fetchall()
        cursor.close()
        return rows

    @staticmethod
    def get_draft_baseline(db: mysql.connector.connection.MySQLConnection, project_id: int) -> Optional[Dict[str, Any]]:
        cursor = db.cursor(dictionary=True)
        cursor.execute("SELECT id FROM scope_baselines WHERE project_id = %s AND status = 'DRAFT' ORDER BY id DESC LIMIT 1", (project_id,))
        baseline = cursor.fetchone()
        cursor.close()
        return baseline

    @staticmethod
    def update_baseline_source_document(db: mysql.connector.connection.MySQLConnection, baseline_id: int, document_id: int) -> None:
        cursor = db.cursor()
        cursor.execute("UPDATE scope_baselines SET source_document_id = %s WHERE id = %s", (document_id, baseline_id))
        cursor.close()

    @staticmethod
    def delete_stakeholders_by_project(db: mysql.connector.connection.MySQLConnection, project_id: int) -> None:
        cursor = db.cursor()
        cursor.execute("DELETE FROM stakeholders WHERE project_id = %s", (project_id,))
        cursor.close()

    @staticmethod
    def delete_scope_items_by_baseline(db: mysql.connector.connection.MySQLConnection, baseline_id: int) -> None:
        cursor = db.cursor()
        cursor.execute("DELETE FROM scope_items WHERE baseline_id = %s", (baseline_id,))
        cursor.close()

    @staticmethod
    def delete_deliverables_by_baseline(db: mysql.connector.connection.MySQLConnection, baseline_id: int) -> None:
        pass

    @staticmethod
    def get_max_baseline_version(db: mysql.connector.connection.MySQLConnection, project_id: int) -> int:
        cursor = db.cursor(dictionary=True)
        cursor.execute("SELECT MAX(version) as max_v FROM scope_baselines WHERE project_id = %s", (project_id,))
        row = cursor.fetchone()
        cursor.close()
        return (row["max_v"] or 0) if (row and "max_v" in row) else 0

    @staticmethod
    def create_baseline(db: mysql.connector.connection.MySQLConnection, project_id: int, version: int, document_id: int, status: str = 'DRAFT') -> int:
        cursor = db.cursor()
        cursor.execute("INSERT INTO scope_baselines (project_id, status, version, source_document_id) VALUES (%s, %s, %s, %s)", (project_id, status, version, document_id))
        baseline_id = cursor.lastrowid
        cursor.close()
        return baseline_id

    @staticmethod
    def get_latest_approved_baseline(db: mysql.connector.connection.MySQLConnection, project_id: int) -> Optional[Dict[str, Any]]:
        cursor = db.cursor(dictionary=True)
        cursor.execute("SELECT id FROM scope_baselines WHERE project_id = %s AND status = 'APPROVED' ORDER BY id DESC LIMIT 1", (project_id,))
        baseline = cursor.fetchone()
        cursor.close()
        return baseline

    @staticmethod
    def copy_scope_items(db: mysql.connector.connection.MySQLConnection, source_baseline_id: int, target_baseline_id: int) -> None:
        cursor = db.cursor()
        cursor.execute("""
            INSERT INTO scope_items (baseline_id, project_id, name, scope_item_normalized, description, scope_type, source_document_id, source_page, source_section, evidence_text, confidence, deadline, deadline_original, deadline_normalized, milestone, milestone_normalized, deadline_text, extraction_confidence, extraction_method)
            SELECT %s, project_id, name, scope_item_normalized, description, scope_type, source_document_id, source_page, source_section, evidence_text, confidence, deadline, deadline_original, deadline_normalized, milestone, milestone_normalized, deadline_text, extraction_confidence, extraction_method
            FROM scope_items WHERE baseline_id = %s AND completion_status NOT IN ('COMPLETED', 'CANCELLED')
        """, (target_baseline_id, source_baseline_id))
        cursor.close()

    @staticmethod
    def copy_deliverables(db: mysql.connector.connection.MySQLConnection, source_baseline_id: int, target_baseline_id: int) -> None:
        pass


    @staticmethod
    def create_project_milestone(db, project_id, baseline_id, name, sequence, status='Planned', planned_date=None):
        cursor = db.cursor()
        cursor.execute(
            "INSERT INTO project_milestones (project_id, baseline_id, name, sequence, status, planned_date) VALUES (%s, %s, %s, %s, %s, %s)",
            (project_id, baseline_id, name, sequence, status, planned_date)
        )
        milestone_id = cursor.lastrowid
        cursor.close()
        return milestone_id

    @staticmethod
    def create_scope_milestone_mapping(db, scope_item_id, milestone_id, weight=1.0):
        cursor = db.cursor()
        cursor.execute(
            "INSERT INTO scope_milestone_mapping (scope_item_id, milestone_id, weight) VALUES (%s, %s, %s)",
            (scope_item_id, milestone_id, weight)
        )
        cursor.close()

    @staticmethod
    def get_scope_items_for_diff(db: mysql.connector.connection.MySQLConnection, baseline_id: int) -> List[Dict[str, Any]]:
        cursor = db.cursor(dictionary=True)
        cursor.execute("SELECT id, name, scope_type, milestone, deadline_text FROM scope_items WHERE baseline_id = %s", (baseline_id,))
        items = cursor.fetchall()
        cursor.close()
        return items

    @staticmethod
    def update_scope_item(db: mysql.connector.connection.MySQLConnection, item_id: int, description: str, scope_type: str, source_document_id: int, source_page: Optional[int], source_section: Optional[str], evidence_text: str, confidence: float, status_change_tag: Optional[str], deadline: Optional[str], milestone: Optional[str] = None, deadline_text: Optional[str] = None, extraction_confidence: Optional[float] = None, extraction_method: Optional[str] = None, scope_item_normalized: Optional[str] = None, milestone_normalized: Optional[str] = None, deadline_original: Optional[str] = None, deadline_normalized: Optional[str] = None, category: Optional[str] = None, completion_status: Optional[str] = None,
        # Planning / date-provenance fields (new — all optional for backward compat)
        estimated_start_date: Optional[str] = None, estimated_deadline: Optional[str] = None,
        date_provenance: Optional[str] = None, estimation_confidence: Optional[str] = None,
        estimation_basis: Optional[str] = None, plan_assumptions: Optional[str] = None,
        estimated_effort_low: Optional[float] = None, estimated_effort_likely: Optional[float] = None,
        estimated_effort_high: Optional[float] = None, baseline_approval_status: Optional[str] = None,
    ) -> None:
        valid_scope_types = {"IN_SCOPE", "OUT_OF_SCOPE", "UNCERTAIN"}
        valid_categories = {"FUNCTIONAL", "TECHNICAL", "DELIVERABLE", "OUT_OF_SCOPE", "MILESTONE"}
        valid_completion_statuses = {"ACTIVE", "COMPLETED", "CANCELLED"}

        scope_type = str(scope_type).upper().strip()
        if scope_type not in valid_scope_types:
            scope_type = "OUT_OF_SCOPE" if ("OUT" in scope_type or "RESPONSIBILITY" in scope_type or "ASSUMPTION" in scope_type) else "IN_SCOPE"

        category = str(category).upper().strip() if category else "FUNCTIONAL"
        if category not in valid_categories:
            category = "OUT_OF_SCOPE" if scope_type == "OUT_OF_SCOPE" else "FUNCTIONAL"

        if completion_status:
            completion_status = str(completion_status).upper().strip()
            if completion_status not in valid_completion_statuses:
                completion_status = "ACTIVE"

        source_section = str(source_section)[:250] if source_section else None
        status_change_tag = str(status_change_tag)[:250] if status_change_tag else None
        milestone = str(milestone)[:250] if milestone else None
        milestone_normalized = str(milestone_normalized)[:250] if milestone_normalized else None
        deadline_text = str(deadline_text)[:250] if deadline_text else None
        deadline_original = str(deadline_original)[:250] if deadline_original else None
        scope_item_normalized = str(scope_item_normalized)[:490] if scope_item_normalized else None
        extraction_method = str(extraction_method)[:50] if extraction_method else None

        cursor = db.cursor()
        sql = """UPDATE scope_items 
                 SET description = %s, scope_type = %s, source_document_id = %s, source_page = %s, 
                     source_section = %s, evidence_text = %s, confidence = %s, status_change_tag = %s, deadline = %s,
                     milestone = %s, deadline_text = %s, extraction_confidence = %s, extraction_method = %s,
                     scope_item_normalized = %s, milestone_normalized = %s, deadline_original = %s, deadline_normalized = %s, category = %s, completion_status = COALESCE(%s, completion_status),
                     estimated_start_date = COALESCE(%s, estimated_start_date),
                     estimated_deadline = COALESCE(%s, estimated_deadline),
                     date_provenance = COALESCE(%s, date_provenance),
                     estimation_confidence = COALESCE(%s, estimation_confidence),
                     estimation_basis = COALESCE(%s, estimation_basis),
                     plan_assumptions = COALESCE(%s, plan_assumptions),
                     estimated_effort_low = COALESCE(%s, estimated_effort_low),
                     estimated_effort_likely = COALESCE(%s, estimated_effort_likely),
                     estimated_effort_high = COALESCE(%s, estimated_effort_high),
                     baseline_approval_status = COALESCE(%s, baseline_approval_status)
                 WHERE id = %s"""
        cursor.execute(sql, (
            description, scope_type, source_document_id, source_page,
            source_section, evidence_text, confidence, status_change_tag, deadline,
            milestone, deadline_text, extraction_confidence, extraction_method,
            scope_item_normalized, milestone_normalized, deadline_original, deadline_normalized,
            category, completion_status,
            estimated_start_date, estimated_deadline, date_provenance, estimation_confidence,
            estimation_basis, plan_assumptions,
            estimated_effort_low, estimated_effort_likely, estimated_effort_high,
            baseline_approval_status,
            item_id
        ))
        cursor.close()

    @staticmethod
    def insert_scope_item_extracted(db: mysql.connector.connection.MySQLConnection, baseline_id: int, project_id: int, name: str, description: str, scope_type: str, source_document_id: int, source_page: Optional[int], source_section: Optional[str], evidence_text: str, confidence: float, deadline: Optional[str], milestone: Optional[str] = None, deadline_text: Optional[str] = None, extraction_confidence: Optional[float] = None, extraction_method: Optional[str] = None, scope_item_normalized: Optional[str] = None, milestone_normalized: Optional[str] = None, deadline_original: Optional[str] = None, deadline_normalized: Optional[str] = None, category: Optional[str] = None, completion_status: str = 'ACTIVE',
        # Planning / date-provenance fields (new — all optional for backward compat)
        estimated_start_date: Optional[str] = None, estimated_deadline: Optional[str] = None,
        date_provenance: Optional[str] = None, estimation_confidence: Optional[str] = None,
        estimation_basis: Optional[str] = None, plan_assumptions: Optional[str] = None,
        estimated_effort_low: Optional[float] = None, estimated_effort_likely: Optional[float] = None,
        estimated_effort_high: Optional[float] = None, baseline_approval_status: Optional[str] = 'PENDING_REVIEW',
    ) -> int:
        valid_scope_types = {"IN_SCOPE", "OUT_OF_SCOPE", "UNCERTAIN"}
        valid_categories = {"FUNCTIONAL", "TECHNICAL", "DELIVERABLE", "OUT_OF_SCOPE", "MILESTONE"}
        valid_completion_statuses = {"ACTIVE", "COMPLETED", "CANCELLED"}

        scope_type = str(scope_type).upper().strip()
        if scope_type not in valid_scope_types:
            scope_type = "OUT_OF_SCOPE" if ("OUT" in scope_type or "RESPONSIBILITY" in scope_type or "ASSUMPTION" in scope_type) else "IN_SCOPE"

        category = str(category).upper().strip() if category else "FUNCTIONAL"
        if category not in valid_categories:
            category = "OUT_OF_SCOPE" if scope_type == "OUT_OF_SCOPE" else "FUNCTIONAL"

        completion_status = str(completion_status).upper().strip() if completion_status else "ACTIVE"
        if completion_status not in valid_completion_statuses:
            completion_status = "ACTIVE"

        name = str(name)[:490] if name else "Unknown"
        scope_item_normalized = str(scope_item_normalized)[:490] if scope_item_normalized else None
        source_section = str(source_section)[:250] if source_section else None
        milestone = str(milestone)[:250] if milestone else None
        milestone_normalized = str(milestone_normalized)[:250] if milestone_normalized else None
        deadline_text = str(deadline_text)[:250] if deadline_text else None
        deadline_original = str(deadline_original)[:250] if deadline_original else None
        extraction_method = str(extraction_method)[:50] if extraction_method else None

        cursor = db.cursor()
        sql = """INSERT INTO scope_items 
                 (baseline_id, project_id, name, scope_item_normalized, description, scope_type, source_document_id, source_page, source_section, evidence_text, confidence, deadline, deadline_original, deadline_normalized, milestone, milestone_normalized, deadline_text, extraction_confidence, extraction_method, category, completion_status,
                  estimated_start_date, estimated_deadline, date_provenance, estimation_confidence, estimation_basis, plan_assumptions, estimated_effort_low, estimated_effort_likely, estimated_effort_high, baseline_approval_status)
                 VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"""
        cursor.execute(sql, (
            baseline_id, project_id, name, scope_item_normalized, description,
            scope_type, source_document_id, source_page,
            source_section, evidence_text, confidence, deadline, deadline_original, deadline_normalized,
            milestone, milestone_normalized, deadline_text, extraction_confidence, extraction_method, category, completion_status,
            estimated_start_date, estimated_deadline, date_provenance, estimation_confidence,
            estimation_basis, plan_assumptions,
            estimated_effort_low, estimated_effort_likely, estimated_effort_high, baseline_approval_status
        ))
        item_id = cursor.lastrowid
        cursor.close()
        return item_id

    @staticmethod
    def get_deliverables_for_diff(db: mysql.connector.connection.MySQLConnection, baseline_id: int) -> List[Dict[str, Any]]:
        return []

    @staticmethod
    def update_deliverable(db: mysql.connector.connection.MySQLConnection, item_id: int, description: str, deadline: Optional[str], owner: Optional[str], source_document_id: int) -> None:
        pass

    @staticmethod
    def insert_deliverable(db: mysql.connector.connection.MySQLConnection, baseline_id: int, project_id: int, name: str, description: str, deadline: Optional[str], owner: Optional[str], source_document_id: int) -> None:
        pass

    @staticmethod
    def insert_stakeholder(db: mysql.connector.connection.MySQLConnection, project_id: int, name: str, email: Optional[str], role: Optional[str], responsibility: Optional[str]) -> None:
        cursor = db.cursor()
        sql = """INSERT INTO stakeholders (project_id, name, email, role, responsibility)
                 VALUES (%s, %s, %s, %s, %s)"""
        cursor.execute(sql, (
            project_id, name, email, role, responsibility
        ))
        cursor.close()

    @staticmethod
    def update_project_monitoring_status(db: mysql.connector.connection.MySQLConnection, project_id: int, status: str) -> None:
        cursor = db.cursor()
        cursor.execute("UPDATE projects SET monitoring_status = %s WHERE id = %s", (status, project_id))
        cursor.close()

    @staticmethod
    def get_latest_baseline_details(db: mysql.connector.connection.MySQLConnection, project_id: int) -> Optional[Dict[str, Any]]:
        cursor = db.cursor(dictionary=True)
        cursor.execute("SELECT * FROM scope_baselines WHERE project_id = %s ORDER BY id DESC LIMIT 1", (project_id,))
        baseline = cursor.fetchone()
        if not baseline:
            cursor.close()
            return None
        cursor.execute("SELECT * FROM scope_items WHERE baseline_id = %s ORDER BY id ASC", (baseline["id"],))
        items = cursor.fetchall()
        
        # Batch load latest progress to avoid N+1 queries (233 queries reduced to 1)
        if items:
            item_ids = [it["id"] for it in items]
            format_strings = ','.join(['%s'] * len(item_ids))
            cursor.execute(f"""
                SELECT dp.*, psc.label as status_label, doc.document_name, doc.document_type
                FROM deliverable_progress dp
                LEFT JOIN progress_status_config psc ON dp.status_code = psc.status_code
                LEFT JOIN documents doc ON dp.source_document_id = doc.id
                WHERE dp.scope_item_id IN ({format_strings})
                ORDER BY dp.id DESC
            """, tuple(item_ids))
            all_dp = cursor.fetchall()
            dp_by_item = {}
            for dp in all_dp:
                sid = dp.get("scope_item_id")
                if sid not in dp_by_item:
                    dp_by_item[sid] = dp
            import json
            for it in items:
                it["latest_progress"] = dp_by_item.get(it["id"])
                it["recurring_occurrences"] = []
                pa = it.get("plan_assumptions")
                if pa and isinstance(pa, str):
                    try:
                        it["parsed_plan_assumptions"] = json.loads(pa)
                    except Exception:
                        it["parsed_plan_assumptions"] = {}
                elif isinstance(pa, dict):
                    it["parsed_plan_assumptions"] = pa
                else:
                    it["parsed_plan_assumptions"] = {}
        deliverables = []
        
        cursor.execute("""
            SELECT pm.*, 
                   GROUP_CONCAT(DISTINCT md.child_milestone_id) as blocking_ids, 
                   GROUP_CONCAT(DISTINCT md2.parent_milestone_id) as blocked_by_ids,
                   GROUP_CONCAT(DISTINCT CONCAT(succ.name, '||', md.dependency_type) SEPARATOR ';;') as successor_details,
                   GROUP_CONCAT(DISTINCT CONCAT(pred.name, '||', md2.dependency_type) SEPARATOR ';;') as predecessor_details
            FROM project_milestones pm
            LEFT JOIN milestone_dependencies md ON pm.id = md.parent_milestone_id
            LEFT JOIN project_milestones succ ON md.child_milestone_id = succ.id
            LEFT JOIN milestone_dependencies md2 ON pm.id = md2.child_milestone_id
            LEFT JOIN project_milestones pred ON md2.parent_milestone_id = pred.id
            WHERE pm.project_id = %s AND pm.baseline_id = %s
            GROUP BY pm.id
        """, (project_id, baseline["id"]))
        milestones = cursor.fetchall()
        
        cursor.close()
        
        # Separate parent recurring items from their child occurrences in the flat list
        # (child occurrences are already attached to their parent via recurring_occurrences)
        top_level_items = [i for i in items if not i.get("parent_scope_item_id")]
        
        baseline["scope_items"] = top_level_items
        baseline["deliverables"] = deliverables
        baseline["milestones"] = milestones
        
        # ── Unified Schema v2.0 fields ──
        baseline["schema_version"] = "2.0.0"
        baseline["field_semantics"] = {
            "all_records": "Complete canonical scope items ledger (reconciled)",
            "work_items": "Eligible capacity-consuming work items with AI-predicted deadlines",
            "out_of_scope_records": "Contractually excluded items with change control requirement",
            "pending_review_records": "Ambiguous/uncertain items gated for review"
        }
        baseline["all_records"] = top_level_items
        baseline["work_items"] = [
            it for it in top_level_items 
            if it.get("parsed_plan_assumptions", {}).get("work_item_eligible") is True
            or (it.get("scope_type") == "IN_SCOPE" and (it.get("estimated_deadline") is not None or it.get("deadline") is not None) and it.get("category") != "MILESTONE")
        ]
        baseline["out_of_scope_records"] = [
            it for it in top_level_items if it.get("scope_type") == "OUT_OF_SCOPE"
        ]
        baseline["pending_review_records"] = [
            it for it in top_level_items if it.get("scope_type") == "UNCERTAIN"
        ]
        baseline["governance_obligations"] = [
            it for it in top_level_items
            if it.get("scope_type") == "IN_SCOPE" and (
                it.get("category") == "GOVERNANCE" or
                it.get("parsed_plan_assumptions", {}).get("record_type") in ("Governance Obligation", "Vendor Responsibility") or
                any(k in (it.get("name") or "").lower() for k in ("steering", "committee", "powers to decide", "fee negotiation"))
            )
        ]
        baseline["contextual_records"] = [
            it for it in top_level_items
            if it.get("scope_type") == "IN_SCOPE"
            and it not in baseline["work_items"]
            and it not in baseline["governance_obligations"]
            and it.get("category") != "MILESTONE"
        ]
        baseline["payment_milestones"] = [
            m for m in milestones 
            if any(k in (m.get("name") or "").lower() for k in ("60%", "30%", "10%", "implement", "sign-off", "hypercare", "payment"))
        ]
        baseline["project_phases"] = [
            m for m in milestones if m not in baseline["payment_milestones"]
        ]

        # Enrich missing planning registers from authoritative final artifact / Step 9 logs
        try:
            from services.final_baseline_service import FinalBaselineService
            doc_id = baseline.get("source_document_id")
            art_res = FinalBaselineService.load_validated_final_baseline(project_id, doc_id)
            art = art_res.get("artifact", {}) if art_res.get("status") in ("READY", "SUCCESS") else {}
            if art.get("governance_obligations"):
                baseline["governance_obligations"] = art.get("governance_obligations")
            if art.get("contextual_records"):
                baseline["contextual_records"] = art.get("contextual_records")
            if art.get("payment_milestones"):
                baseline["payment_milestones"] = art.get("payment_milestones")
            if art.get("all_records"):
                baseline["all_records"] = art.get("all_records")
            baseline["customer_dependencies"] = art.get("customer_dependencies") or []
            baseline["client_responsibilities"] = art.get("client_responsibilities") or []
            baseline["commitments"] = art.get("commitments") or []
            baseline["contractual_risks"] = art.get("contractual_risks") or []
            baseline["document_quality_findings"] = art.get("document_quality_findings") or []
            baseline["schedule_warnings"] = art.get("schedule_warnings") or []
        except Exception:
            baseline["customer_dependencies"] = []
            baseline["client_responsibilities"] = []
            baseline["commitments"] = []
            baseline["contractual_risks"] = []
            baseline["document_quality_findings"] = []
            baseline["schedule_warnings"] = []

        return baseline

    @staticmethod
    def get_all_baseline_versions(db: mysql.connector.connection.MySQLConnection, project_id: int) -> List[Dict[str, Any]]:
        cursor = db.cursor(dictionary=True)
        cursor.execute("""
            SELECT sb.*, d.document_name as document_name 
            FROM scope_baselines sb
            LEFT JOIN documents d ON sb.source_document_id = d.id
            WHERE sb.project_id = %s 
            ORDER BY sb.version DESC
        """, (project_id,))
        baselines = cursor.fetchall()
        for b in baselines:
            cursor.execute("SELECT * FROM scope_items WHERE baseline_id = %s", (b["id"],))
            b["scope_items"] = cursor.fetchall()
            b["deliverables"] = []
        cursor.close()
        return baselines

    @staticmethod
    def approve_baseline(db: mysql.connector.connection.MySQLConnection, baseline_id: int, user_id: int, allow_override: bool = False) -> None:
        cursor = db.cursor(dictionary=True)
        cursor.execute("SELECT COUNT(*) as count FROM scope_items WHERE baseline_id = %s AND scope_type = 'UNCERTAIN'", (baseline_id,))
        row = cursor.fetchone()
        uncertain_count = row.get("count", 0) if row else 0
        if uncertain_count > 0 and not allow_override:
            cursor.close()
            raise ValueError(f"Baseline approval blocked: {uncertain_count} scope item(s) remain PENDING_REVIEW. Review gates must be resolved before approval.")
        cursor.execute("UPDATE scope_baselines SET status = 'APPROVED', approved_by = %s, approved_at = NOW() WHERE id = %s", (user_id, baseline_id))
        cursor.close()

    @staticmethod
    def get_latest_baseline(db: mysql.connector.connection.MySQLConnection, project_id: int) -> Optional[Dict[str, Any]]:
        cursor = db.cursor(dictionary=True)
        cursor.execute("SELECT id, source_document_id FROM scope_baselines WHERE project_id = %s ORDER BY version DESC LIMIT 1", (project_id,))
        baseline = cursor.fetchone()
        cursor.close()
        return baseline

    @staticmethod
    def create_simple_baseline(db: mysql.connector.connection.MySQLConnection, project_id: int, status: str = 'DRAFT') -> int:
        cursor = db.cursor()
        cursor.execute("INSERT INTO scope_baselines (project_id, status) VALUES (%s, %s)", (project_id, status))
        baseline_id = cursor.lastrowid
        cursor.close()
        return baseline_id

    @staticmethod
    def create_scope_item(db: mysql.connector.connection.MySQLConnection, baseline_id: int, project_id: int, name: str, description: str, scope_type: str, evidence_text: str, confidence: float, source_document_id: Optional[int] = None, deadline: Optional[str] = None, milestone: Optional[str] = None, deadline_text: Optional[str] = None, scope_item_normalized: Optional[str] = None, milestone_normalized: Optional[str] = None, deadline_original: Optional[str] = None, deadline_normalized: Optional[str] = None) -> int:
        cursor = db.cursor()
        sql = """
            INSERT INTO scope_items (baseline_id, project_id, name, scope_item_normalized, description, scope_type, evidence_text, confidence, source_document_id, deadline, deadline_original, deadline_normalized, milestone, milestone_normalized, deadline_text)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """
        cursor.execute(sql, (baseline_id, project_id, name, scope_item_normalized, description, scope_type, evidence_text, confidence, source_document_id, deadline, deadline_original, deadline_normalized, milestone, milestone_normalized, deadline_text))
        item_id = cursor.lastrowid
        cursor.close()
        return item_id


    @staticmethod
    def create_project_milestone(db, project_id, baseline_id, name, sequence, status='Planned', planned_date=None):
        cursor = db.cursor()
        cursor.execute(
            "INSERT INTO project_milestones (project_id, baseline_id, name, sequence, status, planned_date) VALUES (%s, %s, %s, %s, %s, %s)",
            (project_id, baseline_id, name, sequence, status, planned_date)
        )
        milestone_id = cursor.lastrowid
        cursor.close()
        return milestone_id

    @staticmethod
    def create_scope_milestone_mapping(db, scope_item_id, milestone_id, weight=1.0):
        cursor = db.cursor()
        cursor.execute(
            "INSERT INTO scope_milestone_mapping (scope_item_id, milestone_id, weight) VALUES (%s, %s, %s)",
            (scope_item_id, milestone_id, weight)
        )
        cursor.close()

    @staticmethod
    def get_scope_item(db: mysql.connector.connection.MySQLConnection, item_id: int) -> Optional[Dict[str, Any]]:
        cursor = db.cursor(dictionary=True)
        cursor.execute("SELECT * FROM scope_items WHERE id = %s", (item_id,))
        item = cursor.fetchone()
        cursor.close()
        return item

    @staticmethod
    def check_scope_item_exists_in_project(db: mysql.connector.connection.MySQLConnection, item_id: int, project_id: int) -> Optional[Dict[str, Any]]:
        cursor = db.cursor(dictionary=True)
        cursor.execute("SELECT id FROM scope_items WHERE id = %s AND project_id = %s", (item_id, project_id))
        item = cursor.fetchone()
        cursor.close()
        return item

    @staticmethod
    def delete_scope_item(db: mysql.connector.connection.MySQLConnection, item_id: int, project_id: int) -> None:
        cursor = db.cursor()
        cursor.execute("DELETE FROM scope_items WHERE id = %s AND project_id = %s", (item_id, project_id))
        cursor.close()

    @staticmethod
    def delete_baseline(db: mysql.connector.connection.MySQLConnection, baseline_id: int, project_id: int) -> None:
        cursor = db.cursor()
        cursor.execute("DELETE FROM scope_items WHERE baseline_id = %s AND project_id = %s", (baseline_id, project_id))
        sql = "DELETE FROM scope_baselines WHERE id = %s AND project_id = %s AND status = 'DRAFT'"
        cursor.execute(sql, (baseline_id, project_id))
        cursor.close()

    @staticmethod
    def update_scope_item_completion(db: mysql.connector.connection.MySQLConnection, item_id: int, project_id: int, completion_status: str) -> None:
        cursor = db.cursor()
        cursor.execute(
            "UPDATE scope_items SET completion_status = %s WHERE id = %s AND project_id = %s",
            (completion_status, item_id, project_id)
        )
        cursor.close()

    @staticmethod
    def insert_deliverable_progress(
        db: mysql.connector.connection.MySQLConnection, 
        project_id: int, 
        scope_item_id: int, 
        source_document_id: int, 
        risk_evaluation_id: int,
        baseline_version: int,
        status_code: str, 
        progress_percentage: Optional[int], 
        execution_summary: str,
        dependencies: list,
        resolved_items: list,
        confidence: float,
        evidence_text: str
    ) -> None:
        import json
        cursor = db.cursor()
        import datetime
        import re
        _normalize = lambda x: re.sub(r'[^\w\s]', '', (x or '').lower().strip())
        
        # Dependency Lifecycle Merging (Execution Prerequisite State Manager)
        # Fetch the previous dependencies for this scope_item
        cursor.execute(
            "SELECT dependencies FROM deliverable_progress WHERE scope_item_id = %s ORDER BY id DESC LIMIT 1",
            (scope_item_id,)
        )
        prev_row = cursor.fetchone()
        
        previous_deps_map = {}
        if prev_row and prev_row[0]:
            try:
                prev_deps = json.loads(prev_row[0])
                for d in prev_deps:
                    if isinstance(d, dict) and "name" in d:
                        previous_deps_map[_normalize(d["name"])] = d
                    elif isinstance(d, str):
                        # Handle legacy string format
                        previous_deps_map[_normalize(d)] = {
                            "name": d,
                            "status": "PENDING",
                            "last_updated": datetime.datetime.now().isoformat(),
                            "evidence": "",
                            "resolved_by_document": None
                        }
            except Exception:
                pass
                
        # Build new dependencies state
        current_deps_map = {}
        
        # 1. Start with everything we just extracted (which the LLM thinks are pending/blocking right now)
        for d in dependencies:
            name = d if isinstance(d, str) else d.get("name", str(d))
            norm_name = _normalize(name)
            current_deps_map[norm_name] = {
                "name": name,
                "status": "PENDING",
                "last_updated": datetime.datetime.now().isoformat(),
                "evidence": "Extracted as execution prerequisite",
                "resolved_by_document": None
            }
            
        # 2. Inherit previous dependencies that haven't explicitly transitioned
        for norm_name, prev_obj in previous_deps_map.items():
            if norm_name not in current_deps_map:
                current_deps_map[norm_name] = prev_obj
                
        # 3. Apply Explicit Resolutions
        try:
            from api.routes.baseline import _is_title_match
        except Exception:
            _is_title_match = None

        for r_item in resolved_items:
            r_name = r_item.get("name", "")
            r_norm = _normalize(r_name)
            # Find matching prerequisite
            for d_norm in list(current_deps_map.keys()):
                d_orig_name = current_deps_map[d_norm].get("name", "")
                if (r_norm in d_norm or d_norm in r_norm) or (_is_title_match and _is_title_match(r_name, d_orig_name)):
                    current_deps_map[d_norm]["status"] = "COMPLETED"
                    current_deps_map[d_norm]["last_updated"] = datetime.datetime.now().isoformat()
                    current_deps_map[d_norm]["evidence"] = r_item.get("resolution_evidence", "Resolved")
                    current_deps_map[d_norm]["resolved_by_document"] = source_document_id

        # 4. If deliverable is COMPLETED, mark all its prerequisites as SATISFIED / COMPLETED
        if str(status_code).upper() == "COMPLETED" or (progress_percentage is not None and float(progress_percentage) >= 100):
            for d_norm in list(current_deps_map.keys()):
                current_deps_map[d_norm]["status"] = "COMPLETED"
                current_deps_map[d_norm]["last_updated"] = datetime.datetime.now().isoformat()
                current_deps_map[d_norm]["evidence"] = execution_summary or "Deliverable completed"
                current_deps_map[d_norm]["resolved_by_document"] = source_document_id

        final_dependencies = list(current_deps_map.values())

        sql = """
            INSERT INTO deliverable_progress (
                project_id, scope_item_id, source_document_id, risk_evaluation_id, baseline_version,
                status_code, progress_percentage, execution_summary, dependencies, confidence, evidence_text
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """
        cursor.execute(sql, (
            project_id, scope_item_id, source_document_id, risk_evaluation_id, baseline_version,
            status_code, progress_percentage, execution_summary, json.dumps(final_dependencies), 
            confidence, evidence_text
        ))

        # Synchronize scope_items and project_milestones if deliverable is completed
        if str(status_code).upper() == "COMPLETED" or (progress_percentage is not None and float(progress_percentage) >= 100):
            try:
                cursor.execute(
                    "UPDATE scope_items SET completion_status = 'COMPLETED' WHERE id = %s AND project_id = %s",
                    (scope_item_id, project_id)
                )
                cursor.execute("""
                    UPDATE project_milestones pm
                    JOIN scope_items si ON (
                        LOWER(TRIM(pm.name)) = LOWER(TRIM(si.name)) 
                        OR LOWER(TRIM(pm.name)) = LOWER(TRIM(COALESCE(si.milestone, '')))
                        OR LOWER(TRIM(pm.name)) = LOWER(TRIM(COALESCE(si.milestone_normalized, '')))
                    )
                    SET pm.status = 'Completed'
                    WHERE si.id = %s AND pm.project_id = %s
                """, (scope_item_id, project_id))
            except Exception as e:
                print(f"  [BaselineRepo] Warning: Failed to sync scope_items/milestones: {e}")

        cursor.close()

    @staticmethod
    def update_scope_item_details(
        db: mysql.connector.connection.MySQLConnection,
        item_id: int,
        project_id: int,
        completion_status: Optional[str] = None,
        deadline: Optional[str] = None
    ) -> None:
        cursor = db.cursor()
        updates = []
        params = []
        if completion_status is not None:
            updates.append("completion_status = %s")
            params.append(completion_status)
        if deadline is not None:
            updates.append("deadline = %s")
            params.append(deadline)
        if updates:
            params.extend([item_id, project_id])
            sql = f"UPDATE scope_items SET {', '.join(updates)} WHERE id = %s AND project_id = %s"
            cursor.execute(sql, tuple(params))
        cursor.close()

    # -----------------------------------------------------------------------
    # RECURRING DELIVERABLE HELPERS
    # -----------------------------------------------------------------------

    @staticmethod
    def get_recurring_parents(db: mysql.connector.connection.MySQLConnection, baseline_id: int) -> List[Dict[str, Any]]:
        """Return all recurring parent scope items for a baseline."""
        cursor = db.cursor(dictionary=True)
        cursor.execute(
            """SELECT * FROM scope_items
               WHERE baseline_id = %s AND is_recurring = 1 AND parent_scope_item_id IS NULL""",
            (baseline_id,)
        )
        items = cursor.fetchall()
        cursor.close()
        return items

    @staticmethod
    def get_recurring_children(db: mysql.connector.connection.MySQLConnection, parent_id: int) -> List[Dict[str, Any]]:
        """Return all generated occurrence rows for a given parent."""
        cursor = db.cursor(dictionary=True)
        cursor.execute(
            "SELECT * FROM scope_items WHERE parent_scope_item_id = %s ORDER BY deadline ASC",
            (parent_id,)
        )
        items = cursor.fetchall()
        cursor.close()
        return items

    @staticmethod
    def delete_future_occurrences_after_date(
        db: mysql.connector.connection.MySQLConnection,
        parent_id: int,
        after_date: str
    ) -> int:
        """Hard-delete future occurrences that have NO progress recorded (for shortened projects)."""
        cursor = db.cursor()
        cursor.execute(
            """DELETE si FROM scope_items si
               LEFT JOIN deliverable_progress dp ON dp.scope_item_id = si.id
               WHERE si.parent_scope_item_id = %s
                 AND si.deadline > %s
                 AND dp.id IS NULL""",
            (parent_id, after_date)
        )
        affected = cursor.rowcount
        cursor.close()
        return affected

    @staticmethod
    def insert_recurring_occurrence(
        db,
        baseline_id: int,
        project_id: int,
        parent_scope_item_id: int,
        name: str,
        occurrence_label: str,
        deadline: str,
        scope_type: str,
        category: str,
        recurrence_cadence: str,
    ) -> int:
        """
        Insert one child occurrence row for a recurring parent scope item.
        Each occurrence represents one period's commitment (e.g. 'CSI – Month 1').
        Generic: works for any cadence and any recurring item.
        Returns the new row's ID.
        """
        try:
            cursor = db.cursor()
            cursor.execute("""
                INSERT INTO scope_items
                (baseline_id, project_id, name, scope_type, category,
                 deadline, deadline_normalized, is_recurring, recurrence_cadence,
                 parent_scope_item_id, completion_status, confidence)
                VALUES (%s, %s, %s, %s, %s, %s, %s, 0, %s, %s, 'ACTIVE', 1.0)
            """, (
                baseline_id, project_id,
                f"{name} \u2013 {occurrence_label}",  # e.g. "CSI Process – Month 1"
                scope_type, category,
                deadline, deadline,
                recurrence_cadence,
                parent_scope_item_id
            ))
            occ_id = cursor.lastrowid
            cursor.close()
            print(f"  [RecurringService] Created occurrence: '{name} \u2013 {occurrence_label}' "
                  f"(due={deadline}, parent={parent_scope_item_id})")
            return occ_id
        except Exception as e:
            print(f"  [RecurringService] Warning: insert_recurring_occurrence failed: {e}")
            return 0
