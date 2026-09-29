from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from prognoz.report import morning
from prognoz.predict.daily import release_engine_matches
from web import accuracy, data
from web.ai import chat
from web.export import _engine_label


@pytest.mark.parametrize("engine", ["ml", "catboost", "stacked_guard"])
def test_engine_epoch_accepts_each_ml_family(monkeypatch, tmp_path: Path, engine: str):
    pd.DataFrame({"engine": ["baseline"]}).to_parquet(
        tmp_path / "2026-08-13.parquet")
    pd.DataFrame({"engine": [engine]}).to_parquet(
        tmp_path / "2026-08-14.parquet")
    monkeypatch.setattr(data, "FORECAST_LOG", tmp_path)
    accuracy.engine_epoch.cache_clear()
    assert accuracy.engine_epoch() == date(2026, 8, 14)


def test_engine_epoch_only_baseline_uses_fallback(monkeypatch, tmp_path: Path):
    pd.DataFrame({"engine": ["baseline"]}).to_parquet(
        tmp_path / "2026-08-13.parquet")
    monkeypatch.setattr(data, "FORECAST_LOG", tmp_path)
    accuracy.engine_epoch.cache_clear()
    assert accuracy.engine_epoch() == accuracy.ENGINE_EPOCH_FALLBACK


@pytest.mark.parametrize(
    ("engine", "label"),
    [
        ("ml", "ML-ядро"),
        ("catboost", "ML-ядро · CatBoost"),
        ("stacked_guard", "ML-ядро · ансамбль"),
        ("baseline", "Резерв: базовая формула"),
        ("future_engine", "движок future_engine"),
    ],
)
def test_export_engine_labels(engine: str, label: str):
    assert _engine_label(engine) == label


def _morning_context(engine: str) -> dict:
    return {
        "yesterday": None,
        "today_order": {
            "target_date": "2026-08-25",
            "weekday": "вторник",
            "engine": engine,
            "total_units": 10,
            "rows": 1,
            "novelty_rows": 0,
            "by_point": {"Точка": 10},
            "guard_clipped_points": [],
        },
        "alert_today": None,
    }


@pytest.mark.parametrize("engine", ["ml", "catboost", "stacked_guard"])
def test_morning_labels_ml_family_as_ml(engine: str):
    rendered = "\\n".join(morning._fmt_table(_morning_context(engine)))
    assert "Движок: ML-ядро" in rendered


def test_morning_labels_baseline_as_reserve_formula():
    rendered = "\\n".join(morning._fmt_table(_morning_context("baseline")))
    assert "Движок: Резерв: базовая формула" in rendered
    assert morning.ENGINE_LABELS["stacked_guard"] == "ML-ядро · ансамбль"


def test_morning_does_not_call_unknown_engine_baseline():
    rendered = "\\n".join(morning._fmt_table(_morning_context("future_engine")))
    assert "Движок: движок future_engine" in rendered
    assert "базовая формула" not in rendered


def test_order_page_recognises_current_ml_engines():
    page = (Path(__file__).parents[1] / "web" / "static" / "index.html").read_text(
        encoding="utf-8")
    assert "['ml', 'catboost', 'stacked_guard']" in page
    assert "else if (m.engine === 'baseline')" in page
    assert "ML-ядро · ансамбль" in page
    assert "резерв: формула КВП" in page
    assert "архив: старый движок" not in page
    assert "ML-ядро в эту ночь не прошло проверку" in page


def test_ai_method_notes_describe_stacked_guard_contract():
    notes = chat.METHOD_NOTES
    for value in ("engine: baseline", "ml", "catboost", "engine: stacked_guard"):
        assert value in notes
    assert "meta_uplift >= 1" in notes
    assert "оба CatBoost >= HGB + 1" in notes
    assert "поле engine: baseline / ml" not in notes


def test_release_gate_rejects_silent_model_fallback():
    assert release_engine_matches(
        pd.DataFrame({"engine": ["stacked_guard", "stacked_guard"]}),
        "stacked_guard",
    )
    assert not release_engine_matches(
        pd.DataFrame({"engine": ["ml"]}), "stacked_guard")
    assert not release_engine_matches(
        pd.DataFrame({"engine": ["baseline"]}), "stacked_guard")
    assert not release_engine_matches(pd.DataFrame(), "stacked_guard")
