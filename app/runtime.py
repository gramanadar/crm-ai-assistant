"""Process-wide CRM store and circuit breaker."""

from __future__ import annotations

from app.config import get_settings
from app.crm.circuit import CircuitBreaker
from app.crm.sandbox import SandboxStore

_runtime: "Runtime | None" = None


class Runtime:
    def __init__(self):
        settings = get_settings()
        self.store = SandboxStore()
        self.circuit = CircuitBreaker(settings.crm_circuit_threshold, settings.crm_circuit_open_seconds)
        self.reader_transport = None
        self.writer_transport = None


def get_runtime() -> Runtime:
    global _runtime
    if _runtime is None:
        _runtime = Runtime()
    return _runtime


def reset_runtime() -> None:
    global _runtime
    _runtime = None
