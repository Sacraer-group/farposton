"""Synthetic, date-specific demo history. Never represents actual foot traffic."""
from __future__ import annotations

import calendar
import hashlib
import json
import math
import os
import random
import sqlite3
from contextlib import closing
from datetime import date, datetime, timedelta
from functools import lru_cache
from pathlib import Path
from urllib.parse import quote
from zoneinfo import ZoneInfo

from .places import DAYS, ROOT, opening_state

DEFAULT_DB = ROOT / "data" / "2gis" / "demo-history-presentation-v1" / "history.sqlite3"
TIMEZONE = "Asia/Vladivostok"
ALGORITHM = "farposton-presentation-v4"


def calendar_month(today: date) -> tuple[date, date]:
    year, month = (today.year - 1, 12) if today.month == 1 else (today.year, today.month - 1)
    first = date(year, month, min(today.day, calendar.monthrange(year, month)[1]))
    return first, today - timedelta(days=1)


def stable_seed(*parts: str) -> int:
    return int.from_bytes(hashlib.sha256("|".join(parts).encode()).digest()[:8], "big")


def _kind(place):
    categories = set(place.get("categories") or [])
    if "bar" in categories:
        return "bar"
    if categories.intersection({"cafe", "restaurant", "coffee", "fast", "bakery"}):
        return "food"
    text = " ".join(place.get("rubrics") or []).casefold()
    if any(word in text for word in ("магазин", "торгов", "супермаркет", "аптек")):
        return "retail"
    if any(word in text for word in ("банк", "бизнес-центр", "администрац", "офис")):
        return "office"
    return "other"


def _peak(hour, center, width):
    return math.exp(-0.5 * ((hour - center) / width) ** 2)


def _base_profile(place, day_index):
    # The provider's congestion field is deliberately never used for simulation.
    profile = place.get("synthetic") or {}
    weekly = profile.get("weekly_by_hour") or {}
    existing = weekly.get(DAYS[day_index])
    if isinstance(existing, list) and len(existing) == 24:
        if any(isinstance(n, (int, float)) and n > 0 for values in weekly.values()
               if isinstance(values, list) for n in values):
            return existing, profile.get("schedule_basis") or "unknown"
        fallback, _ = _base_profile({**place, "schedule": None, "synthetic": None}, day_index)
        return fallback, "assumed_09_21_no_usable_hours"
    schedule = place.get("schedule")
    basis = "reported_regular_schedule" if isinstance(schedule, dict) and schedule else "assumed_09_21_daily"
    if basis == "reported_regular_schedule" and not schedule.get("is_24x7") and not any(
            opening_state(schedule, day, hour) == "open" for day in range(7) for hour in range(24)):
        schedule, basis = None, "assumed_09_21_no_usable_hours"
    kind = _kind(place)
    values = []
    for hour in range(24):
        state = opening_state(schedule, day_index, hour)
        if state == "unknown":
            # Missing weekdays in an existing schedule were treated as closed in the original demo.
            is_open = 9 <= hour < 21 if basis.startswith("assumed_09_21") else False
        else:
            is_open = state == "open"
        if not is_open:
            values.append(0)
        elif kind == "bar":
            values.append(round(100 * (0.10 + 0.20 * _peak(hour, 15, 3) + 0.72 * _peak(hour, 21, 3))))
        elif kind == "food":
            values.append(round(100 * (0.12 + 0.56 * _peak(hour, 13, 2.2) + 0.62 * _peak(hour, 19, 2.7))))
        elif kind == "retail":
            values.append(round(100 * (0.15 + 0.53 * _peak(hour, 14, 3.4) + 0.40 * _peak(hour, 19, 2.5))))
        elif kind == "office":
            values.append(round(100 * (0.12 + 0.65 * _peak(hour, 11, 2.2) + 0.42 * _peak(hour, 15, 2.1))))
        else:
            values.append(round(100 * (0.14 + 0.52 * _peak(hour, 13, 3.6) + 0.25 * _peak(hour, 18, 3))))
    return values, basis


