"""Classify a support request and store the route."""

from __future__ import annotations

from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.classify.prompt import classification_system, classification_user
from app.classify.routing import decide_route
from app.classify.schema import get_classification_model
from app.config import get_settings
from app.db.models import SupportRequest, utcnow
from app.errors import AppError
from app.llm.base import LLMProvider
from app.webhooks.dispatch import emit


def classify_request(session: Session, provider: LLMProvider, text: str, source: str, request_id: str) -> dict:
    text = text.strip()
    if not text:
        raise AppError("validation_failed", "Request text is required.", 400)
    settings = get_settings()
    schema = get_classification_model()
    system = classification_system(settings.category_list)
    messages = [{"role": "user", "content": classification_user(text)}]

    first = provider.structured(system=system, messages=messages, schema=schema, task="classify", request_id=request_id)
    parsed, error = _coerce(first, schema)
    attempts = [first]
    if error and settings.classify_double_check:
        pass
    if error:
        second = provider.structured(
            system=system,
            messages=messages + [{"role": "user", "content": "The previous output failed validation. Return the schema."}],
            schema=schema,
            task="classify",
            request_id=request_id,
        )
        attempts.append(second)
        parsed, error = _coerce(second, schema)

    disagreed = False
    if parsed is not None and settings.classify_double_check and not error:
        second = provider.structured(system=system, messages=messages, schema=schema, task="classify", request_id=request_id)
        attempts.append(second)
        other, other_error = _coerce(second, schema)
        if other_error or other is None or str(other.category) != str(parsed.category):
            disagreed = True

    if error or parsed is None:
        row = SupportRequest(
            id=str(uuid4()),
            source=source or "manual",
            text=text,
            category=None,
            confidence=None,
            is_ambiguous=True,
            reason=None,
            extracted=None,
            model_output={"attempts": len(attempts)},
            status="review",
            route_reason="validation_failed",
            validation_error=error or "invalid",
            split="unlabeled",
        )
        session.add(row)
        session.flush()
        _emit_classified(session, row)
        return _payload(row)

    category = str(parsed.category)
    extracted = None
    if parsed.extracted is not None:
        extracted = {key: value for key, value in parsed.extracted.model_dump().items() if value}
        if not extracted:
            extracted = None
    status, reason = decide_route(
        category=category,
        confidence=float(parsed.confidence),
        is_ambiguous=bool(parsed.is_ambiguous),
        validation_failed=False,
        disagreed=disagreed,
        settings=settings,
    )
    row = SupportRequest(
        id=str(uuid4()),
        source=source or "manual",
        text=text,
        category=category,
        confidence=float(parsed.confidence),
        is_ambiguous=bool(parsed.is_ambiguous) or disagreed,
        reason=parsed.reason,
        extracted=extracted,
        model_output=parsed.model_dump(),
        status=status,
        route_reason=reason,
        validation_error=None,
        final_category=category if status == "routed" else None,
        split="unlabeled",
    )
    session.add(row)
    session.flush()
    _emit_classified(session, row)
    return _payload(row)


def list_requests(session: Session, status: str | None) -> list[dict]:
    query = select(SupportRequest).order_by(SupportRequest.created_at.desc())
    if status:
        query = query.where(SupportRequest.status == status)
    return [_payload(row) for row in session.scalars(query).all()]


def review_request(session: Session, request_id: str, reviewer: str, decision: str, category: str | None) -> dict:
    row = session.get(SupportRequest, request_id)
    if row is None:
        raise AppError("not_found", "Request not found.", 404)
    if row.status != "review":
        raise AppError("validation_failed", "Only review items can be resolved.", 400)
    settings = get_settings()
    if decision == "confirm":
        final = row.category
        if not final:
            raise AppError("validation_failed", "There is no suggested category to confirm.", 400)
        row.corrected_category = None
    elif decision == "correct":
        if not category or category not in settings.category_list:
            raise AppError("validation_failed", "A valid corrected category is required.", 400)
        final = category
        row.corrected_category = category
    else:
        raise AppError("validation_failed", "Decision must be confirm or correct.", 400)
    row.final_category = final
    row.reviewer = reviewer
    row.reviewed_at = utcnow()
    row.status = "resolved"
    session.flush()
    return _payload(row)


def export_labels(session: Session) -> list[dict]:
    rows = session.scalars(
        select(SupportRequest).where(SupportRequest.final_category.is_not(None)).order_by(SupportRequest.created_at)
    ).all()
    exported = []
    for row in rows:
        if row.split == "test":
            continue
        exported.append(
            {
                "id": row.id,
                "text": row.text,
                "model_category": row.category,
                "human_category": row.final_category,
                "corrected_category": row.corrected_category,
                "split": row.split or "unlabeled",
            }
        )
    return exported


def _coerce(result, schema):
    if result is None or result.invalid or result.parsed is None or result.stop_reason in {"refusal", "max_tokens"}:
        return None, result.stop_reason if result else "invalid"
    try:
        parsed = result.parsed if isinstance(result.parsed, schema) else schema.model_validate(result.parsed)
    except Exception as exc:
        return None, str(exc.__class__.__name__)
    return parsed, None


def _emit_classified(session: Session, row: SupportRequest) -> None:
    emit(
        session,
        "request.classified",
        {
            "request_id": row.id,
            "status": row.status,
            "category": row.category,
            "confidence": row.confidence,
            "ambiguous": row.status == "review",
            "review_url": f"{get_settings().public_app_url}/review?id={row.id}",
        },
    )


def _payload(row: SupportRequest) -> dict:
    return {
        "id": row.id,
        "source": row.source,
        "text": row.text,
        "category": row.category,
        "confidence": row.confidence,
        "is_ambiguous": row.is_ambiguous,
        "reason": row.reason,
        "extracted": row.extracted,
        "status": row.status,
        "route_reason": row.route_reason,
        "validation_error": row.validation_error,
        "reviewer": row.reviewer,
        "corrected_category": row.corrected_category,
        "final_category": row.final_category,
        "reviewed_at": row.reviewed_at.isoformat() if row.reviewed_at else None,
        "split": row.split,
        "model_category": (row.model_output or {}).get("category", row.category) if isinstance(row.model_output, dict) else row.category,
    }
