"""JSON logs with request context and secret redaction."""

from __future__ import annotations

import json
import logging
import re
from contextvars import ContextVar
from datetime import datetime, timezone

from app.config import get_settings

request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)
user_var: ContextVar[str | None] = ContextVar("user", default=None)
task_var: ContextVar[str | None] = ContextVar("task", default=None)

_SECRET_RE = re.compile(r"(sk-[A-Za-z0-9_\-]{8,}|Bearer\s+[A-Za-z0-9_\-\.]{8,})")


def redact(text: str) -> str:
    if not text:
        return text
    redacted = text
    try:
        secrets = get_settings().secret_values()
    except Exception:
        secrets = []
    for secret in secrets:
        if secret and secret in redacted:
            redacted = redacted.replace(secret, "[REDACTED]")
    return _SECRET_RE.sub("[REDACTED]", redacted)


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "message": redact(record.getMessage()),
            "logger": record.name,
            "request_id": getattr(record, "request_id", None) or request_id_var.get(),
            "task": getattr(record, "task", None) or task_var.get(),
            "user": getattr(record, "user", None) or user_var.get(),
        }
        if hasattr(record, "duration_ms"):
            payload["duration_ms"] = record.duration_ms
        if record.exc_info:
            payload["error_class"] = record.exc_info[0].__name__ if record.exc_info[0] else "error"
        return json.dumps(payload, default=str)


def configure_logging() -> None:
    settings = get_settings()
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(settings.log_level.upper())


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
