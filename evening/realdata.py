from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

try:
    from web.places import CATEGORY_LABELS as FOOD_CATEGORY_LABELS  # type: ignore
except Exception:  # pragma: no cover
    FOOD_CATEGORY_LABELS = {
        "cafe": ("Кафе", "Кафе-кондитерские"),
        "restaurant": ("Рестораны",),
        "coffee": ("Кофейни",),
        "bar": ("Бары", "Суши-бары", "Пабы"),
        "fast": ("Быстрое питание", "Пиццерии", "Столовые"),
        "bakery": ("Пекарни", "Кондитерские изделия"),
    }

from .config import catalog_dir, events_path
from .timeutil import TZ

DAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
FEATURE_ATTRS = {
    "dog": "Можно с собакой",
    "laptop": "Можно с ноутбуком",
    "children": "Детское меню",
    "reservations": "Заказ столиков",
}
TERRACE_ATTRS = ("Летняя веранда", "Столики на улице")
UUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-5][0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}$"
)


class RealDataUnavailable(Exception):
    pass


@dataclass
class Catalog:
    all_places: dict[str, dict]
    food_places: dict[str, dict]
    source_name: str
    source_checked_at: str | None


_CATALOG_CACHE: Catalog | None = None
_CATALOG_KEY: str | None = None
_EVENTS_CACHE: dict | None = None
_EVENTS_KEY: str | None = None


def _catalog_key() -> str:
    d = catalog_dir()
    if d is None:
        return "none"
    try:
        parts = []
        for folder in ("01_2gis", "02_generated"):
            p = d / folder / "profiles.jsonl"
            st = p.stat()
            parts.append(f"{p}:{st.st_mtime_ns}:{st.st_size}")
        return "|".join(parts)
    except (FileNotFoundError, PermissionError, OSError):
        return f"unavailable:{d}"


def _events_key() -> str:
    p = events_path()
    try:
        st = p.stat()
        return f"{p}:{st.st_mtime_ns}:{st.st_size}"
    except (FileNotFoundError, PermissionError, OSError):
        return f"unavailable:{p}"


def _safe_https(url: Any) -> str | None:
    if not isinstance(url, str):
        return None
    u = url.strip()
    if u.startswith("https://") and len(u) > len("https://"):
        return u
    return None


def _feature_map(attribute_groups: Any) -> dict[str, bool | str]:
    present = set()
    if isinstance(attribute_groups, list):
        for g in attribute_groups:
            if not isinstance(g, dict):
                continue
            for a in g.get("attributes") or []:
                if not isinstance(a, dict):
                    continue
                name = a.get("name")
                if isinstance(name, str):
                    present.add(name)
    feats: dict[str, bool | str] = {}
    for feat, attr in FEATURE_ATTRS.items():
        feats[feat] = True if attr in present else "unknown"
    feats["terrace"] = True if any(a in present for a in TERRACE_ATTRS) else "unknown"
    return feats


def _normalize_rubrics(card: dict) -> list[str]:
    out = []
    for x in card.get("rubrics") or []:
        if isinstance(x, dict) and isinstance(x.get("name"), str):
            out.append(x["name"])
    return out


def _food_categories(rubrics: list[str]) -> list[str]:
    return [key for key, labels in FOOD_CATEGORY_LABELS.items() if any(n in labels for n in rubrics)]


def _normalize_place(row: dict) -> tuple[dict | None, bool]:
    card = row.get("card") or {}
    if not isinstance(card, dict):
        return None, False
    ident = str(card.get("id") or "")
    name = card.get("name")
    if not ident or not isinstance(name, str):
        return None, False
    point = card.get("point") or {}
    lat, lon = point.get("lat"), point.get("lon")
    if not (isinstance(lat, (int, float)) and isinstance(lon, (int, float)) and -90 <= lat <= 90 and -180 <= lon <= 180):
        lat = lon = None
    rubrics = _normalize_rubrics(card)
    categories = _food_categories(rubrics)
    reviews = card.get("reviews") or {}
    rating = reviews.get("general_rating")
    if not isinstance(rating, (int, float)) or not 0 <= rating <= 5:
        rating = None
    count = reviews.get("general_review_count")
    if not isinstance(count, int) or count < 0:
        count = None
    updated_at = (card.get("dates") or {}).get("updated_at") if isinstance(card.get("dates"), dict) else None
    place = {
        "id": ident,
        "name": name,
        "address": card.get("address_name") or card.get("full_address_name") or "",
        "lat": lat,
        "lon": lon,
        "rating": rating,
        "review_count": count,
        "schedule": card.get("schedule") if isinstance(card.get("schedule"), dict) else None,
        "attribute_groups": card.get("attribute_groups") if isinstance(card.get("attribute_groups"), list) else [],
        "features": _feature_map(card.get("attribute_groups")),
        "rubrics": rubrics,
        "categories": categories,
        "category": categories[0] if categories else (rubrics[0] if rubrics else "общепит"),
        "updated_at": updated_at if isinstance(updated_at, str) else None,
        "synthetic": False,
        "source": "2gis_catalog_snapshot",
    }
    return place, bool(categories)


