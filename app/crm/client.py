"""CRM reads and writes with timeouts, retries, and a circuit breaker.

The write credential is only used by the execution service. Lookup uses the read credential.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from uuid import uuid4

import httpx
from sqlalchemy.orm import Session

from app.config import get_settings
from app.crm.circuit import CircuitBreaker
from app.crm.sandbox import SandboxStore, SandboxTransport
from app.db.models import ExternalCall

CRM_TIMEOUT_MESSAGE = "I couldn't reach the CRM, so I can't confirm this record's details."
CRM_NOT_FOUND_MESSAGE = "No record found for that email."
CRM_UNAVAILABLE_MESSAGE = "CRM unavailable"
CRM_BAD_INPUT_MESSAGE = "The CRM rejected the lookup input."


class CRMCallError(Exception):
    def __init__(self, code: str, message: str, status_code: int | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


class UnknownWrite(Exception):
    """The write request was sent and the outcome is unknown."""


class TransportFailure(Exception):
    def __init__(self, kind: str, status_code: int | None = None):
        super().__init__(kind)
        self.kind = kind
        self.status_code = status_code


@dataclass
class CRMResult:
    ok: bool
    is_error: bool
    message: str
    record: dict | None = None
    code: str = ""


class HttpxTransport:
    def __init__(self, base_url: str, token: str, connect_timeout: float, read_timeout: float):
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.timeout = httpx.Timeout(connect=connect_timeout, read=read_timeout, write=read_timeout, pool=connect_timeout)

    def request(self, method: str, path: str, *, json_body: dict | None = None, headers: dict | None = None) -> dict:
        request_headers = {"Authorization": f"Bearer {self.token}", **(headers or {})}
        try:
            response = httpx.request(
                method,
                f"{self.base_url}{path}",
                params=json_body if method == "GET" else None,
                json=json_body if method != "GET" else None,
                headers=request_headers,
                timeout=self.timeout,
            )
        except httpx.TimeoutException as exc:
            raise TransportFailure("timeout") from exc
        except httpx.HTTPError as exc:
            raise TransportFailure("connection") from exc
        return {"status_code": response.status_code, "body": _safe_json(response)}


class CRMClient:
    def __init__(self, transport, circuit: CircuitBreaker, session: Session, request_id: str, target: str = "crm"):
        self.transport = transport
        self.circuit = circuit
        self.session = session
        self.request_id = request_id
        self.target = target

    def lookup(self, *, email: str | None = None, record_id: str | None = None) -> CRMResult:
        body = {"email": email, "record_id": record_id}
        try:
            payload = self._call("GET", "/records", body, retry_timeout=True, retry_server=True)
        except CRMCallError as exc:
            return CRMResult(ok=False, is_error=True, message=exc.message, code=exc.code)
        return CRMResult(ok=True, is_error=False, message="found", record=payload, code="ok")

    def find_by_external_id(self, external_id: str) -> CRMResult:
        try:
            payload = self._call("GET", "/records", {"external_id": external_id}, retry_timeout=True, retry_server=True)
        except CRMCallError as exc:
            if exc.code == "not_found":
                return CRMResult(ok=False, is_error=True, message=exc.message, code="not_found")
            raise
        return CRMResult(ok=True, is_error=False, message="found", record=payload, code="ok")

    def write(self, payload: dict, external_id: str) -> dict:
        body = {**payload, "external_id": external_id}
        return self._call("POST", "/writes", body, retry_timeout=False, retry_server=True, unknown_on_timeout=True)

    def _call(
        self,
        method: str,
        path: str,
        body: dict,
        *,
        retry_timeout: bool,
        retry_server: bool,
        unknown_on_timeout: bool = False,
    ) -> dict:
        attempt = 0
        while True:
            attempt += 1
            if not self.circuit.allow():
                self._log(path, None, 0, attempt, "circuit_open")
                raise CRMCallError("crm_unavailable", CRM_UNAVAILABLE_MESSAGE, 503)
            started = time.perf_counter()
            try:
                response = self.transport.request(method, path, json_body=body)
            except TransportFailure as exc:
                latency = int((time.perf_counter() - started) * 1000)
                self._log(path, exc.status_code, latency, attempt, exc.kind)
                if exc.kind == "timeout" and unknown_on_timeout:
                    raise UnknownWrite(CRM_TIMEOUT_MESSAGE) from exc
                if exc.kind == "timeout" and retry_timeout and attempt == 1:
                    time.sleep(0.01)
                    continue
                if exc.kind == "timeout":
                    self.circuit.record_failure()
                    raise CRMCallError("crm_unavailable", CRM_TIMEOUT_MESSAGE, 504)
                self.circuit.record_failure()
                raise CRMCallError("crm_unavailable", CRM_UNAVAILABLE_MESSAGE, 503)
            latency = int((time.perf_counter() - started) * 1000)
            status = int(response.get("status_code", 0))
            if status >= 500 or status == 429:
                self._log(path, status, latency, attempt, "server_error")
                self.circuit.record_failure()
                if retry_server and attempt == 1:
                    time.sleep(0.01)
                    continue
                raise CRMCallError("crm_unavailable", CRM_UNAVAILABLE_MESSAGE, status)
            if status == 404:
                self._log(path, status, latency, attempt, "not_found")
                self.circuit.record_success()
                raise CRMCallError("not_found", CRM_NOT_FOUND_MESSAGE, 404)
            if status >= 400:
                self._log(path, status, latency, attempt, "client_error")
                self.circuit.record_success()
                raise CRMCallError("validation_failed", CRM_BAD_INPUT_MESSAGE, status)
            self._log(path, status, latency, attempt, None)
            self.circuit.record_success()
            return response.get("body") or {}

    def _log(self, endpoint: str, status_code: int | None, latency_ms: int, attempt: int, error_class: str | None) -> None:
        self.session.add(
            ExternalCall(
                id=str(uuid4()),
                request_id=self.request_id,
                target=self.target,
                endpoint=endpoint,
                status_code=status_code,
                latency_ms=latency_ms,
                attempt=attempt,
                error_class=error_class,
            )
        )
        self.session.flush()


def _safe_json(response: httpx.Response) -> dict:
    try:
        payload = response.json()
    except Exception:
        return {"raw": ""}
    return payload if isinstance(payload, dict) else {"raw": payload}


def build_reader(session: Session, request_id: str, store: SandboxStore, circuit: CircuitBreaker) -> CRMClient:
    """Lookup client. Reads only the read credential."""
    settings = get_settings()
    runtime_transport = None
    from app.runtime import get_runtime

    runtime_transport = get_runtime().reader_transport
    if runtime_transport is not None:
        return CRMClient(runtime_transport, circuit, session, request_id)
    if settings.crm_base_url.startswith("http"):
        transport = HttpxTransport(
            settings.crm_base_url, settings.crm_read_token, settings.crm_connect_timeout, settings.crm_read_timeout
        )
    else:
        transport = SandboxTransport(store, allow_write=False, token=settings.crm_read_token)
    return CRMClient(transport, circuit, session, request_id)


def build_crm_clients(session: Session, request_id: str, store: SandboxStore, circuit: CircuitBreaker) -> tuple[CRMClient, CRMClient]:
    settings = get_settings()
    if settings.crm_base_url.startswith("http"):
        reader_transport = HttpxTransport(
            settings.crm_base_url, settings.crm_read_token, settings.crm_connect_timeout, settings.crm_read_timeout
        )
        writer_transport = HttpxTransport(
            settings.crm_base_url, settings.crm_write_token, settings.crm_connect_timeout, settings.crm_read_timeout
        )
    else:
        reader_transport = SandboxTransport(store, allow_write=False, token=settings.crm_read_token)
        writer_transport = SandboxTransport(store, allow_write=True, token=settings.crm_write_token)
    reader = CRMClient(reader_transport, circuit, session, request_id)
    writer = CRMClient(writer_transport, circuit, session, request_id)
    return reader, writer
