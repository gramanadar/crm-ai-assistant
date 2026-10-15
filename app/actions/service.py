"""Drafts and the approval queue. Nothing here writes to the CRM."""

from __future__ import annotations

from uuid import uuid4

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.actions.machine import transition
from app.actions.schema import ActionPayload, idempotency_marker, payload_hash
from app.db.models import Action, ActionEvent
from app.errors import AppError
from app.webhooks.dispatch import emit


def create_draft(
    session: Session,
    reader,
    *,
    kind: str,
    title: str,
    body: str,
    record_id: str | None,
    email: str | None,
    due_date: str | None,
    priority: str | None,
    actor: str,
    conversation_id: str | None = None,
) -> dict:
    lookup = reader.lookup(email=email, record_id=record_id)
    confirmed = bool(lookup.ok and lookup.record)
    resolved_id = (lookup.record or {}).get("id") if confirmed else (record_id or "")
    snapshot = _snapshot(lookup.record) if confirmed else None
    key = uuid4().hex
    marker = idempotency_marker(key)
    if marker not in body:
        body = f"{body.rstrip()}\n\n{marker}"
    try:
        payload = ActionPayload(
            kind=kind,
            record_id=resolved_id or "unconfirmed",
            title=title,
            body=body,
            due_date=due_date,
            priority=priority,
        )
    except ValidationError as exc:
        raise AppError("validation_failed", _validation_message(exc), 400) from exc
    action = Action(
        id=str(uuid4()),
        idempotency_key=key,
        type=kind,
        crm_record_id=resolved_id or None,
        target_snapshot=snapshot,
        payload=payload.model_dump(),
        status="DRAFT",
        lookup_confirmed=confirmed,
        conversation_id=conversation_id,
    )
    session.add(action)
    session.flush()
    session.add(
        ActionEvent(
            id=str(uuid4()),
            action_id=action.id,
            from_status=None,
            to_status="DRAFT",
            actor=actor,
            payload_snapshot=action.payload,
        )
    )
    session.flush()
    return action_payload(action)


def submit_action(session: Session, action_id: str, actor: str) -> dict:
    action = _get(session, action_id)
    if not action.lookup_confirmed or not action.crm_record_id:
        raise AppError("validation_failed", "The target record is unconfirmed, so this draft cannot be submitted.", 400)
    _revalidate(action.payload)
    transition(session, action, "PENDING_APPROVAL", actor, action.payload)
    return action_payload(action)


def approve_action(session: Session, action_id: str, actor: str) -> dict:
    action = _get(session, action_id)
    _revalidate(action.payload)
    action.approved_payload_hash = payload_hash(action.payload)
    action.approved_by = actor
    from app.db.models import utcnow

    action.approved_at = utcnow()
    transition(session, action, "APPROVED", actor, action.payload)
    emit(session, "action.approved", {"action_id": action.id, "idempotency_key": action.idempotency_key})
    session.refresh(action)
    return action_payload(action)


def reject_action(session: Session, action_id: str, actor: str) -> dict:
    action = _get(session, action_id)
    transition(session, action, "REJECTED", actor, action.payload)
    return action_payload(action)


def edit_action(session: Session, action_id: str, actor: str, payload: dict) -> dict:
    action = _get(session, action_id)
    if action.status in {"EXECUTING", "SUCCEEDED", "REJECTED"}:
        raise AppError("validation_failed", "This action can no longer be edited.", 409)
    try:
        parsed = ActionPayload.model_validate(payload)
    except ValidationError as exc:
        raise AppError("validation_failed", _validation_message(exc), 400) from exc
    action.payload = parsed.model_dump()
    action.approved_payload_hash = None
    action.approved_by = None
    action.approved_at = None
    if action.status == "DRAFT":
        session.add(
            ActionEvent(
                id=str(uuid4()),
                action_id=action.id,
                from_status="DRAFT",
                to_status="DRAFT",
                actor=actor,
                payload_snapshot=action.payload,
            )
        )
        session.flush()
    elif action.status == "APPROVED":
        transition(session, action, "PENDING_APPROVAL", actor, action.payload)
    else:
        transition(session, action, "PENDING_APPROVAL", actor, action.payload)
    return action_payload(action)


def list_actions(session: Session, status: str | None) -> list[dict]:
    query = select(Action).order_by(Action.created_at.desc())
    if status:
        wanted = "PENDING_APPROVAL" if status == "pending" else status
        query = query.where(Action.status == wanted)
    return [action_payload(row) for row in session.scalars(query).all()]


def action_events(session: Session, action_id: str) -> list[dict]:
    rows = session.scalars(select(ActionEvent).where(ActionEvent.action_id == action_id).order_by(ActionEvent.at)).all()
    return [
        {
            "id": row.id,
            "from_status": row.from_status,
            "to_status": row.to_status,
            "actor": row.actor,
            "payload_snapshot": row.payload_snapshot,
            "at": row.at.isoformat() if row.at else None,
        }
        for row in rows
    ]


def action_payload(action: Action) -> dict:
    snapshot = action.target_snapshot or {}
    return {
        "id": action.id,
        "idempotency_key": action.idempotency_key,
        "type": action.type,
        "status": action.status,
        "crm_record_id": action.crm_record_id,
        "target": snapshot,
        "target_label": snapshot.get("name") or snapshot.get("email") or snapshot.get("case_title"),
        "payload": action.payload,
        "approved_payload_hash": action.approved_payload_hash,
        "approved_by": action.approved_by,
        "approved_at": action.approved_at.isoformat() if action.approved_at else None,
        "crm_result_id": action.crm_result_id,
        "last_error": action.last_error,
        "lookup_confirmed": action.lookup_confirmed,
        "n8n_result": action.n8n_result,
        "retry_count": action.retry_count,
    }


def _get(session: Session, action_id: str) -> Action:
    action = session.get(Action, action_id)
    if action is None:
        raise AppError("not_found", "Action not found.", 404)
    return action


def _revalidate(payload: dict) -> ActionPayload:
    try:
        return ActionPayload.model_validate(payload)
    except ValidationError as exc:
        raise AppError("validation_failed", _validation_message(exc), 400) from exc


def _validation_message(exc: ValidationError) -> str:
    errors = exc.errors()
    if not errors:
        return "The draft payload is invalid."
    first = errors[0]
    loc = ".".join(str(part) for part in first.get("loc", []))
    return f"{loc}: {first.get('msg')}" if loc else str(first.get("msg"))


def _snapshot(record: dict) -> dict:
    return {
        "id": record.get("id"),
        "name": record.get("name") or record.get("display_name"),
        "email": record.get("email"),
        "case_title": record.get("case_title") or record.get("title"),
    }
