"""Persist provider usage and enforce the optional daily token ceiling."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.models import LLMCall
from app.errors import AppError
from app.llm.base import StructuredResult, ToolTurn, Usage


def estimate_cost(model: str, usage: Usage) -> float:
    table = get_settings().price_table
    prices = table.get(model) or {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0}
    return (
        usage.input_tokens * float(prices.get("input", 0))
        + usage.output_tokens * float(prices.get("output", 0))
        + usage.cache_read_tokens * float(prices.get("cache_read", 0))
        + usage.cache_write_tokens * float(prices.get("cache_write", 0))
    ) / 1_000_000


def tokens_used_today(session: Session) -> int:
    start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    total = session.scalar(
        select(func.coalesce(func.sum(LLMCall.input_tokens + LLMCall.output_tokens), 0)).where(
            LLMCall.created_at >= start
        )
    )
    return int(total or 0)


def enforce_budget(session: Session, task: str) -> None:
    ceiling = get_settings().daily_token_ceiling
    if not ceiling or ceiling <= 0:
        return
    used = tokens_used_today(session)
    if used >= ceiling:
        from app.logging_config import get_logger

        get_logger("budget").info(
            "budget reached",
            extra={"task": task, "duration_ms": used},
        )
        raise AppError(
            "budget_reached",
            "The daily token budget has been reached. Try again tomorrow or raise the ceiling.",
            429,
        )


def record_usage(session: Session, result: StructuredResult | ToolTurn, task: str, request_id: str) -> LLMCall:
    row = LLMCall(
        id=str(uuid4()),
        request_id=request_id,
        task=task,
        provider=result.provider,
        model=result.model,
        input_tokens=result.usage.input_tokens,
        output_tokens=result.usage.output_tokens,
        cache_read_tokens=result.usage.cache_read_tokens,
        cache_write_tokens=result.usage.cache_write_tokens,
        latency_ms=result.latency_ms,
        stop_reason=result.stop_reason,
        est_cost=estimate_cost(result.model, result.usage),
    )
    session.add(row)
    session.flush()
    return row
