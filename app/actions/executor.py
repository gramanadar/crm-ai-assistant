"""The only CRM write path. The agent never imports this module's credential."""

from __future__ import annotations

import threading

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.actions.machine import transition
from app.actions.schema import payload_hash
from app.actions.service import _revalidate, action_payload
from app.config import get_settings
from app.crm.client import CRMClient, UnknownWrite, build_crm_clients
from app.db.models import Action
from app.errors import AppError
from app.runtime import get_runtime

_GUARD = threading.Lock()
_LOCKS: dict[str, threading.Lock] = {}


def action_lock(action_id: str) -> threading.Lock:
    with _GUARD:
        lock = _LOCKS.get(action_id)
        if lock is None:
            lock = threading.RLock()
            _LOCKS[action_id] = lock
        return lock


def execute_action(session: Session, action_id: str, actor: str, request_id: str) -> dict:
    with action_lock(action_id):
        try:
            return _execute_locked(session, action_id, actor, request_id)
        finally:
            session.commit()


def _execute_locked(session: Session, action_id: str, actor: str, request_id: str) -> dict:
    with action_lock(action_id):
        statement = select(Action).where(Action.id == action_id)
        if session.get_bind().dialect.name == "postgresql":
            statement = statement.with_for_update()
        action = session.execute(statement).scalar_one_or_none()
        if action is None:
            raise AppError("not_found", "Action not found.", 404)
        if action.status == "SUCCEEDED":
            return action_payload(action)
        if action.status == "UNKNOWN":
            verified = _verify_unknown(session, action, actor, request_id)
            session.refresh(action)
            if action.status == "SUCCEEDED" or (action.retry_count or 0) >= 1:
                return verified
            action.retry_count = (action.retry_count or 0) + 1
        elif action.status not in {"APPROVED", "FAILED"}:
            raise AppError("validation_failed", "Only an approved action can be executed.", 409)
        current_hash = payload_hash(action.payload)
        if not action.approved_payload_hash or current_hash != action.approved_payload_hash:
            action.approved_payload_hash = None
            action.approved_by = None
            action.approved_at = None
            if action.status == "APPROVED":
                transition(session, action, "PENDING_APPROVAL", actor, action.payload)
            raise AppError("validation_failed", "The payload changed after approval and was not written.", 409)
        _revalidate(action.payload)
        reader, writer = _clients(session, request_id)
        if writer is reader:
            raise AppError("crm_unavailable", "Write and read clients must stay separate.", 500)
        lookup = reader.lookup(record_id=action.crm_record_id)
        if not lookup.ok:
            transition(session, action, "EXECUTING", actor, action.payload)
            transition(session, action, "FAILED", actor, action.payload)
            action.last_error = "not_found" if lookup.code == "not_found" else lookup.code or "crm_unavailable"
            session.flush()
            _emit_finished(session, action, failed=True)
            return action_payload(action)
        transition(session, action, "EXECUTING", actor, action.payload)
        try:
            result = writer.write(action.payload, action.idempotency_key)
        except UnknownWrite:
            transition(session, action, "UNKNOWN", actor, action.payload)
            action.last_error = "unknown_write"
            session.flush()
            return _verify_unknown(session, action, actor, request_id)
        except Exception as exc:
            code = getattr(exc, "code", "crm_unavailable")
            transition(session, action, "FAILED", actor, action.payload)
            action.last_error = code
            action.retry_count = (action.retry_count or 0) + 1
            session.flush()
            _emit_finished(session, action, failed=True)
            return action_payload(action)
        action.crm_result_id = str((result or {}).get("id") or "")
        action.last_error = None
        transition(session, action, "SUCCEEDED", actor, action.payload)
        _emit_finished(session, action, failed=False)
        return action_payload(action)


def _verify_unknown(session: Session, action: Action, actor: str, request_id: str) -> dict:
    _reader, writer = _clients(session, request_id)
    try:
        found = writer.find_by_external_id(action.idempotency_key)
    except Exception:
        found = None
    if found is not None and found.ok and found.record:
        action.crm_result_id = str(found.record.get("id") or "")
        action.last_error = None
        if action.status == "UNKNOWN":
            transition(session, action, "SUCCEEDED", actor, action.payload)
        _emit_finished(session, action, failed=False)
        return action_payload(action)
    action.last_error = "unknown_write_absent"
    session.flush()
    return action_payload(action)


def _clients(session: Session, request_id: str) -> tuple[CRMClient, CRMClient]:
    runtime = get_runtime()
    settings = get_settings()
    if runtime.reader_transport is not None or runtime.writer_transport is not None:
        reader = CRMClient(runtime.reader_transport, runtime.circuit, session, request_id)
        writer = CRMClient(runtime.writer_transport, runtime.circuit, session, request_id)
        return reader, writer
    reader, writer = build_crm_clients(session, request_id, runtime.store, runtime.circuit)
    if settings.crm_write_token and settings.crm_write_token == settings.crm_read_token and settings.crm_base_url.startswith("http"):
        return reader, writer
    return reader, writer


def _emit_finished(session: Session, action: Action, failed: bool) -> None:
    from app.webhooks.dispatch import emit

    emit(
        session,
        "action.failed" if failed else "action.completed",
        {
            "action_id": action.id,
            "status": action.status,
            "crm_result_id": action.crm_result_id,
            "last_error": action.last_error,
            "requester": "requester@example.com",
        },
    )
