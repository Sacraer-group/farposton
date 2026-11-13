from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from uuid import UUID

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from pydantic import ValidationError

from web.auth import require_auth, session_is_valid

from .config import is_real_mode
from .models import PlanCreateRequest
from .realdata import RealDataUnavailable, load_catalog, load_events
from .recommender import recommend
from .seed import find_event as demo_find_event, get_seed as demo_get_seed
from .store import create_plan, get_plan
from .timeutil import event_local_date, event_local_time, iso_vl, now_vl, parse_hhmm

router = APIRouter()
STATIC = Path(__file__).resolve().parents[1] / "web" / "static"

CATALOG_SOURCE_NOTE = (
    "Каталог мест: снимок 2ГИС-карточек (profiles-separated-v1), synthetic-загруженность игнорируется."
)
EVENTS_SOURCE_NOTE = "События: ручная проверенная подборка."
UNVERIFIED_NOTE = "Факт наличия столов не проверен. Статусы билетов не проверены."
STALE_NOTE = "Проверка источника старше 24 часов; данные могли устареть."


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
    return _page(request, "evening-plan.html")


def _parse_iso(dt: str | None):
    if not isinstance(dt, str):
        return None
    try:
        v = datetime.fromisoformat(dt)
        return v
    except Exception:
        return None


def _checked_stale(checked_at: str | None, now: datetime) -> bool:
    dt = _parse_iso(checked_at)
    if dt is None:
        return True
    if dt.tzinfo is None:
        return True
    return (now - dt.astimezone(now.tzinfo)) > timedelta(hours=24)


def _event_public(ev: dict, stale: bool) -> dict:
    out = {
        "id": ev.get("id"),
        "title": ev.get("title"),
        "starts_at": ev.get("starts_at"),
        "date": ev.get("date"),
        "status": ev.get("status"),
        "synthetic": bool(ev.get("synthetic", False)),
        "venue_id": ev.get("venue_id"),
        "venue_name": ev.get("venue_name"),
        "source_url": ev.get("source_url"),
        "source_name": ev.get("source_name"),
        "checked_at": ev.get("checked_at"),
    }
    if ev.get("age_restriction"):
        out["age_restriction"] = ev.get("age_restriction")
    if stale:
        out["warning"] = STALE_NOTE
    return out


def _demo_sources(checked_at: str):
    return [{"name": "fictional_seed", "checked_at": checked_at, "note": "Вымышленные данные, не реальная проверка"}]


def _real_sources(event: dict, catalog_source_checked_at: str | None, now: datetime):
    ev_checked = event.get("checked_at")
    ev_stale = _checked_stale(ev_checked, now)
    sources = [
        {
            "name": "2gis_catalog_snapshot",
            "checked_at": catalog_source_checked_at,
            "note": CATALOG_SOURCE_NOTE,
            "synthetic": False,
        },
        {
            "name": str(event.get("source_name") or "event_source"),
            "checked_at": ev_checked,
            "url": event.get("source_url"),
            "note": EVENTS_SOURCE_NOTE + (f" {STALE_NOTE}" if ev_stale else ""),
            "synthetic": False,
            "stale": ev_stale,
        },
        {
            "name": "availability_notice",
            "checked_at": ev_checked,
            "note": UNVERIFIED_NOTE,
            "synthetic": False,
        },
    ]
    return sources, ev_stale


def _find_event(events: list[dict], event_id: str):
    for ev in events:
        if ev.get("id") == event_id:
            return ev
    return None


