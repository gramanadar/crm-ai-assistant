"""Append-only action transitions."""

from __future__ import annotations

from uuid import uuid4

from sqlalchemy.orm import Session

from app.db.models import Action, ActionEvent, utcnow
from app.errors import AppError

TRANSITIONS: dict[str, set[str]] = {
    "DRAFT": {"PENDING_APPROVAL"},
    "PENDING_APPROVAL": {"APPROVED", "REJECTED", "PENDING_APPROVAL"},
    "APPROVED": {"EXECUTING", "PENDING_APPROVAL"},
    "EXECUTING": {"SUCCEEDED", "FAILED", "UNKNOWN"},
    "FAILED": {"EXECUTING"},
    "UNKNOWN": {"SUCCEEDED", "FAILED", "EXECUTING"},
    "SUCCEEDED": set(),
    "REJECTED": set(),
}


def transition(session: Session, action: Action, to_status: str, actor: str, snapshot: dict | None = None) -> ActionEvent:
    allowed = TRANSITIONS.get(action.status, set())
    if to_status not in allowed:
        raise AppError("validation_failed", f"Cannot move from {action.status} to {to_status}.", 409)
    event = ActionEvent(
        id=str(uuid4()),
        action_id=action.id,
        from_status=action.status,
        to_status=to_status,
        actor=actor,
        payload_snapshot=snapshot if snapshot is not None else action.payload,
        at=utcnow(),
    )
    action.status = to_status
    action.updated_at = utcnow()
    session.add(event)
    session.flush()
    return event
