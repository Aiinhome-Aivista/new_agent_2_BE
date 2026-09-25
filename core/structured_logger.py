"""
Structured Logging Module for ACSE Agent System.

Provides high-performance, structured JSON and formatted logging with
trace context propagation (trace_id, project_id, document_id) across threads.
"""

import json
import logging
import sys
import time
from contextvars import ContextVar
from typing import Any, Dict, Optional

# Context variables for propagating execution context across asynchronous or nested calls
ctx_trace_id: ContextVar[str] = ContextVar("ctx_trace_id", default="")
ctx_project_id: ContextVar[Optional[int]] = ContextVar("ctx_project_id", default=None)
ctx_document_id: ContextVar[Optional[int]] = ContextVar("ctx_document_id", default=None)
ctx_node_name: ContextVar[str] = ContextVar("ctx_node_name", default="")


class StructuredJsonFormatter(logging.Formatter):
    """
    Formats log records as JSON with standard enterprise observability fields.
    """

    def format(self, record: logging.LogRecord) -> str:
        log_payload: Dict[str, Any] = {
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(record.created)),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }

        # Inject context variables if set
        trace_id = ctx_trace_id.get()
        if trace_id:
            log_payload["trace_id"] = trace_id

        project_id = ctx_project_id.get()
        if project_id is not None:
            log_payload["project_id"] = project_id

        document_id = ctx_document_id.get()
        if document_id is not None:
            log_payload["document_id"] = document_id

        node_name = ctx_node_name.get()
        if node_name:
            log_payload["node"] = node_name

        # Include extra custom attributes attached to the LogRecord
        if hasattr(record, "extra_fields") and isinstance(record.extra_fields, dict):
            log_payload.update(record.extra_fields)

        # Include exception info if present
        if record.exc_info:
            log_payload["exception"] = self.formatException(record.exc_info)

        return json.dumps(log_payload, default=str)


class AgentLogger:
    """
    Wrapper around python standard logging with convenience methods for
    agent workflow structured logs.
    """

    def __init__(self, name: str = "acse.agent"):
        self.logger = logging.getLogger(name)
        if not self.logger.handlers:
            self._setup_default_handler()

    def _setup_default_handler(self):
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(StructuredJsonFormatter())
        self.logger.addHandler(handler)
        self.logger.setLevel(logging.INFO)
        self.logger.propagate = False

    def bind(self, trace_id: Optional[str] = None, project_id: Optional[int] = None,
             document_id: Optional[int] = None, node_name: Optional[str] = None):
        """Binds context variables for subsequent log statements in this thread/task."""
        if trace_id is not None:
            ctx_trace_id.set(trace_id)
        if project_id is not None:
            ctx_project_id.set(project_id)
        if document_id is not None:
            ctx_document_id.set(document_id)
        if node_name is not None:
            ctx_node_name.set(node_name)

    def log_event(self, level: int, message: str, **kwargs):
        """Logs a message with additional structured attributes."""
        record = self.logger.makeRecord(
            self.logger.name, level, "(unknown)", 0, message, (), None
        )
        record.extra_fields = kwargs
        self.logger.handle(record)

    def info(self, message: str, **kwargs):
        self.log_event(logging.INFO, message, **kwargs)

    def warning(self, message: str, **kwargs):
        self.log_event(logging.WARNING, message, **kwargs)

    def error(self, message: str, **kwargs):
        self.log_event(logging.ERROR, message, **kwargs)

    def debug(self, message: str, **kwargs):
        self.log_event(logging.DEBUG, message, **kwargs)


# Global singleton logger
agent_logger = AgentLogger("acse.agent")
