from __future__ import annotations

from datetime import datetime
from pathlib import Path
from uuid import UUID

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from pydantic import ValidationError

from web.auth import require_auth, session_is_valid

from .models import PlanCreateRequest
from .recommender import recommend
from .seed import find_event, get_seed
from .store import create_plan, get_plan
from .timeutil import TZ, combine_local, event_local_date, event_local_time, iso_vl, now_vl, parse_hhmm

router = APIRouter()
STATIC = Path(__file__).resolve().parents[1] / "web" / "static"


def _err(status: int, code: str, message: str):
    return JSONResponse(status_code=status, content={"detail": {"error": code, "message": message}})


def _page(request: Request, name: str):
    if not session_is_valid(request):
        return RedirectResponse("/login", status_code=303)
    path = STATIC / name
    if not path.exists():
        raise HTTPException(404, "Page not found")
    return FileResponse(path)


@router.get("/evening")
def evening_page(request: Request):
    return _page(request, "evening.html")


@router.get("/evening/p/{plan_id}")
def evening_plan_page(plan_id: str, request: Request):
    # plan_id in path can be any string for page; API validates existence
    return _page(request, "evening-plan.html")


@router.get("/api/evening/events", dependencies=[Depends(require_auth)])
def list_events():
    seed = get_seed()
    events = []
    now = now_vl()
    for ev in seed.get("events", []):
        status = ev.get("status")
        if status != "scheduled":
            continue
        try:
            starts = datetime.fromisoformat(ev["starts_at"])
            if starts <= now:
                continue
        except Exception:
            continue
        events.append({
            "id": ev.get("id"),
            "title": ev.get("title"),
            "starts_at": ev.get("starts_at"),
            "date": ev.get("date"),
            "status": ev.get("status"),
            "synthetic": True,
        })
    return {"items": events, "synthetic": True, "timezone": "Asia/Vladivostok"}


@router.post("/api/evening/plans", dependencies=[Depends(require_auth)])
def create_plan_api(payload: dict = Body(...)):
    try:
        req = PlanCreateRequest.model_validate(payload)
    except ValidationError as e:
        msg = "; ".join([err.get("msg", "validation error") for err in e.errors()])[:300]
        return _err(422, "validation_error", msg or "Invalid request")

    seed = get_seed()
    event = find_event(seed, req.event_id)
    if event is None:
        return _err(404, "event_not_found", "Событие не найдено")

    try:
        starts_at = datetime.fromisoformat(event["starts_at"])
    except Exception:
        return _err(409, "event_unavailable", "Событие недоступно")

    ev_date = event_local_date(starts_at)
    if req.date != ev_date.isoformat():
        return _err(422, "validation_error", "date must match event date in Asia/Vladivostok")

    meal_start = parse_hhmm(req.meal_start)
    meal_end = parse_hhmm(req.meal_end)
    if meal_end <= meal_start:
        return _err(422, "validation_error", "meal_end must be later than meal_start")
    ev_time = event_local_time(starts_at)
    if meal_end > ev_time:
        return _err(422, "validation_error", "meal_end must be not later than event start")

    status = event.get("status")
    if status in {"cancelled", "past", "rescheduled"} or starts_at <= now_vl() or status != "scheduled":
        # rescheduled treated as unavailable for this exact event instance in MVP
        if status == "scheduled" and starts_at <= now_vl():
            pass
        return _err(409, "event_unavailable", "Событие недоступно для создания плана")

    req_dict = req.model_dump()
    try:
        primary, alternative = recommend(seed, req_dict, event)
    except ValueError as e:
        if str(e) == "not_enough_places":
            return _err(409, "not_enough_places", "Недостаточно подходящих мест для плана и плана Б")
        raise

    checked_at = iso_vl(now_vl())
    snapshot = {
        "synthetic": True,
        "request": req_dict,
        "event": {
            "id": event.get("id"),
            "title": event.get("title"),
            "starts_at": event.get("starts_at"),
            "status": "scheduled",
        },
        "primary": primary,
        "alternative": alternative,
        "sources": [
            {"name": "fictional_seed", "checked_at": checked_at, "note": "Вымышленные данные, не реальная проверка"}
        ],
        "disclaimer": "Наличие столов не проверено. Данные вымышленные. Минуты пути и гарантии мест не показываются.",
    }
    record = create_plan(snapshot)
    current = _compute_current(seed, snapshot)
    record["current"] = current
    return JSONResponse(status_code=201, content=record)


def _compute_current(seed: dict, snapshot: dict) -> dict:
    checked_at = iso_vl(now_vl())
    try:
        event_id = snapshot.get("event", {}).get("id") or snapshot.get("request", {}).get("event_id")
        event = find_event(seed, event_id) if event_id else None
        if event is None:
            return {"event_status": "unavailable", "starts_at": None, "stale": False, "checked_at": checked_at}
        starts_at = event.get("starts_at")
        status = event.get("status")
        if status == "cancelled":
            return {"event_status": "cancelled", "starts_at": starts_at, "stale": False, "checked_at": checked_at}
        if status == "rescheduled":
            return {"event_status": "rescheduled", "starts_at": starts_at, "stale": False, "checked_at": checked_at}
        try:
            st = datetime.fromisoformat(starts_at)
            if st <= now_vl() or status == "past":
                return {"event_status": "past", "starts_at": starts_at, "stale": False, "checked_at": checked_at}
        except Exception:
            return {"event_status": "stale", "starts_at": starts_at, "stale": True, "checked_at": checked_at}
        return {"event_status": "scheduled", "starts_at": starts_at, "stale": False, "checked_at": checked_at}
    except Exception:
        return {"event_status": "stale", "starts_at": None, "stale": True, "checked_at": checked_at}


@router.get("/api/evening/plans/{plan_id}", dependencies=[Depends(require_auth)])
def get_plan_api(plan_id: str):
    try:
        UUID(plan_id)
    except Exception:
        return _err(422, "validation_error", "Invalid plan id")
    snapshot, state = get_plan(plan_id)
    if state == "not_found":
        return _err(404, "plan_not_found", "План не найден")
    if state == "expired":
        return _err(410, "plan_expired", "Срок действия плана истёк")
    try:
        seed = get_seed()
        current = _compute_current(seed, snapshot)
    except Exception:
        checked_at = iso_vl(now_vl())
        current = {"event_status": "stale", "starts_at": snapshot.get("event", {}).get("starts_at"), "stale": True, "checked_at": checked_at}
    snapshot["current"] = current
    return snapshot
