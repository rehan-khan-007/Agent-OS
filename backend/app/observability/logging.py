"""
Structured logging: emits JSON log lines instead of Python's default
plain-text format, so logs are machine-parseable by whatever ingests
them (Render's log viewer, a future log aggregator, etc.) and each
line carries consistent fields (timestamp, level, logger name,
message) rather than relying on eyeballing free-text output.
"""

import json
import logging
import sys
from datetime import datetime, timezone


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        # Allow callers to attach extra structured fields via
        # logger.info("msg", extra={"extra_fields": {...}})
        extra_fields = getattr(record, "extra_fields", None)
        if extra_fields:
            payload.update(extra_fields)
        return json.dumps(payload)


def configure_logging(level: str = "INFO") -> None:
    """Call once at app startup. Replaces the root logger's handlers
    with a single JSON-formatted stream handler."""
    root = logging.getLogger()
    root.setLevel(level)

    # Remove any existing handlers (e.g. uvicorn's defaults) to avoid
    # duplicate/mismatched-format log lines.
    for handler in list(root.handlers):
        root.removeHandler(handler)

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root.addHandler(handler)

    # Uvicorn's own loggers propagate to root by default once we clear
    # their handlers too, so access/error logs also come out as JSON.
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        uv_logger = logging.getLogger(name)
        uv_logger.handlers = []
        uv_logger.propagate = True


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