@router.get("/api/evening/events", dependencies=[Depends(require_auth)])
def list_events():
    now = now_vl()
    if not is_real_mode():
        seed = demo_get_seed()
        events = []
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
        return {"items": events, "synthetic": True, "timezone": "Asia/Vladivostok", "data_mode": "demo"}

    try:
        evdata = load_events()
    except RealDataUnavailable as e:
        return _err(503, "events_unavailable", "Реальные события недоступны")
    events = []
    any_stale = False
    for ev in evdata.get("events", []):
        status = ev.get("status")
        if status != "scheduled":
            continue
        try:
            starts = datetime.fromisoformat(ev["starts_at"])
            if starts <= now:
                continue
        except Exception:
            continue
        stale = _checked_stale(ev.get("checked_at"), now)
        any_stale = any_stale or stale
        events.append(_event_public(ev, stale))
    out = {
        "items": events,
        "synthetic": False,
        "timezone": "Asia/Vladivostok",
        "data_mode": "real",
        "source": "vladivostok_events",
        "notes": [EVENTS_SOURCE_NOTE, UNVERIFIED_NOTE],
    }
    if any_stale:
        out["warning"] = STALE_NOTE
    return out


def _compute_current_demo(seed: dict, snapshot: dict) -> dict:
    checked_at = iso_vl(now_vl())
    try:
        event_id = snapshot.get("event", {}).get("id") or snapshot.get("request", {}).get("event_id")
        event = demo_find_event(seed, event_id) if event_id else None
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


def _compute_current_real(events: list[dict], snapshot: dict) -> dict:
    checked_at = iso_vl(now_vl())
    try:
        event_id = snapshot.get("event", {}).get("id") or snapshot.get("request", {}).get("event_id")
        event = _find_event(events, event_id) if event_id else None
        if event is None:
            return {"event_status": "unavailable", "starts_at": None, "stale": False, "checked_at": checked_at}
        starts_at = event.get("starts_at")
        status = event.get("status")
        if status == "cancelled":
            return {"event_status": "cancelled", "starts_at": starts_at, "stale": False, "checked_at": checked_at}
        if status == "rescheduled":
            return {"event_status": "rescheduled", "starts_at": starts_at, "stale": False, "checked_at": checked_at}
        snap_event = snapshot.get("event") or {}
        snap_starts_at = snap_event.get("starts_at")
        if isinstance(snap_starts_at, str) and isinstance(starts_at, str):
            try:
                if datetime.fromisoformat(snap_starts_at) != datetime.fromisoformat(starts_at):
                    return {"event_status": "rescheduled", "starts_at": starts_at, "stale": False, "checked_at": checked_at}
            except Exception:
                return {"event_status": "stale", "starts_at": starts_at, "stale": True, "checked_at": checked_at}
        snap_venue_id = snap_event.get("venue_id")
        cur_venue_id = event.get("venue_id")
        if snap_venue_id is not None and cur_venue_id is not None and str(snap_venue_id) != str(cur_venue_id):
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


def _attach_read_metadata(snapshot: dict):
    """Ensure read responses have correct synthetic/source markers and stale warnings."""
    now = now_vl()
    # old fictional/demo plans may have synthetic true and fictional_seed
    if snapshot.get("synthetic") is True:
        # refresh checked_at note? keep snapshot as-is except current computed elsewhere
        return snapshot, None
    # real snapshots: add stale warning info if event source old
    ev_checked = snapshot.get("event", {}).get("checked_at") or None
    # try sources
    stale = False
    for s in snapshot.get("sources") or []:
        if s.get("stale") is True:
            stale = True
    # recompute based on stored event checked_at
    if not stale and isinstance(ev_checked, str):
        stale = _checked_stale(ev_checked, now)
    if stale:
        snapshot["warning"] = STALE_NOTE
    return snapshot, stale


