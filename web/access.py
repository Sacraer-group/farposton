"""Server-side point scope for local pilot accounts.

The browser must never be trusted to choose a point.  A manager's point is
therefore read from the signed session, while an administrator may select one.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import date

from fastapi import HTTPException, Request, status


@dataclass(frozen=True)
class Principal:
    username: str
    role: str
    point_key: str | None = None

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"


def demo_no_auth() -> bool:
    return (os.environ.get("WEB_DEMO_NO_AUTH") == "1"
            and os.environ.get("DEPLOYMENT_PROFILE", "local").lower() == "local")


def demo_date() -> date | None:
    """Explicit fixture clock; never enabled for authenticated/client deployments."""
    raw = os.environ.get("WEB_DEMO_DATE", "").strip()
    return date.fromisoformat(raw) if raw and demo_no_auth() else None


def principal_from_session(request: Request) -> Principal:
    if demo_no_auth():
        return Principal(username="admin", role="admin")
    username = request.session.get("username")
    role = request.session.get("role")
    point_key = request.session.get("point_key")
    if not isinstance(username, str) or role not in {"admin", "manager"}:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Требуется вход")
    if role == "manager" and (not isinstance(point_key, str) or not point_key):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Некорректная сессия")
    return Principal(username=username, role=role, point_key=point_key if isinstance(point_key, str) else None)


def require_admin(request: Request) -> Principal:
    """FastAPI dependency for network-wide analytical sections.

    Point managers intentionally receive only the order workflow and their
    operational summaries.  Keeping this check on the server means that hiding
    navigation links is presentation only: a direct API request is rejected too.
    """
    principal = principal_from_session(request)
    if not principal.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Недостаточно прав: раздел доступен только администратору.",
        )
    return principal


def request_point(principal: Principal, requested: str | None = None) -> str | None:
    """Return the only point a manager can access; admin may optionally filter."""
    if principal.is_admin:
        return requested
    if requested is not None and requested != principal.point_key:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Нет доступа к другой точке")
    return principal.point_key


def require_owned_point(principal: Principal, point_key: str) -> None:
    if not principal.is_admin and point_key != principal.point_key:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Нет доступа к другой точке")
