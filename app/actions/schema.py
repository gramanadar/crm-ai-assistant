"""Payload validated again at draft, submit, and execution."""

from __future__ import annotations

import json
from datetime import datetime
from hashlib import sha256

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ActionPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: str
    record_id: str = Field(min_length=1, max_length=120)
    title: str = Field(min_length=1, max_length=200)
    body: str = Field(min_length=1, max_length=4000)
    due_date: str | None = None
    priority: str | None = None

    @model_validator(mode="after")
    def check_kind(self) -> "ActionPayload":
        if self.kind not in {"note", "task"}:
            raise ValueError("kind must be note or task")
        if self.kind == "task":
            if self.priority not in {"low", "normal", "high"}:
                raise ValueError("task priority must be low, normal, or high")
            if not self.due_date:
                raise ValueError("task due_date is required")
            try:
                datetime.strptime(self.due_date, "%Y-%m-%d")
            except ValueError as exc:
                raise ValueError("task due_date must be YYYY-MM-DD") from exc
        return self


def payload_hash(payload: dict) -> str:
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return sha256(raw.encode()).hexdigest()


def idempotency_marker(key: str) -> str:
    return f"[idempotency:{key}]"
