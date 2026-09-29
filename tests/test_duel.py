# -*- coding: utf-8 -*-
"""Пересчёт движков задним числом (predict.duel + web.comparison).

Главное, что здесь проверяется, — честность: модель, обученная позже целевого
дня, знает ответ, и такой день попадать в сравнение не должен ни при каких
обстоятельствах. Плюс арифметика денег и отбор строк, на которых сравнение
вообще имеет смысл.
"""
import sys
from datetime import date
from pathlib import Path

import pandas as pd

PROGNOZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROGNOZ / "src"))
sys.path.insert(0, str(PROGNOZ))

from prognoz.predict import ml_core                           # noqa: E402
from prognoz.predict.guard import model_sanity                # noqa: E402

TARGET = date(2026, 7, 20)


def _stamp(cutoff: str, rows: int = 50_000) -> dict:
    return {"train_cutoff": cutoff, "n_train_rows": rows,
            "feats_version": ml_core.FEATS_VERSION,
            "gbm_params_hash": ml_core._gbm_params_hash(),
            "lag": ml_core.LAG, "quantile": ml_core.Q,
            "excluded_hash": ml_core._excluded_hash(),
            "assortment_policy_sha256": ml_core.assortment_provenance()[
                "assortment_policy_sha256"],
            "sklearn_version": __import__("sklearn").__version__}


def _preds() -> pd.DataFrame:
    return pd.DataFrame({"mu_ml": [3.0, 5.0], "r28_mean": [2.0, 4.0],
                         "wd_mean": [2.5, 4.5], "lag_recent": [3.0, 4.0]})


# --- честность модели ---------------------------------------------------------

def test_model_from_future_is_rejected():
    """Модель обучена ПОЗЖЕ цели — сторож обязан это поймать."""
    problems = model_sanity(_stamp("2026-08-10"), None, _preds(), TARGET)
    assert any("будущее" in p for p in problems), problems


def test_model_on_the_edge_is_fine():
    """Ровно target − lag — допустимо: это и есть прод-горизонт."""
    assert model_sanity(_stamp("2026-07-17"), None, _preds(), TARGET) == []


def test_refit_forced_when_stamp_is_newer_than_target():
    """Отрицательный возраст штампа = переобучение, а не «модель годна»."""
    cutoff = TARGET - pd.Timedelta(days=ml_core.LAG).to_pytimedelta()
    reason = ml_core._needs_refit(_stamp("2026-08-10"), cutoff, ml_core.MlCfg())
    assert reason and "позже цели" in reason


def test_model_dir_sandbox(monkeypatch, tmp_path):
    """Пересчёт уводит модели в песочницу — боевую папку не трогает."""
    monkeypatch.delenv("PROGNOZ_ML_MODEL_DIR", raising=False)
    assert ml_core.model_dir().name == "ml_model"
    monkeypatch.setenv("PROGNOZ_ML_MODEL_DIR", str(tmp_path))
    assert ml_core.model_dir() == tmp_path
    assert ml_core.model_dir(3) == tmp_path
    assert ml_core.model_dir(4) == tmp_path / "lag_4"
    assert ml_core.model_dir(5) == tmp_path / "lag_5"


def test_long_horizon_hgb_fallback_cannot_replace_d2(monkeypatch, tmp_path):
    monkeypatch.setenv("PROGNOZ_ML_MODEL_DIR", str(tmp_path))
    base = {"train_cutoff": "2026-08-20"}
    ml_core.save_model({"horizon": 2}, base, ml_core.MlCfg(lag=3))
    d2_hash = ml_core.hashlib.sha256(
        (tmp_path / "model.joblib").read_bytes()).hexdigest()

    ml_core.save_model({"horizon": 4}, base, ml_core.MlCfg(lag=5))

    assert ml_core.hashlib.sha256(
        (tmp_path / "model.joblib").read_bytes()).hexdigest() == d2_hash
    assert (tmp_path / "lag_5" / "model.joblib").is_file()


# --- отбор строк и деньги (web.comparison) ------------------------------------

