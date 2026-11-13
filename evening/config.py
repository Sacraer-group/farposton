from __future__ import annotations

import os
from pathlib import Path


def data_mode() -> str:
    mode = os.getenv("EVENING_DATA_MODE", "real").strip().lower()
    if mode not in {"real", "demo"}:
        return "real"
    return mode


def demo_dir() -> Path:
    return Path(os.getenv("EVENING_DEMO_DIR", ".demo/evening"))


def catalog_dir() -> Path | None:
    val = os.getenv("EVENING_CATALOG_DIR") or os.getenv("EVENING_PLACES_DATA_DIR")
    if not val:
        return None
    return Path(val)


def events_path() -> Path:
    val = os.getenv("EVENING_EVENTS_PATH")
    if val:
        return Path(val)
    return Path(__file__).resolve().parent / "data" / "vladivostok_events.json"


def is_real_mode() -> bool:
    return data_mode() == "real"
