from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid5, NAMESPACE_URL

from .timeutil import TZ, today_vl

DEFAULT_DEMO_DIR = Path(".demo/evening")
SEED_FILENAME = "seed.json"

FEATURES = ("dog", "laptop", "children", "terrace", "reservations")


def demo_dir() -> Path:
    return Path(os.getenv("EVENING_DEMO_DIR", DEFAULT_DEMO_DIR))


def seed_path() -> Path:
    return demo_dir() / SEED_FILENAME


def _stable_uuid(name: str) -> str:
    return str(uuid5(NAMESPACE_URL, f"evening-mvp:{name}"))


def _ensure_dir(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def generate_seed(base_day=None) -> dict:
    base = base_day or today_vl()
    # 6 events on nearest dates: mix of statuses/times for acceptance scenarios
    event_specs = [
        ("Концерт в вымышленном зале", 1, "19:30", "scheduled"),
        ("Лекция в воображаемой библиотеке", 2, "18:45", "scheduled"),
        ("Киносеанс в демонстрационном кинотеатре", 3, "20:00", "scheduled"),
        ("Спектакль в примерном театре", 1, "17:30", "cancelled"),
        ("Выставка в условной галерее", -1, "18:00", "past"),
        ("Встреча в тестовом клубе", 4, "19:00", "rescheduled"),
    ]
    events = []
    for i, (title, day_off, hm, status) in enumerate(event_specs, start=1):
        d = base + timedelta(days=day_off)
        starts = datetime(d.year, d.month, d.day, int(hm[:2]), int(hm[3:]), tzinfo=TZ)
        events.append({
            "id": _stable_uuid(f"event-{i}-{d.isoformat()}-{hm}"),
            "title": title,
            "starts_at": starts.isoformat(timespec="seconds"),
            "date": d.isoformat(),
            "status": status,
            "synthetic": True,
        })

    categories = [
        "кафе", "ресторан", "кофейня", "бистро", "столовая",
        "пиццерия", "пекарня", "бар", "чайная", "бургерная",
    ]
    feature_patterns = [
        {"dog": True, "laptop": True, "children": False, "terrace": "unknown", "reservations": True},
        {"dog": False, "laptop": True, "children": True, "terrace": True, "reservations": True},
        {"dog": True, "laptop": False, "children": True, "terrace": False, "reservations": False},
        {"dog": "unknown", "laptop": True, "children": False, "terrace": True, "reservations": True},
        {"dog": False, "laptop": False, "children": True, "terrace": "unknown", "reservations": False},
    ]
    hours_patterns = [
        ("09:00", "22:00"),
        ("10:00", "21:00"),
        ("08:00", "20:00"),
        ("12:00", "23:00"),
        ("11:00", "18:00"),  # closes early - useful for F4
    ]
    places = []
    for i in range(1, 26):
        cat = categories[(i - 1) % len(categories)]
        feats = feature_patterns[(i - 1) % len(feature_patterns)].copy()
        # ensure variety: make every feature appear as true somewhere
        if i == 25:
            feats = {"dog": True, "laptop": True, "children": True, "terrace": True, "reservations": True}
        if i == 24:
            feats = {"dog": True, "laptop": False, "children": False, "terrace": True, "reservations": False}
        open_t, close_t = hours_patterns[(i - 1) % len(hours_patterns)]
        # some places farther away
        dist = 200 + (i * 170) % 4800
        places.append({
            "id": f"place-{i:02d}",
            "name": f"Вымышленное место {i:02d}",
            "category": cat,
            "distance_m": dist,
            "hours": {"open": open_t, "close": close_t},
            "features": feats,
            "synthetic": True,
        })

    return {
        "synthetic": True,
        "generated_for": base.isoformat(),
        "timezone": "Asia/Vladivostok",
        "events": events,
        "places": places,
    }


def load_or_create_seed() -> dict:
    path = seed_path()
    if path.exists():
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)
    data = generate_seed()
    _ensure_dir(path)
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return data


def get_seed(refresh: bool = False) -> dict:
    # for runtime reads we can cache in module; but keep simple and deterministic
    # refresh=False means read existing without regeneration; load_or_create handles that
    return load_or_create_seed()


def find_event(seed: dict, event_id: str):
    for ev in seed.get("events", []):
        if ev.get("id") == event_id:
            return ev
    return None