def load_catalog(force: bool = False) -> Catalog:
    global _CATALOG_CACHE, _CATALOG_KEY
    d = catalog_dir()
    if d is None:
        raise RealDataUnavailable("Каталог мест не настроен (EVENING_CATALOG_DIR)")
    key = _catalog_key()
    if not force and _CATALOG_CACHE is not None and _CATALOG_KEY == key:
        return _CATALOG_CACHE
    try:
        if not d.exists():
            raise RealDataUnavailable(f"Каталог мест недоступен: {d}")
        all_places: dict[str, dict] = {}
        food_places: dict[str, dict] = {}
        for folder in ("01_2gis", "02_generated"):
            path = d / folder / "profiles.jsonl"
            if not path.exists():
                raise RealDataUnavailable(f"Файл каталога недоступен: {path}")
            with path.open("r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        row = json.loads(line)
                    except Exception as e:
                        raise RealDataUnavailable(f"Некорректный каталог: {path}") from e
                    place, is_food = _normalize_place(row if isinstance(row, dict) else {})
                    if place is None:
                        continue
                    pid = place["id"]
                    if pid in all_places:
                        continue
                    all_places[pid] = place
                    if is_food:
                        food_places[pid] = place
    except RealDataUnavailable:
        raise
    except (PermissionError, OSError) as e:
        raise RealDataUnavailable("Каталог мест недоступен (ошибка доступа)") from e
    # mtime is not a verification date; catalog checked_at is unknown
    cat = Catalog(all_places=all_places, food_places=food_places, source_name="2gis_catalog_snapshot", source_checked_at=None)
    _CATALOG_CACHE = cat
    _CATALOG_KEY = key
    return cat


def _validate_event(ev: Any) -> dict:
    if not isinstance(ev, dict):
        raise RealDataUnavailable("Некорректные данные событий")
    eid = ev.get("id")
    if not isinstance(eid, str) or not UUID_RE.match(eid):
        raise RealDataUnavailable("Некорректные данные событий")
    eid = str(UUID(eid))
    title = ev.get("title")
    if not isinstance(title, str) or not title.strip():
        raise RealDataUnavailable("Некорректные данные событий")
    starts_at_raw = ev.get("starts_at")
    if not isinstance(starts_at_raw, str):
        raise RealDataUnavailable("Некорректные данные событий")
    try:
        starts_at = datetime.fromisoformat(starts_at_raw)
    except Exception as e:
        raise RealDataUnavailable("Некорректные данные событий") from e
    if starts_at.tzinfo is None:
        raise RealDataUnavailable("Некорректные данные событий")
    starts_at = starts_at.astimezone(TZ)
    d = ev.get("date")
    if not isinstance(d, str):
        raise RealDataUnavailable("Некорректные данные событий")
    try:
        d_obj = date.fromisoformat(d)
    except Exception as e:
        raise RealDataUnavailable("Некорректные данные событий") from e
    if d_obj != starts_at.date():
        raise RealDataUnavailable("Некорректные данные событий")
    status = ev.get("status")
    if status not in {"scheduled", "cancelled", "rescheduled", "past"}:
        raise RealDataUnavailable("Некорректные данные событий")
    synthetic = ev.get("synthetic")
    if synthetic is not False:
        raise RealDataUnavailable("Некорректные данные событий")
    venue_id = ev.get("venue_id")
    if venue_id is None:
        raise RealDataUnavailable("Некорректные данные событий")
    venue_id = str(venue_id)
    if not venue_id:
        raise RealDataUnavailable("Некорректные данные событий")
    venue_name = ev.get("venue_name")
    if not isinstance(venue_name, str) or not venue_name.strip():
        raise RealDataUnavailable("Некорректные данные событий")
    source_url = _safe_https(ev.get("source_url"))
    if source_url is None:
        raise RealDataUnavailable("Некорректные данные событий")
    source_name = ev.get("source_name")
    if not isinstance(source_name, str) or not source_name.strip():
        raise RealDataUnavailable("Некорректные данные событий")
    checked_at_raw = ev.get("checked_at")
    checked_at_out = None
    if isinstance(checked_at_raw, str):
        try:
            cdt = datetime.fromisoformat(checked_at_raw)
            if cdt.tzinfo is None:
                cdt = cdt.replace(tzinfo=TZ)
            checked_at_out = cdt.astimezone(TZ).isoformat(timespec="seconds")
        except Exception as e:
            raise RealDataUnavailable("Некорректные данные событий") from e
    elif checked_at_raw is None:
        checked_at_out = None
    else:
        raise RealDataUnavailable("Некорректные данные событий")
    age = ev.get("age_restriction")
    if age is not None and not isinstance(age, str):
        raise RealDataUnavailable("Некорректные данные событий")
    return {
        "id": eid,
        "title": title.strip(),
        "starts_at": starts_at.isoformat(timespec="seconds"),
        "date": d,
        "status": status,
        "synthetic": False,
        "venue_id": venue_id,
        "venue_name": venue_name.strip(),
        "source_url": source_url,
        "source_name": source_name.strip(),
        "checked_at": checked_at_out,
        "age_restriction": age,
    }


def load_events(force: bool = False) -> dict:
    global _EVENTS_CACHE, _EVENTS_KEY
    p = events_path()
    key = _events_key()
    if not force and _EVENTS_CACHE is not None and _EVENTS_KEY == key:
        return _EVENTS_CACHE
    try:
        if not p.exists():
            raise RealDataUnavailable(f"Файл событий недоступен: {p}")
        with p.open("r", encoding="utf-8") as f:
            data = json.load(f)
    except RealDataUnavailable:
        raise
    except (PermissionError, OSError, json.JSONDecodeError) as e:
        raise RealDataUnavailable(f"Файл событий недоступен или некорректен: {p}") from e
    if not isinstance(data, dict) or data.get("schema_version") != 1 or not isinstance(data.get("events"), list):
        raise RealDataUnavailable("Некорректная схема событий")
    raw_events = data["events"]
    events = [_validate_event(ev) for ev in raw_events]
    out = {"schema_version": 1, "events": events}
    _EVENTS_CACHE = out
    _EVENTS_KEY = key
    return out


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> int:
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return int(round(2 * r * math.asin(min(1.0, math.sqrt(a)))))


# --- schedule ---


def _minutes_of_day(value: Any, allow_2400: bool = False) -> int | None:
    if not isinstance(value, str):
        return None
    try:
        h, m = value.split(":", 1)
        hh, mm = int(h), int(m)
        if allow_2400 and hh == 24 and mm == 0:
            return 1440
        if not (0 <= hh <= 23 and 0 <= mm <= 59):
            return None
        return hh * 60 + mm
    except Exception:
        return None


def _merge_intervals(intervals: list[tuple[int, int]]) -> list[tuple[int, int]]:
    if not intervals:
        return []
    intervals = sorted((max(0, s), min(1440, e)) for s, e in intervals if s < e)
    if not intervals:
        return []
    merged = [intervals[0]]
    for s, e in intervals[1:]:
        ls, le = merged[-1]
        if s <= le:
            merged[-1] = (ls, max(le, e))
        else:
            merged.append((s, e))
    return merged


def _day_parts(schedule: dict, day: str) -> tuple[list[tuple[int, int]], list[tuple[int, int]], bool, bool]:
    """Return (today_spans, next_day_spans_from_overnight, special, has_explicit_hours)."""
    entry = schedule.get(day)
    if entry is None:
        return [], [], False, False
    if not isinstance(entry, dict):
        return [], [], True, False
    wh = entry.get("working_hours")
    if wh is None:
        return [], [], False, False
    if not isinstance(wh, list):
        return [], [], True, False
    today: list[tuple[int, int]] = []
    next_day: list[tuple[int, int]] = []
    special = False
    for it in wh:
        if not isinstance(it, dict):
            special = True
            continue
        s = _minutes_of_day(it.get("from"))
        e = _minutes_of_day(it.get("to"), allow_2400=True)
        if s is None or e is None or s == e:
            special = True
            continue
        if s < e:
            today.append((s, e))
        else:
            today.append((s, 1440))
            next_day.append((0, e))
    if "comment" in entry or "breaks" in entry:
        special = True
    return _merge_intervals(today), _merge_intervals(next_day), special, True


def schedule_open_state(schedule: Any, local_date: date, meal_start: str, meal_end: str) -> str:
    """Return 'open', 'closed', or 'unknown' for full [start,end) on local_date."""
    if not isinstance(schedule, dict) or not schedule:
        return "unknown"
    if schedule.get("is_24x7") is True:
        return "open"
    start = _minutes_of_day(meal_start)
    end = _minutes_of_day(meal_end, allow_2400=True)
    if start is None or end is None or end <= start or end > 1440:
        return "unknown"
    if not any(k in schedule for k in DAYS):
        return "unknown"

    day = DAYS[local_date.weekday()]
    prev_day = DAYS[(local_date.weekday() - 1) % 7]
    today_spans, _, special_today, has_today = _day_parts(schedule, day)
    _, prev_into_today, special_prev, _ = _day_parts(schedule, prev_day)
    trusted_spans = ([] if special_today else today_spans) + ([] if special_prev else prev_into_today)
    if any(start >= s and end <= e for s, e in _merge_intervals(trusted_spans)):
        return "open"
    if special_today or special_prev or not has_today:
        return "unknown"
    return "closed" if today_spans else "unknown"