def _rows() -> pd.DataFrame:
    return pd.DataFrame({
        "baseline": [10.0, 10.0, 10.0, 10.0],
        "ml": [12.0, 12.0, 12.0, 12.0],
        "human": [8.0, 0.0, 8.0, 8.0],
        "sold": [9.0, 9.0, 9.0, 0.0],
        "demand": [11.0, 11.0, 11.0, 0.0],
        "price": [100.0] * 4,
        "supply_gap": [False, False, True, False],
        "wo_settled": [True, True, True, True],
        "no_report": [False, False, False, True],
        "has_order": [True, False, True, True],
    })


def test_clean_drops_gap_and_no_report():
    from web import comparison                                # noqa: PLC0415
    out = comparison._clean(_rows(), "demand")
    assert len(out) == 2                       # срыв поставки и день без отчёта — вон


def test_money_matches_shared_shares():
    """Деньги считаются теми же долями, что на «Точности»: 0.33 / 0.67."""
    from web import comparison                                # noqa: PLC0415
    d = _rows().head(1)                        # прогноз 12, спрос 11, цена 100
    m = comparison._money(d, "ml", "demand")
    assert m["over_k"] == round(1 * 100 * comparison.OVER_COST_SHARE / 1000, 1)
    assert m["under_k"] == 0.0
    m2 = comparison._money(d, "human", "demand")   # заявка 8 при спросе 11
    assert m2["under_k"] == round(3 * 100 * comparison.UNDER_MARGIN_SHARE / 1000, 1)


def test_wape_and_bias():
    from web import comparison                                # noqa: PLC0415
    d = _rows().head(1)
    assert comparison._wape(d, "ml", "demand") == round(100 * 1 / 11, 1)
    assert comparison._bias(d, "baseline", "demand") == round(-100 * 1 / 11, 1)


def test_top_gap_is_sorted_and_exposes_the_absolute_difference(monkeypatch):
    from web import comparison                                # noqa: PLC0415
    frame = pd.DataFrame([
        {"date": "2026-08-01", "canon_name": "Точка А", "sku_name": "А",
         "ml": 10.0, "baseline": 8.0, "human": 9.0, "sold": 9.0, "demand": 9.0},
        {"date": "2026-08-02", "canon_name": "Точка Б", "sku_name": "Б",
         "ml": 15.0, "baseline": 5.0, "human": 7.0, "sold": 8.0, "demand": 8.0},
    ])
    monkeypatch.setattr(comparison, "_slice", lambda *_a, **_k: frame)

    rows = comparison.top_gap("30d", None, 20, "demand")

    assert [row["sku"] for row in rows] == ["Б", "А"]
    assert [row["gap_units"] for row in rows] == [10, 2]


def test_top_gap_matches_displayed_units_and_breaks_ties_stably(monkeypatch):
    from web import comparison                                # noqa: PLC0415
    frame = pd.DataFrame([
        {"date": "2026-08-01", "canon_name": "Точка Б", "sku_name": "Я",
         "ml": 10.4, "baseline": 10.0, "human": 9.0, "sold": 9.0,
         "demand": 9.0},
        {"date": "2026-08-02", "canon_name": "Точка Б", "sku_name": "Б",
         "ml": 12.0, "baseline": 10.0, "human": 9.0, "sold": 9.0,
         "demand": 9.0},
        {"date": "2026-08-02", "canon_name": "Точка А", "sku_name": "А",
         "ml": 8.0, "baseline": 10.0, "human": 9.0, "sold": 9.0,
         "demand": 9.0},
    ])
    monkeypatch.setattr(comparison, "_slice", lambda *_a, **_k: frame)

    rows = comparison.top_gap("30d", None, 20, "demand")

    assert [(row["point"], row["sku"], row["gap_units"]) for row in rows] == [
        ("Точка А", "А", 2),
        ("Точка Б", "Б", 2),
        ("Точка Б", "Я", 0),
    ]
    assert rows[-1]["ml"] == rows[-1]["baseline"] == 10
