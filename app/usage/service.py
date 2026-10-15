"""Usage totals from stored llm_calls rows."""

from __future__ import annotations

import csv
import io
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import LLMCall


def usage_report(session: Session, start: datetime | None, end: datetime | None, group_by: str) -> dict:
    if group_by not in {"task", "model", "day"}:
        group_by = "task"
    column = {
        "task": LLMCall.task,
        "model": LLMCall.model,
        "day": func.date(LLMCall.created_at),
    }[group_by]
    query = select(
        column.label("key"),
        func.coalesce(func.sum(LLMCall.input_tokens), 0),
        func.coalesce(func.sum(LLMCall.output_tokens), 0),
        func.coalesce(func.sum(LLMCall.cache_read_tokens), 0),
        func.coalesce(func.sum(LLMCall.cache_write_tokens), 0),
        func.coalesce(func.sum(LLMCall.est_cost), 0.0),
        func.count(LLMCall.id),
    )
    if start is not None:
        query = query.where(LLMCall.created_at >= start)
    if end is not None:
        query = query.where(LLMCall.created_at <= end)
    query = query.group_by(column).order_by(column)
    rows = []
    for key, input_tokens, output_tokens, cache_read, cache_write, cost, calls in session.execute(query).all():
        rows.append(
            {
                "key": str(key),
                "input_tokens": int(input_tokens),
                "output_tokens": int(output_tokens),
                "cache_read_tokens": int(cache_read),
                "cache_write_tokens": int(cache_write),
                "estimated_cost": float(cost),
                "calls": int(calls),
            }
        )
    return {"group_by": group_by, "rows": rows}


def usage_csv(report: dict) -> str:
    buffer = io.StringIO()
    writer = csv.DictWriter(
        buffer,
        fieldnames=["key", "input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens", "estimated_cost", "calls"],
    )
    writer.writeheader()
    for row in report["rows"]:
        writer.writerow(row)
    return buffer.getvalue()
