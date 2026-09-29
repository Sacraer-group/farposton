"""Authenticated, read-only weekly rehearsal API."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from .access import require_admin
from .weekly_demo import scenario

router = APIRouter(prefix="/api", dependencies=[Depends(require_admin)])


@router.get("/weekly-demo")
def weekly_demo():
    try:
        return scenario()
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(503, f"Недельный демонабор недоступен: {exc}") from exc
