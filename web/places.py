"""Read-only venue catalogue for the local Vladivostok demo."""
from __future__ import annotations

import json
import os
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
DATA_ROOT = ROOT / "data" / "2gis" / "profiles-separated-v1"
DAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
CATEGORY_LABELS = {
    "cafe": ("Кафе", "Кафе-кондитерские"),
    "restaurant": ("Рестораны",),
    "coffee": ("Кофейни",),
    "bar": ("Бары", "Суши-бары", "Пабы"),
    "fast": ("Быстрое питание", "Пиццерии", "Столовые"),
    "bakery": ("Пекарни", "Кондитерские изделия"),
}


def _minutes(value):
    try:
        hour, minute = (int(x) for x in value.split(":"))
        if not 0 <= hour <= 24 or not 0 <= minute < 60 or (hour == 24 and minute):
            return None
        return hour * 60 + minute
    except (AttributeError, TypeError, ValueError):
        return None


def _intervals(schedule, day):
    entry = schedule.get(day)
    if not isinstance(entry, dict):
        return []
    result = []
    for interval in entry.get("working_hours") or []:
        if not isinstance(interval, dict):
            continue
        start, end = _minutes(interval.get("from")), _minutes(interval.get("to"))
        if start is not None and end is not None and start != end:
            result.append((start, end))
    return result


def opening_state(schedule, day, hour):
    """Use the middle of the selected hour; absence of a schedule is unknown."""
    if not isinstance(schedule, dict) or not schedule:
        return "unknown"
    if schedule.get("is_24x7") is True:
        return "open"
    if not any(key in schedule for key in DAYS):
        return "unknown"
    minute = hour * 60 + 30
    today = _intervals(schedule, DAYS[day])
    previous = _intervals(schedule, DAYS[(day - 1) % 7])
    is_open = any((start <= minute < end if start < end else minute >= start)
                  for start, end in today)
    is_open |= any(start > end and minute < end for start, end in previous)
    if is_open:
        return "open"
    entry = schedule.get(DAYS[day])
    if not isinstance(entry, dict) or not isinstance(entry.get("working_hours"), list):
        return "unknown"
    return "closed"


def _normalise(row):
    card = row.get("card") or {}
    ident = str(card.get("id") or "")
    name = card.get("name")
    if not ident or not isinstance(name, str):
        return None
    names = [x.get("name") for x in card.get("rubrics") or []
             if isinstance(x, dict) and isinstance(x.get("name"), str)]
    types = [key for key, labels in CATEGORY_LABELS.items()
             if any(name in labels for name in names)]
    point = card.get("point") or {}
    lat, lon = point.get("lat"), point.get("lon")
    if not (isinstance(lat, (int, float)) and isinstance(lon, (int, float))
            and 42 < lat < 45 and 130 < lon < 134):
        lat = lon = None
    reviews = card.get("reviews") or {}
    rating = reviews.get("general_rating")
    if not isinstance(rating, (int, float)) or not 0 <= rating <= 5:
        rating = None
    count = reviews.get("general_review_count")
    if not isinstance(count, int) or count < 0:
        count = 0
    kind = row.get("profile_type")
    if kind not in {"synthetic", "2gis"}:
        return None
    synthetic = row.get("synthetic_congestion") if kind == "synthetic" else None
    if synthetic is not None and not isinstance(synthetic, dict):
        synthetic = None
    profile = row.get("congestion") if kind == "2gis" else None
    if profile is not None and not isinstance(profile, dict):
        profile = None
    return {"id": ident, "name": name, "address": card.get("address_name") or "",
            "full_address": card.get("full_address_name") or "", "lat": lat, "lon": lon,
            "rubrics": names, "categories": types, "rating": rating, "review_count": count,
            "schedule": card.get("schedule"), "profile_type": kind,
            "synthetic": synthetic, "congestion_2gis": profile,
            "has_dynamic_congestion": card.get("has_dynamic_congestion") is True,
            "attribute_groups": card.get("attribute_groups") or [],
            "updated_at": (card.get("dates") or {}).get("updated_at")}


@lru_cache(maxsize=1)
def catalogue():
    root = Path(os.environ.get("FARPOSTON_PLACES_DATA_DIR", str(DATA_ROOT)))
    result = {}
    for folder in ("01_2gis", "02_generated"):
        path = root / folder / "profiles.jsonl"
        with path.open(encoding="utf-8") as stream:
            for line in stream:
                if not line.strip():
                    continue
                row = _normalise(json.loads(line))
                if row is None or row["id"] in result:
                    raise ValueError("Некорректные или повторяющиеся записи")
                result[row["id"]] = row
    return result


def now_in_vladivostok():
    return datetime.now(ZoneInfo("Asia/Vladivostok"))


def summary(place, day, hour, demo_weekly=None):
    out = {key: place[key] for key in ("id", "name", "address", "lat", "lon", "rubrics",
           "categories", "rating", "review_count", "profile_type", "has_dynamic_congestion")}
    out["opening_state"] = opening_state(place["schedule"], day, hour)
    profile = (demo_weekly or place.get("synthetic") or {}) if place["profile_type"] == "synthetic" else {}
    week = profile.get("weekly_by_hour") or {}
    values = week.get(DAYS[day])
    value = values[hour] if isinstance(values, list) and len(values) == 24 else None
    out["demo_index"] = value if isinstance(value, int) and 0 <= value <= 100 else None
    out["schedule_assumed"] = str(profile.get("schedule_basis") or "").startswith("assumed_09_21")
    return out
