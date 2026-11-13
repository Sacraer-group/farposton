"""Rolling demo forecasts based on synthetic history and recursive predictions."""
from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import closing
from datetime import datetime, timedelta
from functools import lru_cache
from pathlib import Path
from urllib.parse import quote

import joblib
import numpy as np

from .places import ROOT
from .places_history import TIMEZONE, daily_values
from zoneinfo import ZoneInfo

MODEL_PATH = ROOT / ".demo" / "venue-ensemble-extended-20261113-v1" / "ensemble.joblib"
HISTORY_PATH = ROOT / "data" / "2gis" / "demo-history-extended-v1" / "history.sqlite3"
FEATURES = ["weekday", "hour", "lag_1d", "lag_2d", "lag_7d", "mean_past_7d"]


def _readonly(path: Path) -> str:
    return "file:" + quote(str(path.resolve()), safe="/") + "?mode=ro"


def _file_signature(path: Path):
    stat = path.stat()
    return str(path.resolve()), stat.st_size, stat.st_mtime_ns


@lru_cache(maxsize=2)
def _load_verified(model_signature, history_signature):
    model_path, history_path = Path(model_signature[0]), Path(history_signature[0])
    # Only load the locally generated trusted model, never a user-supplied file.
    model = joblib.load(model_path)
    metadata = model.get("metadata", {})
    if model.get("synthetic_only") is not True or metadata.get("synthetic_only") is not True:
        raise ValueError("Модель не помечена как демонстрационная")
    if model.get("features") != FEATURES or not 0 <= model.get("hgb_weight", -1) <= 1:
        raise ValueError("Несовместимый формат модели")
    with history_path.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    if digest != metadata.get("source_sha256"):
        raise ValueError("История не соответствует модели")
    return model


def _predict_day(model, history: np.ndarray, target_date):
    """Use only the preceding seven days; later predictions are fed back as predictions."""
    hours = np.flatnonzero(np.isfinite(history[0]))
    hourly = [None] * 24
    if len(hours):
        prior = history[:, hours]
        means = np.nansum(prior, axis=0) / np.isfinite(prior).sum(axis=0)
        x = np.column_stack((np.full(len(hours), target_date.weekday()), hours, history[-1, hours],
                             history[-2, hours], history[0, hours], means)).astype(np.float32)
        np.nan_to_num(x, copy=False, nan=-1.0)
        weight = model["hgb_weight"]
        prediction = weight * model["hgb"].predict(x) + (1 - weight) * model["extra_trees"].predict(x)
        if not np.isfinite(prediction).all():
            raise ValueError("Модель вернула некорректный прогноз")
        for hour, value in zip(hours, np.clip(prediction, 0, 100)):
            hourly[int(hour)] = int(round(float(value)))
    return hourly


def forecast(place: dict, *, today=None):
    model = _load_verified(_file_signature(MODEL_PATH), _file_signature(HISTORY_PATH))
    with closing(sqlite3.connect(_readonly(HISTORY_PATH), uri=True)) as conn:
        metadata = {key: json.loads(value) for key, value in conn.execute("SELECT key, value FROM metadata")}
        if metadata.get("synthetic") is not True or metadata.get("unit") != "synthetic_index_0_100":
            raise ValueError("История не помечена как демонстрационная")
        if metadata["end_date"] != model["metadata"]["split"]["test"]["end"]:
            raise ValueError("Дата истории не соответствует модели")
    today = today or datetime.now(ZoneInfo(TIMEZONE)).date()
    # These values are freshly simulated, never treated as measured occupancy.
    history = np.array([[np.nan if value is None else value for value in daily_values(place, today - timedelta(days=offset))[0]]
                        for offset in range(7, 0, -1)], dtype=np.float32)
    days = []
    for offset in range(7):
        target = today + timedelta(days=offset)
        hourly = _predict_day(model, history, target)
        days.append({"date": target.isoformat(), "hourly": hourly})
        predicted = np.array([np.nan if value is None else value for value in hourly], dtype=np.float32)
        history = np.vstack((history[1:], predicted))
    return {"synthetic": True, "unit": "synthetic_index_0_100", "model": "ensemble",
            "date": days[0]["date"], "hourly": days[0]["hourly"], "days": days,
            "history_through": (today - timedelta(days=1)).isoformat(),
            "history_type": "synthetic_generated", "method": "recursive_next_day",
            "timezone": TIMEZONE}