def daily_values(place, day: date):
    ident = place["id"]
    template, basis = _base_profile(place, day.weekday())
    venue_rng = random.Random(stable_seed(ALGORITHM, ident, "venue"))
    kind = _kind(place)
    if kind in {"food", "bar"}:
        pattern = venue_rng.random()
        size = (venue_rng.uniform(0.85, 1.00) if pattern < 0.32 else
                venue_rng.uniform(0.58, 0.76) if pattern < 0.72 else
                venue_rng.uniform(0.28, 0.48))
        evening_boost = 10 if pattern < 0.32 else 5 if pattern < 0.72 else 1
    else:
        size = venue_rng.uniform(0.68, 1.18)
        evening_boost = 0
    day_rng = random.Random(stable_seed(ALGORITHM, ident, day.isoformat()))
    local_factor = day_rng.uniform(0.78, 1.20)
    city_rng = random.Random(stable_seed(ALGORITHM, day.isoformat(), "fictional-common-factor"))
    city_factor = city_rng.uniform(0.91, 1.09)
    day_peak = day_rng.randrange(10, 22)
    irregular = day_rng.uniform(-0.12, 0.14)
    smooth = 0.0
    values = []
    for hour, base in enumerate(template):
        if not isinstance(base, (int, float)) or base <= 0:
            values.append(None)
            continue
        smooth = max(-0.14, min(0.14, smooth * 0.62 + day_rng.gauss(0, 0.045)))
        perturbation = 1.0 + smooth + irregular * _peak(hour, day_peak, 2.5)
        weekend_factor = (1.13 if day.weekday() in (4, 5) else 1.02 if day.weekday() == 6 else 0.92)
        extra = evening_boost * _peak(hour, 19, 2.2) if kind in {"food", "bar"} and day.weekday() in (4, 5) else 0
        factor = weekend_factor if kind in {"food", "bar"} else 1.0
        value = round((base * size * factor + extra) * local_factor * city_factor * perturbation)
        if value > 85:
            value = round(85 + 14 * (1 - math.exp(-(value - 85) / 20)))
        values.append(max(1, min(100, value)))
    return values, basis


def create_history(path: Path, places: dict, start: date, end: date) -> dict:
    if end < start or (end - start).days > 366:
        raise ValueError("Некорректный период")
    if not places:
        raise ValueError("Каталог точек пуст")
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    os.close(fd)
    conn = None
    try:
        conn = sqlite3.connect(path)
        conn.executescript("""
            CREATE TABLE metadata(key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE venues(id TEXT PRIMARY KEY, schedule_basis TEXT NOT NULL);
            CREATE TABLE daily(venue_id TEXT NOT NULL, day TEXT NOT NULL, hourly BLOB NOT NULL CHECK(length(hourly)=24),
                               PRIMARY KEY(venue_id, day), FOREIGN KEY(venue_id) REFERENCES venues(id)) WITHOUT ROWID;
            CREATE TABLE weekly(venue_id TEXT NOT NULL, weekday INTEGER NOT NULL, hourly BLOB NOT NULL CHECK(length(hourly)=24),
                                PRIMARY KEY(venue_id, weekday), FOREIGN KEY(venue_id) REFERENCES venues(id)) WITHOUT ROWID;
        """)
        total_days = (end - start).days + 1
        for ident, place in places.items():
            rows = []
            basis = None
            totals = [[0] * 24 for _ in DAYS]
            counts = [[0] * 24 for _ in DAYS]
            for offset in range(total_days):
                day = start + timedelta(days=offset)
                values, basis = daily_values(place, day)
                rows.append((ident, day.isoformat(), bytes(255 if n is None else n for n in values)))
                for hour, value in enumerate(values):
                    if value is not None:
                        totals[day.weekday()][hour] += value
                        counts[day.weekday()][hour] += 1
            conn.execute("INSERT INTO venues VALUES (?,?)", (ident, basis or "unknown"))
            conn.executemany("INSERT INTO daily VALUES (?,?,?)", rows)
            conn.executemany("INSERT INTO weekly VALUES (?,?,?)", (
                (ident, weekday, bytes(round(totals[weekday][hour] / counts[weekday][hour])
                 if counts[weekday][hour] else 255 for hour in range(24)))
                for weekday in range(7)))
        metadata = {"generated_at": datetime.now(ZoneInfo(TIMEZONE)).isoformat(timespec="seconds"),
                    "start_date": start.isoformat(), "end_date": end.isoformat(),
                    "days": total_days, "venues": len(places), "timezone": TIMEZONE,
                    "algorithm": ALGORITHM, "synthetic": True,
                    "unit": "synthetic_index_0_100", "missing_byte": 255,
                    "scenario": "Показательный искусственный сценарий с пиками и альтернативами",
                    "description": "Искусственные демоданные, не наблюдения, не число посетителей и не наличие мест."}
        conn.executemany("INSERT INTO metadata VALUES (?,?)", ((k, json.dumps(v, ensure_ascii=False)) for k, v in metadata.items()))
        conn.commit()
        if conn.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise ValueError("Ошибка целостности базы")
        return metadata
    except BaseException:
        if conn is not None:
            conn.close()
            conn = None
        path.unlink(missing_ok=True)
        raise
    finally:
        if conn is not None:
            conn.close()


