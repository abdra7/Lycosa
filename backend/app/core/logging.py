"""Structured JSON logging with correlation ids (ADR-016).

Correlation ids live in contextvars: the HTTP middleware sets request_id,
the orchestrator sets task_id, the workflow executor sets workflow_run_id.
Every log record emitted within that context carries them automatically.
"""

import json
import logging
import sys
from contextvars import ContextVar
from datetime import UTC, datetime

request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)
task_id_var: ContextVar[str | None] = ContextVar("task_id", default=None)
workflow_run_id_var: ContextVar[str | None] = ContextVar("workflow_run_id", default=None)

_RESERVED = {
    "name",
    "msg",
    "args",
    "levelname",
    "levelno",
    "pathname",
    "filename",
    "module",
    "exc_info",
    "exc_text",
    "stack_info",
    "lineno",
    "funcName",
    "created",
    "msecs",
    "relativeCreated",
    "thread",
    "threadName",
    "processName",
    "process",
    "taskName",
    "message",
}


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        entry: dict = {
            "ts": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname.lower(),
            "logger": record.name,
            "message": record.getMessage(),
        }
        for key, var in (
            ("request_id", request_id_var),
            ("task_id", task_id_var),
            ("workflow_run_id", workflow_run_id_var),
        ):
            value = var.get()
            if value is not None:
                entry[key] = value
        if record.exc_info and record.exc_info[0] is not None:
            entry["exception"] = self.formatException(record.exc_info)
        # structured extras passed via logger.info(..., extra={...})
        for key, value in record.__dict__.items():
            if key not in _RESERVED and not key.startswith("_") and key not in entry:
                entry[key] = value
        return json.dumps(entry, default=str)


def setup_logging(level: str = "info") -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level.upper())
    # uvicorn's own loggers route through root for consistent JSON output
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        logging.getLogger(name).handlers = []
        logging.getLogger(name).propagate = True
