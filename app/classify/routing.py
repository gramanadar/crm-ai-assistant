"""Routing rules. Each condition independently sends a request to review."""

from __future__ import annotations

from app.config import Settings


def decide_route(
    *,
    category: str | None,
    confidence: float | None,
    is_ambiguous: bool | None,
    validation_failed: bool,
    disagreed: bool,
    settings: Settings,
) -> tuple[str, str]:
    if validation_failed:
        return "review", "validation_failed"
    if disagreed:
        return "review", "disagreement"
    if is_ambiguous or category == "other":
        return "review", "ambiguous" if is_ambiguous else "category_other"
    if confidence is None or confidence < settings.classify_confidence_threshold:
        return "review", "low_confidence"
    return "routed", "auto"
