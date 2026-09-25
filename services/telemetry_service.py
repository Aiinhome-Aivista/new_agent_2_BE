"""
OpenTelemetry-based Observability & Tracing Service for ACSE.

Provides:
- OpenTelemetry tracer integration (vendor-neutral, open-source CNCF standard).
- In-memory circular TraceStore to inspect spans, latencies, tokens, and errors
  directly via API or dashboard without requiring external dependencies.
- Optional OTLP export (Jaeger, Langfuse, SigNoz, OpenTelemetry Collector).
- Safe, non-intrusive decorators and context managers for agent graph nodes.
"""

import os
import time
import uuid
import threading
from collections import deque
from typing import Dict, List, Optional, Any
from contextlib import contextmanager
from core.structured_logger import agent_logger, ctx_trace_id, ctx_node_name

# OpenTelemetry imports
try:
    from opentelemetry import trace
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor, BatchSpanProcessor
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.trace import Status, StatusCode
    OTEL_AVAILABLE = True
except ImportError:
    OTEL_AVAILABLE = False


class TraceSpanRecord:
    """Represents a recorded span inside an execution trace."""
    def __init__(self, span_id: str, name: str, parent_id: Optional[str] = None):
        self.span_id = span_id
        self.name = name
        self.parent_id = parent_id
        self.start_time = time.time()
        self.end_time: Optional[float] = None
        self.duration_ms: float = 0.0
        self.status: str = "RUNNING"  # RUNNING, OK, ERROR
        self.attributes: Dict[str, Any] = {}
        self.error: Optional[str] = None

    def finish(self, status: str = "OK", error: Optional[str] = None):
        self.end_time = time.time()
        self.duration_ms = round((self.end_time - self.start_time) * 1000, 2)
        self.status = status
        self.error = error

    def to_dict(self) -> Dict[str, Any]:
        return {
            "span_id": self.span_id,
            "name": self.name,
            "parent_id": self.parent_id,
            "start_time": self.start_time,
            "end_time": self.end_time,
            "duration_ms": self.duration_ms,
            "status": self.status,
            "attributes": self.attributes,
            "error": self.error,
        }


class ExecutionTrace:
    """Represents a complete pipeline run (e.g. evaluate_document)."""
    def __init__(self, trace_id: str, project_id: Optional[int] = None, document_id: Optional[int] = None):
        self.trace_id = trace_id
        self.project_id = project_id
        self.document_id = document_id
        self.start_time = time.time()
        self.end_time: Optional[float] = None
        self.duration_ms: float = 0.0
        self.status: str = "RUNNING"
        self.spans: List[TraceSpanRecord] = []
        self.tokens_prompt: int = 0
        self.tokens_completion: int = 0
        self.total_tokens: int = 0
        self.metadata: Dict[str, Any] = {}
        self.error: Optional[str] = None

    def add_span(self, span: TraceSpanRecord):
        self.spans.append(span)

    def record_tokens(self, prompt_tokens: int = 0, completion_tokens: int = 0):
        self.tokens_prompt += prompt_tokens
        self.tokens_completion += completion_tokens
        self.total_tokens = self.tokens_prompt + self.tokens_completion

    def finish(self, status: str = "OK", error: Optional[str] = None):
        self.end_time = time.time()
        self.duration_ms = round((self.end_time - self.start_time) * 1000, 2)
        self.status = status
        self.error = error

    def to_dict(self) -> Dict[str, Any]:
        return {
            "trace_id": self.trace_id,
            "project_id": self.project_id,
            "document_id": self.document_id,
            "start_time": self.start_time,
            "end_time": self.end_time,
            "duration_ms": self.duration_ms,
            "status": self.status,
            "error": self.error,
            "token_usage": {
                "prompt_tokens": self.tokens_prompt,
                "completion_tokens": self.tokens_completion,
                "total_tokens": self.total_tokens
            },
            "spans_count": len(self.spans),
            "spans": [s.to_dict() for s in self.spans],
            "metadata": self.metadata
        }


class InMemoryTraceStore:
    """Thread-safe circular in-memory buffer storing recent traces."""
    def __init__(self, max_capacity: int = 200):
        self._capacity = max_capacity
        self._traces: deque[ExecutionTrace] = deque(maxlen=max_capacity)
        self._traces_by_id: Dict[str, ExecutionTrace] = {}
        self._lock = threading.Lock()

    def save(self, trace_obj: ExecutionTrace):
        with self._lock:
            if trace_obj.trace_id not in self._traces_by_id:
                # Evict oldest if needed
                if len(self._traces) == self._capacity:
                    evicted = self._traces[0]
                    self._traces_by_id.pop(evicted.trace_id, None)
                self._traces.append(trace_obj)
            self._traces_by_id[trace_obj.trace_id] = trace_obj

    def get(self, trace_id: str) -> Optional[ExecutionTrace]:
        with self._lock:
            return self._traces_by_id.get(trace_id)

    def list_traces(self, project_id: Optional[int] = None, limit: int = 50) -> List[Dict[str, Any]]:
        with self._lock:
            traces = list(self._traces)
        
        # Newest first
        traces.reverse()
        if project_id is not None:
            traces = [t for t in traces if t.project_id == project_id]
        
        return [t.to_dict() for t in traces[:limit]]


