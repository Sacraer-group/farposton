# -*- coding: utf-8 -*-
"""Лестница на синтетике + согласованность констант + сдвиг дня недели.

Три класса ошибок, которые уже случались в проекте:
- лестница считалась по-разному в разных местах (тест на синтетике фиксирует формулу);
- scope.json правился руками и разошёлся с кодом (тест сверяет генерацию с файлом);
- DuckDB нумерует дни недели с воскресенья, Python — с понедельника (сверка
  сравнивала понедельник со вторниками, грабля задокументирована, теперь и прибита).
"""
import json
import sys
from datetime import date, timedelta
from pathlib import Path

import duckdb
import pandas as pd
import pytest

SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))
from prognoz.facts.blocks.ladder import (        # noqa: E402
    bias, drop_dead_days, ladder_table, paired, wape,
)
from prognoz.facts.blocks import scope_block     # noqa: E402
from prognoz.facts.fmt import fmt_ru, parse_ru   # noqa: E402


def synth() -> pd.DataFrame:
    """Два дня, две точки, две позиции — WAPE и bias пересчитываются руками."""
    rows = []
    for d in (date(2026, 7, 1), date(2026, 7, 2)):
        rows += [
            # точка A: прогноз 8/факт 10 (−2), человек 12 (+2)
            {"date": d, "_point_key": "A", "_sku_key": "x", "canon_name": "A",
             "cat": "шаурму", "Прогноз, шт": 8.0, "actual": 10.0, "human": 12.0,
             "u40": 1.0},
            # точка A: прогноз 6/факт 5 (+1), человек 5 (0)
            {"date": d, "_point_key": "A", "_sku_key": "y", "canon_name": "A",
             "cat": "супы", "Прогноз, шт": 6.0, "actual": 5.0, "human": 5.0,
             "u40": 0.0},
        ]
    return pd.DataFrame(rows)


def test_wape_bias_by_hand():
    df = synth()
    p = paired(df)
    # позиция: промахи |−2|+|+1| на факт 15 → 20%; со знаком −1/15 → −6,7%
    g = p.groupby(["date", "_point_key", "_sku_key"])[
        ["Прогноз, шт", "actual", "human"]].sum().reset_index()
    assert wape(g) == pytest.approx(20.0, abs=0.01)
    assert bias(g) == pytest.approx(-6.667, abs=0.01)
    # точка×день: 14 против 15 → промах 1, WAPE 6,67%
    g2 = p.groupby(["date", "_point_key"])[
        ["Прогноз, шт", "actual", "human"]].sum().reset_index()
    assert wape(g2) == pytest.approx(6.667, abs=0.01)
    # человек: 17 против 15 → +13,3% на точке
    assert bias(g2, "human") == pytest.approx(13.333, abs=0.01)


def test_ladder_table_levels_and_floor():
    lt = ladder_table(synth())
    assert list(lt["level"]) == ["sku_day", "group_day", "point_day", "net_day"]
    # пол растёт к мелким уровням: √-закон. В синтетике одна точка, поэтому
    # point_day и net_day совпадают — сравнение нестрогое.
    floors = lt.set_index("level")["floor"]
    assert floors["sku_day"] > floors["point_day"] >= floors["net_day"]
    assert floors["sku_day"] == pytest.approx(79.8 / 7.5 ** 0.5, abs=0.1)


def test_dead_days_are_dropped_not_counted():
    df = synth()
    dead = df.iloc[:1].copy()
    dead["date"] = date(2026, 7, 3)
    dead["actual"] = 0.0
    out, n = drop_dead_days(pd.concat([df, dead], ignore_index=True))
    assert n == 1
    assert date(2026, 7, 3) not in set(out["date"])


def test_scope_json_matches_generation():
    """scope.json — производная кода. Ручная правка файла или забытая
    перегенерация после смены констант daily.py валит этот тест."""
    on_disk = json.loads((scope_block.CONFIG / "scope.json").read_text("utf-8"))
    live = scope_block.snapshot()
    assert on_disk["scope"] == live["scope"]
    assert on_disk["engine"] == live["engine"]
    assert on_disk["protocol"] == live["protocol"]


def test_weekday_shift_duckdb_vs_python():
    """dayofweek(DuckDB): 0=вс..6=сб; weekday(Python): 0=пн..6=вс.
    Контракт перевода: dayofweek(d) == (d.weekday() + 1) % 7."""
    con = duckdb.connect()
    d0 = date(2026, 8, 3)
    for i in range(14):
        d = d0 + timedelta(days=i)
        duck = con.execute("SELECT dayofweek(CAST(? AS DATE))", [d]).fetchone()[0]
        assert duck == (d.weekday() + 1) % 7, f"расхождение на {d}"
    con.close()


def test_fmt_ru_roundtrip():
    assert fmt_ru(10.24) == "10,2"
    assert fmt_ru(1918, 0) == "1 918"
    assert parse_ru("+10,2 %") == pytest.approx(10.2)
    assert parse_ru("−1,3%") == pytest.approx(-1.3)
    assert parse_ru("36,0 млн ₽/год") == pytest.approx(36.0)
    assert parse_ru("1 918 шт") == pytest.approx(1918)
    assert parse_ru("18–26%") == pytest.approx(18)
