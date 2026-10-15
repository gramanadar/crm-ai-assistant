"""HMAC signatures for backend and n8n webhooks."""

from __future__ import annotations

import hashlib
import hmac
import time

from app.config import get_settings
from app.errors import AppError


def canonical(timestamp: str, method: str, path: str, body: bytes) -> bytes:
    return f"{timestamp}.{method.upper()}.{path}.".encode() + body


def sign(secret: str, timestamp: str, method: str, path: str, body: bytes) -> str:
    digest = hmac.new(secret.encode(), canonical(timestamp, method, path, body), hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def verify(secret: str, timestamp: str, method: str, path: str, body: bytes, signature: str) -> None:
    try:
        stamp = int(timestamp)
    except (TypeError, ValueError) as exc:
        raise AppError("unauthorized", "Webhook timestamp is invalid.", 401) from exc
    if abs(int(time.time()) - stamp) > get_settings().webhook_max_age_seconds:
        raise AppError("unauthorized", "Webhook timestamp is outside the allowed window.", 401)
    expected = sign(secret, str(stamp), method, path, body)
    if not signature or not hmac.compare_digest(expected, signature):
        raise AppError("unauthorized", "Webhook signature is invalid.", 401)
