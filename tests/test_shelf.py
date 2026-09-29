# -*- coding: utf-8 -*-
"""Методика «полки»: классификация исходов и деньги — на синтетике и артефактах."""
import json
import sys
from pathlib import Path

import pandas as pd
import pytest

PROGNOZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROGNOZ / "src"))

from prognoz.shelf.core import classify, close_hours, day_rows  # noqa: E402
from prognoz.shelf.params import OUTCOMES, Params           # noqa: E402

FACTS = PROGNOZ / "analysis" / "output" / "facts.json"


def _day(**kw) -> dict:
    base = dict(sold_units=5.0, u40_units=0.0, wo_units=0.0,
                t_last=20.5, close_h=22.0, early_gap_h=1.5,
                t_u40_first=None, markdown_evening_h=3.0)
    base.update(kw)
    return base


def test_classify_priorities():
    df = pd.DataFrame([
        _day(wo_units=3),                        # списание бьёт всё
        _day(wo_units=1, u40_units=4),           # списание бьёт уценку
        _day(u40_units=2, t_u40_first=20.6),     # вечерняя уценка
        _day(sold_units=0, t_last=None),         # не продавалось
        _day(t_last=15.0),                       # рано замолчала
        _day(t_last=21.0),                       # дожила до вечера
    ])
    got = list(classify(df))
    assert got == ["остаток списан", "остаток списан", "остаток ушёл в уценку",
                   "не продавалось", "кончилось рано", "распродано под закрытие"]


def test_fractional_writeoff_gates_deficit():
    """0,3 списанной единицы = товар вечером был: день НЕ дефицитный.

    Порог >=0.5 давал +6,6 тыс шт фантомного дефицита (сверка 08.08).
    Дробная уценка гейтит так же — но только ВЕЧЕРНЯЯ (находка 5)."""
    df = pd.DataFrame([_day(t_last=14.0, wo_units=0.3),
                       _day(t_last=14.0, u40_units=0.2, t_u40_first=20.5)])
    got = list(classify(df))
    assert got == ["остаток списан", "остаток ушёл в уценку"]


def test_daytime_markdown_does_not_gate_deficit():
    """Гейт уценки — только вечерняя с наблюдаемым временем (находка 5, 08.08).

    Дневная дробная у40 (скидочная программа −10%, реконструированная как у40)
    не доказывает, что вечером товар был на полке: позиция кончилась в 14:00 —
    день дефицитный. Вечерняя у40 (в окне close−3ч) — гейтит. Если чеки по дню
    есть, а строки со скидкой >35% нет (u40_strict=0) — «уценки могло не быть
    вовсе» (вывод панели): НЕ гейтит. Совсем без чеков (сеты) — время не судим,
    у40 гейтит как раньше."""
    df = pd.DataFrame([
        _day(t_last=14.0, u40_units=0.5, t_u40_first=12.5),   # дневная → дефицит
        _day(t_last=14.0, u40_units=0.5, t_u40_first=20.0),   # вечерняя → уценка
        _day(t_last=14.0, u40_units=0.5, t_u40_first=None),   # чеки есть, у40-строки
                                                              # нет → дефицит
        _day(t_last=14.0, u40_units=0.5, t_u40_first=12.5,
             wo_units=1.0),                                   # списание бьёт всё
        _day(t_last=21.0, u40_units=2.0, t_u40_first=12.5),   # дневная у40, дожила
        _day(t_last=None, sold_units=3.0, u40_units=1.0,
             t_u40_first=None),                               # сет без чеков → уценка
    ])
    got = list(classify(df))
    assert got == ["кончилось рано", "остаток ушёл в уценку",
                   "кончилось рано", "остаток списан",
                   "распродано под закрытие", "остаток ушёл в уценку"]


