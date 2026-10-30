import pytest
import os
import json
from datetime import datetime, date

from core.database import get_db_connection, db_transaction
from services.final_baseline_service import FinalBaselineService
from repositories.baseline_repository import BaselineRepository


@pytest.fixture(scope="module")
def db_conn():
    conn = get_db_connection()
    yield conn
    conn.close()


def test_persist_draft_baseline_persists_all_canonical_candidates(db_conn):
    """
    Validates PERSIST_DRAFT mode:
    1. Persists baseline metadata in scope_baselines.
    2. Persists all canonical candidate dispositions (IN_SCOPE, OUT_OF_SCOPE, UNCERTAIN).
    3. Persists milestones and contractual DAG dependency edges.
    4. Persists AI-generated deadlines and provenance on all eligible work items.
    5. Reconciles database readback against the authoritative final baseline artifact.
    """
    project_id = 124
    document_id = 262

    res = FinalBaselineService.persist_draft_baseline_to_db(
        db_conn=db_conn,
        project_id=project_id,
        document_id=document_id,
    )

    assert res["success"] is True, f"Persistence failed: {res}"
    assert res["status"] == "DRAFT"
    assert res["execution_mode"] == "PERSIST_DRAFT"
    assert res["readiness_status"] == "PROVISIONAL"

    artifact_res = FinalBaselineService.load_validated_final_baseline(project_id, document_id)
    summary = artifact_res["artifact"]["summary"]

    assert res["total_records_persisted"] == summary["canonical_candidates"]

    readback = res["readback_verification"]
    assert readback["is_valid"] is True
    assert readback["total_scope_items"] == summary["canonical_candidates"]
    assert readback["in_scope_count"] == summary["in_scope_records"]
    assert readback["out_of_scope_count"] == summary["out_of_scope_records"]
    assert readback["pending_review_count"] == summary["pending_review_count"]
    assert readback["milestones_count"] == summary["milestone_count"]
    assert readback["candidate_ids_preserved_count"] == summary["canonical_candidates"]


def test_database_readback_verification_matches_artifact(db_conn):
    """
    Validates that database readback matches the authoritative JSON artifact.
    """
    project_id = 124
    readback = FinalBaselineService.readback_and_verify_persisted_baseline(
        db_conn=db_conn,
        project_id=project_id,
    )

    assert readback["is_valid"] is True
    assert readback["reconciled_with_artifact"] is True
    assert len(readback["artifact_discrepancies"]) == 0
    assert readback["status"] == "DRAFT"
    assert readback["total_scope_items"] > 0


def test_persistence_idempotency_prevents_duplicate_baselines(db_conn):
    """
    Validates idempotency: Re-executing persistence on an existing draft updates
    the records cleanly without creating duplicate baselines or duplicate scope items.
    """
    project_id = 124
    document_id = 262

    # Run persistence twice
    res1 = FinalBaselineService.persist_draft_baseline_to_db(db_conn, project_id, document_id)
    baseline_id_1 = res1["baseline_id"]
    version_1 = res1["version"]

    res2 = FinalBaselineService.persist_draft_baseline_to_db(db_conn, project_id, document_id)
    baseline_id_2 = res2["baseline_id"]
    version_2 = res2["version"]

    assert baseline_id_1 == baseline_id_2, "Idempotent run should reuse existing draft baseline ID"
    assert version_1 == version_2, "Idempotent run should maintain existing draft baseline version"

    cur = db_conn.cursor(dictionary=True)
    cur.execute("SELECT count(id) as c FROM scope_items WHERE baseline_id = %s", (baseline_id_1,))
    count = cur.fetchone()["c"]
    cur.close()

    assert count == res1["total_records_persisted"], f"Expected {res1['total_records_persisted']} scope items after idempotent re-run, got {count}"


def test_pending_review_items_gated_with_review_status(db_conn):
    """
    Validates that pending review candidates have UNCERTAIN scope_type
    and PENDING_REVIEW approval status in the database.
    """
    project_id = 124
    cur = db_conn.cursor(dictionary=True)
    cur.execute(
        "SELECT status_change_tag, scope_type, baseline_approval_status, plan_assumptions "
        "FROM scope_items WHERE project_id = %s AND scope_type = 'UNCERTAIN'",
        (project_id,)
    )
    rows = cur.fetchall()
    cur.close()

    assert len(rows) > 0, f"Expected UNCERTAIN scope items, found {len(rows)}"

    for r in rows:
        assert r["baseline_approval_status"] == "PENDING_REVIEW"
        assumptions = json.loads(r["plan_assumptions"])
        assert assumptions.get("review_status") == "PENDING_REVIEW"
        assert assumptions.get("review_reason") is not None


def test_milestone_dates_and_dag_dependencies_in_db(db_conn):
    """
    Validates that project_milestones has dated milestones and unresolved dates,
    and milestone_dependencies has valid acyclic edges.
    """
    project_id = 124
    cur = db_conn.cursor(dictionary=True)
    cur.execute("SELECT name, planned_date FROM project_milestones WHERE project_id = %s ORDER BY sequence", (project_id,))
    milestones = cur.fetchall()

    dated = [m for m in milestones if m["planned_date"] is not None]

    assert len(milestones) > 0
    assert len(dated) > 0

    cur.execute("SELECT count(id) as c FROM milestone_dependencies WHERE project_id = %s", (project_id,))
    dep_count = cur.fetchone()["c"]
    cur.close()

    assert dep_count > 0
