"""
Tests for Observability, Tracing, Structured Logging, and Evaluation Pipeline.
"""

import pytest
from core.structured_logger import agent_logger, ctx_trace_id, ctx_project_id
from services.telemetry_service import telemetry
from services.eval_service import AgentEvaluationService, EvaluationJudgeReport


def test_structured_logger_context():
    """Verify context variable binding in structured logger."""
    agent_logger.bind(trace_id="test_trc_999", project_id=10, document_id=20)
    assert ctx_trace_id.get() == "test_trc_999"
    assert ctx_project_id.get() == 10


def test_telemetry_trace_and_spans():
    """Verify trace lifecycle and span collection in memory store."""
    trace = telemetry.start_trace(project_id=5, document_id=12)
    assert trace.trace_id.startswith("trace_")
    assert trace.project_id == 5

    with telemetry.span("node_step_1", {"items_count": 3}):
        pass

    with telemetry.span("node_step_2", {"model": "gemini"}):
        pass

    trace.finish(status="OK")
    assert trace.status == "OK"
    assert len(trace.spans) == 2
    assert trace.spans[0].name == "node_step_1"
    assert trace.spans[0].status == "OK"
    assert trace.spans[1].name == "node_step_2"

    # Verify retrieval from circular buffer
    stored = telemetry.trace_store.get(trace.trace_id)
    assert stored is not None
    assert stored.trace_id == trace.trace_id

    # Verify list filter
    traces_for_proj = telemetry.trace_store.list_traces(project_id=5)
    assert any(t["trace_id"] == trace.trace_id for t in traces_for_proj)


def test_eval_judge_report_schema():
    """Verify Pydantic evaluation judge report schema."""
    report = EvaluationJudgeReport(
        overall_score=88.5,
        verdict="PASS",
        faithfulness_score=0.95,
        coverage_score=0.85,
        severity_calibration_score=0.90,
        hallucinations=[],
        missed_elements=[],
        judge_critique="Evidence thoroughly supports the risk categorization.",
        latency_ms=120.5
    )
    data = report.model_dump()
    assert data["overall_score"] == 88.5
    assert data["verdict"] == "PASS"
    assert len(data["hallucinations"]) == 0