@router.post("/api/evening/plans", dependencies=[Depends(require_auth)])
def create_plan_api(payload: dict = Body(...)):
    try:
        req = PlanCreateRequest.model_validate(payload)
    except ValidationError as e:
        msg = "; ".join([err.get("msg", "validation error") for err in e.errors()])[:300]
        return _err(422, "validation_error", msg or "Invalid request")

    now = now_vl()
    req_dict = req.model_dump()

    if not is_real_mode():
        seed = demo_get_seed()
        event = demo_find_event(seed, req.event_id)
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
            return _err(409, "event_unavailable", "Событие недоступно для создания плана")
        try:
            primary, alternative = recommend(list(seed.get("places", [])), req_dict, event, None)
        except ValueError as e:
            if str(e) == "not_enough_places":
                return _err(409, "not_enough_places", "Недостаточно подходящих мест для плана и плана Б")
            raise
        checked_at = iso_vl(now_vl())
        snapshot = {
            "synthetic": True,
            "request": req_dict,
            "event": {"id": event.get("id"), "title": event.get("title"), "starts_at": event.get("starts_at"), "status": "scheduled"},
            "primary": primary,
            "alternative": alternative,
            "sources": _demo_sources(checked_at),
            "disclaimer": "Наличие столов не проверено. Данные вымышленные. Минуты пути и гарантии мест не показываются.",
        }
        record = create_plan(snapshot)
        record["current"] = _compute_current_demo(seed, snapshot)
        return JSONResponse(status_code=201, content=record)

    # real mode
    try:
        catalog = load_catalog()
        evdata = load_events()
    except RealDataUnavailable:
        return _err(503, "real_data_unavailable", "Реальная база или события недоступны")
    event = _find_event(evdata.get("events", []), req.event_id)
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
        return _err(409, "event_unavailable", "Событие недоступно для создания плана")

    venue_id = event.get("venue_id")
    venue = catalog.all_places.get(str(venue_id)) if venue_id is not None else None
    if venue is None or venue.get("lat") is None or venue.get("lon") is None:
        # cannot compute distance without venue coords - do not invent/substitute
        return _err(409, "event_venue_unavailable", "Площадка события не найдена в каталоге с координатами")
    event_ctx = dict(event)
    event_ctx["venue_lat"] = venue["lat"]
    event_ctx["venue_lon"] = venue["lon"]

    try:
        primary, alternative = recommend(list(catalog.food_places.values()), req_dict, event_ctx, ev_date)
    except ValueError as e:
        if str(e) == "not_enough_places":
            return _err(409, "not_enough_places", "Недостаточно подходящих мест для плана и плана Б")
        raise

    sources, ev_stale = _real_sources(event, catalog.source_checked_at, now)
    snapshot = {
        "synthetic": False,
        "request": req_dict,
        "event": _event_public(event, ev_stale),
        "primary": primary,
        "alternative": alternative,
        "sources": sources,
        "disclaimer": "Наличие столов не проверено. Статусы билетов не проверены. Данные: снимок каталога и ручная проверенная подборка событий; синтетическая загруженность не используется.",
    }
    if ev_stale:
        snapshot["warning"] = STALE_NOTE
    record = create_plan(snapshot)
    record["current"] = _compute_current_real(evdata.get("events", []), snapshot)
    return JSONResponse(status_code=201, content=record)


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

    # Determine mode from snapshot to preserve old fictional plans correctly
    is_snapshot_synthetic = snapshot.get("synthetic") is True
    checked_at_now = iso_vl(now_vl())

    if is_snapshot_synthetic:
        try:
            seed = demo_get_seed()
            current = _compute_current_demo(seed, snapshot)
        except Exception:
            current = {"event_status": "stale", "starts_at": snapshot.get("event", {}).get("starts_at"), "stale": True, "checked_at": checked_at_now}
        snapshot["current"] = current
        # old fictional plans keep their synthetic marker/sources as-is
        return snapshot

    # real snapshot: recheck against current real events if available
    try:
        evdata = load_events()
        current = _compute_current_real(evdata.get("events", []), snapshot)
        # refresh stale warning based on stored checked_at (we must not write auto-verified)
        _attach_read_metadata(snapshot)
    except RealDataUnavailable:
        current = {"event_status": "stale", "starts_at": snapshot.get("event", {}).get("starts_at"), "stale": True, "checked_at": checked_at_now}
        snapshot["warning"] = STALE_NOTE
    snapshot["current"] = current
    return snapshot
