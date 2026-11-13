# -*- coding: utf-8 -*-
"""Граница загруженных данных: дни после последнего дня с продажами — «данных ещё
нет», а не «не продавалось» (регрессия 13–17.09: заявки на день вперёд помечали
весь as_of-день разрывом поставки, ядро теряло lag_recent у 100% пар)."""
from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd
import pytest

from prognoz.predict import catboost_core
from prognoz.predict import stacked_guard_core as guard
from prognoz.shelf.core import flag_data_missing, flag_supply_gaps, last_complete_day


def _shelf(last_sales_day: str, end: str, n_sku: int = 6) -> pd.DataFrame:
    days = pd.date_range("2026-06-01", end, freq="D")
    rows = []
    for sku in range(n_sku):
        for d in days:
            known = d <= pd.Timestamp(last_sales_day)
            rows.append({
                "point_key": "P", "sku_key": f"S{sku}", "trade_day": d,
                "grp": "готовая еда",
                "sold_units": float((d.dayofyear + sku) % 5 + 1) if known else 0.0,
                "n_lines": 3 if known else 0,
                "lost_units": 0.0, "u40_units": 0.0, "wo_units": 0.0,
                "ordered_human": 4.0, "avg_price": 199.0,
                "outcome": "продавалось" if known else "не продавалось",
                "supply_gap": False, "wo_settled": True,
            })
    return pd.DataFrame(rows)


def test_days_after_last_sales_day_are_data_missing_not_supply_gap():
    df = _shelf("2026-08-14", "2026-08-16")
    assert last_complete_day(df) == pd.Timestamp("2026-08-14")
    gaps = flag_supply_gaps(df)                      # ≥5 SKU с заявкой и нулём продаж
    assert gaps[df["trade_day"] > "2026-08-14"].all()
    missing = flag_data_missing(df)
    assert missing[df["trade_day"] > "2026-08-14"].all()
    assert not missing[df["trade_day"] <= "2026-08-14"].any()
    # в build() разрыв поставки снимается там, где данных просто нет
    df["data_missing"] = missing
    df.loc[df["data_missing"], "supply_gap"] = False
    assert not df.loc[df["data_missing"], "supply_gap"].any()


def test_effective_lag_falls_back_to_last_complete_day():
    df = _shelf("2026-08-14", "2026-08-16")
    df["data_missing"] = flag_data_missing(df)
    target = date(2026, 8, 19)                       # номинальный лаг 3 → as_of 16.08
    as_of, eff = guard.effective_lag(df, target, 3)
    assert (as_of, eff) == (date(2026, 8, 14), 5)
    # данных нет всю неделю — выпуск ML невозможен (фолбэк, а не тихий ноль)
    with pytest.raises(guard.BundleError, match="слишком далёк"):
        guard.effective_lag(df, date(2026, 8, 22), 3)
    # полка без служебной колонки и без чеков: граница — по продажам
    plain = _shelf("2026-08-16", "2026-08-16").drop(columns=["n_lines"])
    assert guard.effective_lag(plain, target, 3) == (date(2026, 8, 16), 3)


def test_panel_takes_recent_lag_from_last_complete_day():
    df = _shelf("2026-08-14", "2026-08-16")
    df["data_missing"] = flag_data_missing(df)
    target = date(2026, 8, 19)
    panel = guard._full_feature_panel(df, target, 3, compact=False)
    assert panel.attrs["effective_lag"] == 5 and panel.attrs["data_as_of"] == "2026-08-14"
    row = panel[(panel["day"] == pd.Timestamp(target)) & (panel["sku_key"] == "S0")].iloc[0]
    assert not np.isnan(row["lag5"])                 # последний полный день известен
    assert row["lag3"] == row["lag5"]                # адаптер кладёт его в слот бандла
    # признаки за дни без данных — NaN, а не ноль
    raw = catboost_core.build_panel(df, target, as_of=date(2026, 8, 16))
    tail = raw[(raw["sku_key"] == "S0") & (raw["day"] > "2026-08-14") & (raw["day"] <= "2026-08-16")]
    assert tail["feature_dem"].isna().all()
