"""Outbound events and the in-process stand-in for the n8n workflow."""

from __future__ import annotations

import json
import time
from uuid import uuid4

import httpx
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.models import ExternalCall, WebhookEvent
from app.errors import AppError
from app.webhooks.sign import sign, verify


def emit(session: Session, event_type: str, data: dict) -> dict:
    settings = get_settings()
    event_id = f"evt_{uuid4().hex}"
    payload = {"event_id": event_id, "type": event_type, "data": data, "timestamp": int(time.time())}
    row = WebhookEvent(
        id=event_id,
        event_type=event_type,
        direction="outbound",
        payload=payload,
        processed=False,
    )
    session.add(row)
    session.flush()
    body = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
    path = "/webhook/assistant-events"
    signature = sign(settings.n8n_signing_secret, str(payload["timestamp"]), "POST", path, body)
    if settings.n8n_webhook_url:
        _post(session, settings.n8n_webhook_url, body, signature, str(payload["timestamp"]), event_id)
    if settings.n8n_inline:
        row.result = apply_workflow(session, payload, body, signature, path)
        row.processed = True
        session.flush()
    return payload


def apply_workflow(session: Session, payload: dict, body: bytes, signature: str, path: str) -> dict:
    settings = get_settings()
    verify(settings.n8n_signing_secret, str(payload["timestamp"]), "POST", path, body, signature)
    existing = session.get(WebhookEvent, payload["event_id"])
    if existing and existing.processed and existing.result and existing.result.get("applied"):
        return {"ignored": True, "reason": "duplicate_event"}
    event_type = payload.get("type")
    data = payload.get("data") or {}
    if event_type == "request.classified":
        return _notify_classification(data) | {"applied": True}
    if event_type == "action.approved":
        from app.actions.executor import execute_action

        action = execute_action(session, data["action_id"], actor="n8n", request_id=payload["event_id"])
        return {"applied": True, "execute_status": action["status"], "crm_result_id": action.get("crm_result_id")}
    if event_type in {"action.completed", "action.failed"}:
        return {
            "applied": True,
            "notified": data.get("requester") or "requester@example.com",
            "alerted_owner": event_type == "action.failed",
        }
    return {"applied": True, "ignored_type": event_type}


def accept_n8n_result(session: Session, action_id: str, body: bytes, headers: dict, path: str) -> dict:
    from app.db.models import Action

    settings = get_settings()
    timestamp = headers.get("x-timestamp") or headers.get("X-Timestamp") or ""
    signature = headers.get("x-signature") or headers.get("X-Signature") or ""
    verify(settings.n8n_signing_secret, timestamp, "POST", path, body, signature)
    try:
        payload = json.loads(body.decode() or "{}")
    except json.JSONDecodeError as exc:
        raise AppError("validation_failed", "Webhook body must be JSON.", 400) from exc
    event_id = payload.get("event_id")
    if not event_id:
        raise AppError("validation_failed", "event_id is required.", 400)
    existing = session.get(WebhookEvent, event_id)
    if existing is not None:
        return {"ignored": True, "reason": "duplicate_event", "action_id": action_id}
    action = session.get(Action, action_id)
    if action is None:
        raise AppError("not_found", "Action not found.", 404)
    action.n8n_result = payload
    session.add(
        WebhookEvent(
            id=event_id,
            event_type="n8n.result",
            direction="inbound",
            payload=payload,
            processed=True,
            result={"action_id": action_id},
        )
    )
    session.flush()
    return {"ignored": False, "action_id": action_id, "stored": True}


def _notify_classification(data: dict) -> dict:
    settings = get_settings()
    channels = settings.category_channels
    if data.get("status") == "review" or data.get("ambiguous"):
        destination = channels.get("review", "review@example.com")
        return {"notified": destination, "link": data.get("review_url"), "channel": "review"}
    destination = channels.get(data.get("category") or "", channels.get("other", "support@example.com"))
    return {"notified": destination, "channel": data.get("category")}


def _post(session: Session, url: str, body: bytes, signature: str, timestamp: str, event_id: str) -> None:
    started = time.perf_counter()
    status_code = None
    error_class = None
    try:
        response = httpx.post(
            url,
            content=body,
            headers={
                "Content-Type": "application/json",
                "X-Signature": signature,
                "X-Timestamp": timestamp,
                "X-Event-Id": event_id,
            },
            timeout=10,
        )
        status_code = response.status_code
    except httpx.HTTPError as exc:
        error_class = exc.__class__.__name__
    session.add(
        ExternalCall(
            id=str(uuid4()),
            request_id=event_id,
            target="n8n",
            endpoint=url,
            status_code=status_code,
            latency_ms=int((time.perf_counter() - started) * 1000),
            attempt=1,
            error_class=error_class,
        )
    )
    session.flush()
