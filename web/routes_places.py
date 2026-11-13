"""Venue discovery endpoints, using only the local, labelled demo snapshot."""
from __future__ import annotations

import json
import logging
import sqlite3
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse, RedirectResponse

from .auth import require_auth, session_is_valid
from .places import CATEGORY_LABELS, catalogue, now_in_vladivostok, summary
from .places_forecast import forecast
from .places_history import history_metadata, read_history, weekly_index

router = APIRouter()
STATIC = Path(__file__).resolve().parent / "static"


def _catalogue():
    try:
        return catalogue()
    except (OSError, ValueError, json.JSONDecodeError):
        raise HTTPException(503, "Данные заведений сейчас недоступны") from None


def _weekly_profiles():
    try:
        return weekly_index()
    except (OSError, sqlite3.Error, ValueError, KeyError):
        return {}


@router.get("/places")
def places_page(request: Request):
    if not session_is_valid(request):
        return RedirectResponse("/login", status_code=303)
    return FileResponse(STATIC / "places.html")


@router.get("/api/places", dependencies=[Depends(require_auth)])
def places_list(
    q: str = Query(default="", max_length=100),
    category: Literal["food", "all", "cafe", "restaurant", "coffee", "bar", "fast", "bakery"] = "food",
    source: Literal["all", "synthetic", "2gis"] = "all",
    open_only: bool = False,
    sort: Literal["popular", "rating", "name", "demo_asc", "demo_desc"] = "popular",
    day: int | None = Query(default=None, ge=0, le=6),
    hour: int | None = Query(default=None, ge=0, le=23),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=48, ge=1, le=100),
):
    now = now_in_vladivostok()
    if sort in {"demo_asc", "demo_desc"} and source != "synthetic":
        raise HTTPException(400, "Сортировка по демо-индексу доступна только для искусственных профилей")
    day = now.weekday() if day is None else day
    hour = now.hour if hour is None else hour
    places = list(_catalogue().values())
    weekly = _weekly_profiles()
    counts = {key: sum(key in p["categories"] for p in places) for key in CATEGORY_LABELS}
    counts["food"] = sum(bool(p["categories"]) for p in places)
    counts["all"] = len(places)
    term = q.strip().casefold()
    found = []
    for place in places:
        if category == "food" and not place["categories"]:
            continue
        if category not in {"all", "food"} and category not in place["categories"]:
            continue
        if source != "all" and place["profile_type"] != source:
            continue
        if term and term not in " ".join((place["name"], place["address"], *place["rubrics"])).casefold():
            continue
        item = summary(place, day, hour, weekly.get(place["id"]))
        if open_only and item["opening_state"] != "open":
            continue
        found.append(item)
    if sort in {"demo_asc", "demo_desc"}:
        sign = 1 if sort == "demo_asc" else -1
        found.sort(key=lambda x: (x["demo_index"] is None,
                                   sign * (x["demo_index"] or 0), x["name"].casefold()))
    elif sort == "name":
        found.sort(key=lambda x: x["name"].casefold())
    elif sort == "rating":
        found.sort(key=lambda x: (-(x["rating"] or 0), -x["review_count"], x["name"].casefold()))
    else:
        found.sort(key=lambda x: (-x["review_count"], -(x["rating"] or 0), x["name"].casefold()))
    try:
        history = history_metadata()
    except (OSError, sqlite3.Error, ValueError, KeyError):
        history = None
    return {"items": found[offset:offset + limit], "total": len(found), "offset": offset,
            "limit": limit, "counts": counts, "day": day, "hour": hour,
            "timezone": "Asia/Vladivostok", "history": history,
            "data_note": "Неполная выборка, профили демо и 2ГИС не отражают свободные места"}


@router.get("/api/places/{place_id}", dependencies=[Depends(require_auth)])
def place_detail(place_id: str):
    place = _catalogue().get(place_id)
    if place is None:
        raise HTTPException(404, "Заведение не найдено")
    if place["profile_type"] == "synthetic":
        weekly = _weekly_profiles().get(place_id)
        if weekly:
            return {**place, "synthetic": {**(place["synthetic"] or {}), **weekly,
                                            "generated_with": "synthetic-history-weekly"}}
    return place


@router.get("/api/places/{place_id}/history", dependencies=[Depends(require_auth)])
def place_history(place_id: str):
    if place_id not in _catalogue():
        raise HTTPException(404, "Заведение не найдено")
    try:
        result = read_history(place_id)
    except (OSError, sqlite3.Error, ValueError, KeyError):
        raise HTTPException(503, "Демо-история сейчас недоступна") from None
    if result is None:
        raise HTTPException(404, "Демо-история для этой точки не найдена")
    return result


@router.get("/api/places/{place_id}/forecast", dependencies=[Depends(require_auth)])
def place_forecast(place_id: str):
    place = _catalogue().get(place_id)
    if place is None:
        raise HTTPException(404, "Заведение не найдено")
    try:
        return forecast(place)
    except Exception:
        logging.getLogger(__name__).exception("Не удалось получить демопрогноз")
        raise HTTPException(503, "Демопрогноз сейчас недоступен") from None
