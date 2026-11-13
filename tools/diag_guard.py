# -*- coding: utf-8 -*-
"""Диагностика ML-сторожа: почему панель пуста и какой лаг фактически используется.

Запуск (окружение как у ночного цикла — PROGNOZ_DB_PATH, APP_OUTPUT_ROOT и т.д.):
    python tools/diag_guard.py --issue-date 2026-09-17

Печатает по горизонтам D+2/D+3/D+4: последний полный день продаж, номинальный и
фактический лаг, число пар в панели / активных за 28 дней, долю пар без единого
опорного признака (r28_mean, wd_mean, lag_recent) — как считает сторож. Ничего не
пишет: полка берётся из кэша (shelf_rolling.parquet), при его отсутствии строится.
"""
from __future__ import annotations

import argparse
import sys
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "src"))
sys.path.insert(0, str(PROJECT / "analysis" / "etl"))

from _db import connect_reader  # noqa: E402
from prognoz.predict import stacked_guard_core as sg  # noqa: E402
from prognoz.predict.demand import load_shelf  # noqa: E402
from prognoz.predict.guard import NAN_SHARE_FAIL  # noqa: E402

FEATS = ("r28_mean", "wd_mean", "lag_recent")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--issue-date", type=date.fromisoformat, default=date.today())
    args = parser.parse_args()
    issue = args.issue_date
    con = connect_reader()
    try:
        shelf = load_shelf(con, issue)
    finally:
        con.close()
    shelf["trade_day"] = pd.to_datetime(shelf["trade_day"])
    complete = sg.last_complete_day(shelf)
    print(f"выпуск {issue}: полка {shelf['trade_day'].min().date()} → "
          f"{shelf['trade_day'].max().date()}, последний полный день продаж: {complete}, "
          f"строк {len(shelf)}, пар {shelf.groupby(['point_key', 'sku_key']).ngroups}")
    if "data_missing" in shelf.columns:
        print(f"  дней «данных ещё нет»: {int(shelf['data_missing'].sum())} строк; "
              f"разрывов поставки: {int(shelf['supply_gap'].sum())} строк")
    active_since = pd.Timestamp(issue - timedelta(days=29))
    active = set(zip(*[shelf.loc[shelf['trade_day'] >= active_since, c]
                       for c in ("point_key", "sku_key")]))
    for horizon, lag in sg_horizons():
        target = issue + timedelta(days=horizon)
        try:
            panel = sg._full_feature_panel(shelf, target, lag)
        except sg.BundleError as exc:
            print(f"D+{horizon} {target}: ОТКАЗ — {exc}")
            continue
        row = panel[panel["day"] == pd.Timestamp(target)].copy()
        row["lag_recent"] = row[f"lag{panel.attrs['effective_lag']}"]
        allp = row[list(FEATS)].isna().all(axis=1).mean()
        act = row[[(p, s) in active for p, s in zip(row["point_key"], row["sku_key"])]]
        act_share = act[list(FEATS)].isna().all(axis=1).mean() if len(act) else float("nan")
        verdict = "фолбэк" if act_share > NAN_SHARE_FAIL else "ML"
        print(f"D+{horizon} {target}: лаг {lag}→{panel.attrs['effective_lag']} "
              f"(данные по {panel.attrs['data_as_of']}); пар в панели {len(row)}, "
              f"активных {len(act)}; пустые признаки: по всей панели {allp:.0%}, "
              f"по активным {act_share:.0%} → {verdict}; "
              + ", ".join(f"{c} NaN {row[c].isna().mean():.0%}" for c in FEATS))
    return 0


def sg_horizons():
    return ((2, 3), (3, 4), (4, 5))


if __name__ == "__main__":
    raise SystemExit(main())
