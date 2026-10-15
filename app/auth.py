"""Bearer tokens mapped to roles. Operator maps to approver in v1."""

from __future__ import annotations

from dataclasses import dataclass

from app.config import get_settings
from app.errors import AppError


@dataclass(frozen=True)
class CurrentUser:
    id: str
    roles: frozenset[str]

    def has_role(self, *roles: str) -> bool:
        return any(role in self.roles for role in roles)


def parse_auth_tokens(raw: str) -> dict[str, CurrentUser]:
    users: dict[str, CurrentUser] = {}
    for part in raw.split(","):
        item = part.strip()
        if not item or ":" not in item:
            continue
        roles_raw, token = item.split(":", 1)
        roles = frozenset(role.strip() for role in roles_raw.split("+") if role.strip())
        if not token or not roles:
            continue
        users[token] = CurrentUser(id=roles_raw, roles=roles)
    return users


def user_from_header(authorization: str | None) -> CurrentUser:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise AppError("unauthorized", "Authentication required.", 401)
    token = authorization.split(" ", 1)[1].strip()
    user = parse_auth_tokens(get_settings().auth_tokens).get(token)
    if user is None:
        raise AppError("unauthorized", "Authentication required.", 401)
    return user


def require_user(user: CurrentUser, *roles: str) -> CurrentUser:
    if roles and not user.has_role(*roles):
        raise AppError("forbidden", "You do not have access to this action.", 403)
    return user