def _metadata(conn) -> dict:
    meta = {k: json.loads(v) for k, v in conn.execute("SELECT key,value FROM metadata")}
    if meta.get("synthetic") is not True or meta.get("unit") != "synthetic_index_0_100":
        raise ValueError("Неподтверждённое происхождение истории")
    return meta


@lru_cache(maxsize=2)
def _load_weekly(path: str, modified_ns: int, size: int) -> dict:
    del modified_ns, size
    uri = "file:" + quote(path, safe="/") + "?mode=ro"
    with closing(sqlite3.connect(uri, uri=True)) as conn:
        _metadata(conn)
        result = {}
        for ident, weekday, raw, basis in conn.execute(
                "SELECT w.venue_id,w.weekday,w.hourly,v.schedule_basis "
                "FROM weekly w JOIN venues v ON v.id=w.venue_id"):
            if not 0 <= weekday < 7 or len(raw) != 24:
                raise ValueError("Некорректный недельный профиль")
            values = [None if n == 255 else n for n in raw]
            if any(n is not None and n > 100 for n in values):
                raise ValueError("Некорректный демо-индекс")
            record = result.setdefault(ident, {"weekly_by_hour": {}, "schedule_basis": basis})
            record["weekly_by_hour"][DAYS[weekday]] = values
        return result


def weekly_index() -> dict:
    path = Path(os.environ.get("FARPOSTON_HISTORY_DB", str(DEFAULT_DB))).resolve()
    stat = path.stat()
    return _load_weekly(str(path), stat.st_mtime_ns, stat.st_size)


def read_history(ident: str) -> dict | None:
    path = Path(os.environ.get("FARPOSTON_HISTORY_DB", str(DEFAULT_DB)))
    uri = "file:" + quote(str(path.resolve()), safe="/") + "?mode=ro"
    with closing(sqlite3.connect(uri, uri=True)) as conn:
        meta = _metadata(conn)
        venue = conn.execute("SELECT schedule_basis FROM venues WHERE id=?", (ident,)).fetchone()
        if venue is None:
            return None
        data = []
        for day, raw in conn.execute("SELECT day,hourly FROM daily WHERE venue_id=? ORDER BY day", (ident,)):
            if len(raw) != 24:
                raise ValueError("Некорректная строка истории")
            values = [None if n == 255 else n for n in raw]
            if any(n is not None and n > 100 for n in values):
                raise ValueError("Некорректный демо-индекс")
            present = [n for n in values if n is not None]
            data.append({"date": day, "hourly": values,
                         "average": round(sum(present) / len(present)) if present else None,
                         "peak": max(present) if present else None})
        return {"synthetic": True, "unit": "synthetic_index_0_100", "timezone": meta["timezone"],
                "scenario": meta.get("scenario"),
                "start_date": meta["start_date"], "end_date": meta["end_date"],
                "schedule_basis": venue[0], "days": data}


def history_metadata() -> dict:
    path = Path(os.environ.get("FARPOSTON_HISTORY_DB", str(DEFAULT_DB)))
    uri = "file:" + quote(str(path.resolve()), safe="/") + "?mode=ro"
    with closing(sqlite3.connect(uri, uri=True)) as conn:
        meta = _metadata(conn)
    result = {key: meta[key] for key in ("start_date", "end_date", "days", "venues", "timezone", "synthetic")}
    result["scenario"] = meta.get("scenario")
    return result
