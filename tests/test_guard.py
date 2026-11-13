# -*- coding: utf-8 -*-
"""Аварийный сторож ML-режима (predict.guard): клип, ALERT, санити модели."""
import sys
from datetime import date
from pathlib import Path

import pandas as pd

PROGNOZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROGNOZ / "src"))

from prognoz.predict import guard                            # noqa: E402
from prognoz.predict.guard import (DAY_GUARD, day_guard,     # noqa: E402
                                   model_sanity)

DAY = date(2026, 8, 13)


def _df(pt, qtys):
    return pd.DataFrame({
        "Точка": pt, "Позиция": [f"поз{i}" for i in range(len(qtys))],
        "Прогноз, шт": qtys, "Пояснение": "",
    })


def test_inside_frame_untouched(tmp_path, monkeypatch):
    monkeypatch.setattr(guard, "LOGS", tmp_path)
    df = _df("Т", [5, 7, 3])                        # итог 15 при ориентире 12
    out = day_guard(df, {"Т": 12.0}, DAY)
    assert list(out["Прогноз, шт"]) == [5, 7, 3]
    assert (out["_guard_scale"] == 1.0).all()
    assert not list(tmp_path.glob("ALERT-*"))       # рабочая норма — тихо


def test_runaway_clipped_to_frame_with_alert(tmp_path, monkeypatch):
    monkeypatch.setattr(guard, "LOGS", tmp_path)
    df = _df("Т", [20, 20, 20])                     # итог 60 = ×3 от 20 — авария
    out = day_guard(df, {"Т": 20.0}, DAY)
    goal = 20.0 * DAY_GUARD[1]
    assert out["Прогноз, шт"].sum() == round(goal)  # целые по наибольшим остаткам
    assert (out["_guard_scale"] < 1.0).all()
    assert (out["Пояснение"].str.contains("аварийным сторожем")).all()
    alerts = list(tmp_path.glob("ALERT-*"))
    assert alerts and "ML-СТОРОЖ" in alerts[0].read_text(encoding="utf-8")


def test_day_guard_never_scales_a_row_below_hgb_floor(tmp_path, monkeypatch):
    monkeypatch.setattr(guard, "LOGS", tmp_path)
    df = _df("Т", [90, 5, 6])
    df["_hgb_floor_qty"] = [90, 5, 5]
    out = day_guard(
        df, {"Т": 100 / DAY_GUARD[1]}, DAY,
        floor_col="_hgb_floor_qty")
    assert (out["Прогноз, шт"] >= out["_hgb_floor_qty"]).all()
    assert out["Прогноз, шт"].sum() == 100


def test_floor_wins_when_emergency_cap_is_below_hgb(tmp_path, monkeypatch):
    monkeypatch.setattr(guard, "LOGS", tmp_path)
    df = _df("Т", [91, 6, 6])
    df["_hgb_floor_qty"] = [90, 5, 5]
    out = day_guard(df, {"Т": 50}, DAY, floor_col="_hgb_floor_qty")
    assert (out["Прогноз, шт"] >= out["_hgb_floor_qty"]).all()
    assert out["Прогноз, шт"].sum() == 100
    alert_text = next(tmp_path.glob("ALERT-*")).read_text(encoding="utf-8")
    assert "HGB-floor" in alert_text


def test_info_zone_alerts_without_clip(tmp_path, monkeypatch):
    monkeypatch.setattr(guard, "LOGS", tmp_path)
    df = _df("Т", [11, 11, 11])                     # ×1.65: инфо-зона, не клип
    out = day_guard(df, {"Т": 20.0}, DAY)
    assert out["Прогноз, шт"].sum() == 33
    alerts = list(tmp_path.glob("ALERT-*"))
    assert alerts and "инфо" in alerts[0].read_text(encoding="utf-8")


def _stamp(cutoff=DAY, n=80_000):
    return {"train_cutoff": cutoff.isoformat(), "n_train_rows": n}


def _preds(mu=(3.0, 5.0)):
    return pd.DataFrame({"mu_ml": mu, "r28_mean": [2.5, 4.0],
                         "wd_mean": [3.0, 5.0], "lag_recent": [3.0, 6.0]})


def test_model_sanity_ok():
    assert model_sanity(_stamp(date(2026, 8, 10)), _stamp(), _preds(),
                        DAY) == []


def test_model_sanity_catches_problems():
    stale = model_sanity(_stamp(date(2026, 7, 1)), None, _preds(), DAY)
    assert any("старше" in p for p in stale)
    shrunk = model_sanity(_stamp(date(2026, 8, 10), n=10_000), _stamp(),
                          _preds(), DAY)
    assert any("схлопнулась" in p for p in shrunk)
    zeros = model_sanity(_stamp(date(2026, 8, 10)), _stamp(),
                         _preds(mu=(0.0, 0.0)), DAY)
    assert any("нулевые" in p for p in zeros)
    empty = model_sanity(_stamp(date(2026, 8, 10)), _stamp(),
                         _preds().iloc[:0], DAY)
    assert any("ни одной строки" in p for p in empty)