class TelemetryService:
    """
    Core Telemetry Manager configuring OpenTelemetry and in-memory trace collection.
    """
    _instance: Optional['TelemetryService'] = None
    _lock = threading.Lock()

    def __init__(self):
        self.trace_store = InMemoryTraceStore(max_capacity=200)
        self.tracer = None
        self._init_opentelemetry()

    @classmethod
    def get_instance(cls) -> 'TelemetryService':
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    def _init_opentelemetry(self):
        if not OTEL_AVAILABLE:
            agent_logger.warning("OpenTelemetry SDK not found; using in-memory agent trace store only.")
            return

        try:
            resource = Resource.create({"service.name": "acse-contract-evaluator", "service.version": "2.0.0"})
            provider = TracerProvider(resource=resource)

            # Optional OTLP Exporter if configured in environment
            otlp_endpoint = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "").strip()
            if otlp_endpoint:
                try:
                    import importlib
                    # pyrefly: ignore [missing-import]
                    otlp_mod = importlib.import_module("opentelemetry.exporter.otlp.proto.grpc.trace_exporter")
                    OTLPSpanExporter = getattr(otlp_mod, "OTLPSpanExporter")
                    exporter = OTLPSpanExporter(endpoint=otlp_endpoint, insecure=True)
                    provider.add_span_processor(BatchSpanProcessor(exporter))
                    agent_logger.info(f"OpenTelemetry OTLP Exporter connected to {otlp_endpoint}")
                except Exception as ex:
                    agent_logger.warning(f"Could not initialize OTLP exporter: {ex}")

            trace.set_tracer_provider(provider)
            self.tracer = trace.get_tracer("acse.evaluator", "2.0.0")
        except Exception as e:
            agent_logger.warning(f"Failed to initialize OpenTelemetry provider: {e}")

    def start_trace(self, project_id: Optional[int] = None, document_id: Optional[int] = None) -> ExecutionTrace:
        """Starts a new top-level execution trace."""
        trace_id = f"trace_{uuid.uuid4().hex[:12]}"
        exec_trace = ExecutionTrace(trace_id=trace_id, project_id=project_id, document_id=document_id)
        self.trace_store.save(exec_trace)
        ctx_trace_id.set(trace_id)
        agent_logger.bind(trace_id=trace_id, project_id=project_id, document_id=document_id)
        agent_logger.info("Started new execution trace", trace_id=trace_id, project_id=project_id, document_id=document_id)
        return exec_trace

    @contextmanager
    def span(self, span_name: str, attributes: Optional[Dict[str, Any]] = None):
        """
        Context manager wrapping a code block or agent node in an OpenTelemetry & memory span.
        """
        trace_id = ctx_trace_id.get() or "unknown"
        span_id = f"span_{uuid.uuid4().hex[:8]}"
        ctx_node_name.set(span_name)

        span_record = TraceSpanRecord(span_id=span_id, name=span_name)
        if attributes:
            span_record.attributes.update(attributes)

        current_trace = self.trace_store.get(trace_id)
        if current_trace:
            current_trace.add_span(span_record)

        agent_logger.info(f"Entered node span: {span_name}", span_name=span_name, span_id=span_id, **(attributes or {}))

        otel_span = None
        if self.tracer:
            try:
                otel_span = self.tracer.start_span(span_name)
                if attributes:
                    for k, v in attributes.items():
                        if isinstance(v, (str, int, float, bool)):
                            otel_span.set_attribute(k, v)
            except Exception:
                pass

        start_time = time.time()
        try:
            yield span_record
            span_record.finish(status="OK")
            if otel_span:
                try:
                    otel_span.set_status(Status(StatusCode.OK))
                except Exception:
                    pass
        except Exception as e:
            span_record.finish(status="ERROR", error=str(e))
            agent_logger.error(f"Error in span {span_name}: {e}", exc_info=True, span_name=span_name)
            if otel_span:
                try:
                    otel_span.record_exception(e)
                    otel_span.set_status(Status(StatusCode.ERROR, str(e)))
                except Exception:
                    pass
            raise
        finally:
            duration = round((time.time() - start_time) * 1000, 2)
            agent_logger.info(
                f"Exited node span: {span_name} ({duration}ms)",
                span_name=span_name,
                duration_ms=duration,
                status=span_record.status
            )
            if otel_span:
                try:
                    otel_span.end()
                except Exception:
                    pass


# Global singleton instance
telemetry = TelemetryService.get_instance()