def test_supply_gap_flags_mass_zero_days():
    """Точко-день, где «заявка была, продаж ноль» сразу у ≥10 позиций — срыв
    поставки, а не 10 совпадений. Флаг ставится всем строкам этого точко-дня."""
    from prognoz.shelf.core import flag_supply_gaps
    rows = []
    for i in range(12):   # день срыва: 12 молчащих позиций с заявкой
        rows.append(dict(point_key="P1", trade_day="2026-01-10",
                         outcome="не продавалось", ordered_human=3.0))
    rows.append(dict(point_key="P1", trade_day="2026-01-10",
                     outcome="распродано под закрытие", ordered_human=2.0))
    for i in range(3):    # обычный день: 3 молчащих — не срыв
        rows.append(dict(point_key="P1", trade_day="2026-01-11",
                         outcome="не продавалось", ordered_human=1.0))
    df = pd.DataFrame(rows)
    got = flag_supply_gaps(df, min_skus=10)
    assert got[:13].all()          # весь точко-день срыва помечен
    assert not got[13:].any()      # обычный день — нет


def test_outcomes_cover_everything():
    """Классификация тотальна: любой день получает ровно один исход."""
    import numpy as np
    rng = np.random.default_rng(7)
    n = 500
    df = pd.DataFrame({
        "sold_units": rng.integers(0, 10, n).astype(float),
        "u40_units": rng.choice([0, 0, 0, 1, 2.5], n),
        "wo_units": rng.choice([0, 0, 0, 0.3, 4], n),
        "t_last": rng.uniform(10, 23, n),
        "t_u40_first": rng.choice([None, 12.0, 20.5], n),
        "close_h": 22.5, "early_gap_h": 1.5, "markdown_evening_h": 3.0})
    df.loc[df["sold_units"] == 0, "t_last"] = None
    out = classify(df)
    assert out.isin(OUTCOMES).all()
    assert len(out) == n


def test_params_fingerprint_changes():
    assert Params().fingerprint() != Params(signif_units=3.0).fingerprint()


class _QueryResult:
    def __init__(self, *, scalar=None, frame=None):
        self.scalar = scalar
        self.frame = pd.DataFrame() if frame is None else frame

    def fetchone(self):
        return [self.scalar]

    def fetchdf(self):
        return self.frame


class _QuerySpy:
    def __init__(self, wo_max=None):
        self.wo_max = wo_max
        self.calls = []

    def execute(self, sql, params=None):
        self.calls.append((sql, params))
        if "max(wo_date)" in sql:
            return _QueryResult(scalar=self.wo_max)
        return _QueryResult()


def test_shelf_queries_are_bounded_by_requested_asof():
    params = Params(since=pd.Timestamp("2026-01-01").date(),
                    until=pd.Timestamp("2026-01-31").date())
    close_spy = _QuerySpy()
    close_hours(close_spy, params)
    assert close_spy.calls[-1][1] == [params.since, params.until]
    assert "trade_day BETWEEN ? AND ?" in close_spy.calls[-1][0]

    day_spy = _QuerySpy(wo_max=pd.Timestamp("2026-01-29").date())
    day_rows(day_spy, params)
    watermark_sql, watermark_params = day_spy.calls[0]
    assert "wo_date <= ?" in watermark_sql
    assert watermark_params == [params.until]


@pytest.fixture(scope="module")
def facts() -> dict:
    if not FACTS.exists():
        pytest.skip("facts.json не собран")
    return json.loads(FACTS.read_text(encoding="utf-8"))["facts"]


def test_shelf_hour_gap_is_large(facts):
    """Самопроверка разметки: дефицитные дни замолкают на ЧАСЫ раньше дней
    с уценкой. Если разрыв схлопнулся — классификация сломана."""
    assert facts["shelf.hour.gap"]["value"] >= 2.0


def test_shelf_outcome_shares_sum_to_100(facts):
    keys = ("early", "clean", "markdown", "writeoff", "silent")
    total = sum(facts[f"shelf.outcome.{k}_share"]["value"] for k in keys)
    assert total == pytest.approx(100.0, abs=0.5)


def test_miss_excludes_markdown(facts):
    """«Цена промаха» складывает ровно две потери — уценка справочна."""
    miss = facts["shelf.year.miss_rub_m"]["value"]
    lost = facts["shelf.year.lost_rub_m"]["value"]
    wo = facts["shelf.year.wo_rub_m"]["value"]
    assert miss == pytest.approx(lost + wo, abs=0.15)
