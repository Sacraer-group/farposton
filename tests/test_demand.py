# -*- coding: utf-8 -*-
"""Правило восстановленного спроса (predict.demand): пороги, кап, отзыв.

Тождество прода закреплено отдельно: build() с выключенным флагом (по
умолчанию) даёт байт-в-байт прежние _mu — проверено на живых данных 09.08.2026
(423 строки, Σmu 1778.044); здесь — что выключенный флаг вообще не зовёт полку.
"""
import sys
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import pytest

PROGNOZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROGNOZ / "src"))

from prognoz.predict.demand import (BuildCfg, MIN_DEFICIT_DAYS,  # noqa: E402
                                    UPLIFT_CAP, uplift_lookup, uplift_table)

ASOF = date(2026, 7, 20)


def _pair_days(pt, sku, sold_per_day, lost_days, u40_total=0.0, n_days=28):
    """28 дней жизни пары: lost_days дней с дефицитом по 3 шт."""
    rows = []
    for i in range(n_days):
        d = ASOF - timedelta(days=n_days - i)
        rows.append(dict(point_key=pt, sku_key=sku, trade_day=d,
                         sold_units=float(sold_per_day),
                         lost_units=3.0 if i < lost_days else 0.0,
                         u40_units=u40_total / n_days,
                         wo_units=0.0, wo_settled=True))
    return rows


def test_threshold_and_cap():
    df = pd.DataFrame(
        _pair_days("P", "хроник", 10, lost_days=6)          # 6 дефицитных → активна
        + _pair_days("P", "редкий", 10, lost_days=MIN_DEFICIT_DAYS - 1)  # мало
        + _pair_days("P", "яма", 1, lost_days=10))          # lost >> sold → кап
    g = uplift_table(df, ASOF).set_index("sku_key")
    assert g.loc["хроник", "active"] and not g.loc["хроник", "revoked"]
    # 6 дней × 3 шт = 18 lost на 280 sold → +6,4%
    assert g.loc["хроник", "uplift"] == pytest.approx(1.064, abs=0.01)
    assert not g.loc["редкий", "active"]
    assert g.loc["редкий", "uplift"] == 1.0
    assert g.loc["яма", "uplift"] == UPLIFT_CAP              # кап +25%

def test_revoke_by_markdown_share():
    """Уценка >20% за окно = завоз уже догнал спрос — добавка отзывается."""
    df = pd.DataFrame(_pair_days("P", "s", 10, lost_days=6, u40_total=70.0))
    g = uplift_table(df, ASOF).set_index("sku_key")
    assert g.loc["s", "revoked"]
    assert g.loc["s", "uplift"] == 1.0
    assert "уценка" in g.loc["s", "reason"]


def test_revoke_by_writeoffs():
    """Два дня со списанием за 14 дней — отзыв (свежие акты, вне полки)."""
    df = pd.DataFrame(_pair_days("P", "s", 10, lost_days=6))
    wo = pd.Series({("P", "s"): 2})
    g = uplift_table(df, ASOF, wo_days14=wo).set_index("sku_key")
    assert g.loc["s", "revoked"] and g.loc["s", "uplift"] == 1.0


def test_no_leak_from_asof_day():
    """Строка самого дня asof в оценку не входит — прогноз её не знает."""
    base = _pair_days("P", "s", 10, lost_days=MIN_DEFICIT_DAYS - 1)
    leak = [dict(point_key="P", sku_key="s", trade_day=ASOF,
                 sold_units=10.0, lost_units=3.0, u40_units=0.0,
                 wo_units=0.0, wo_settled=True)]
    g = uplift_table(pd.DataFrame(base + leak), ASOF).set_index("sku_key")
    assert not g.loc["s", "active"]      # день asof не добавил 4-й дефицитный


def test_unsettled_days_ignored():
    """Дни без пришедших актов не считаются: там «кончилось рано» может
    оказаться списанием, когда акт доедет."""
    rows = _pair_days("P", "s", 10, lost_days=6)
    for r in rows[:10]:                  # дефицитные дни лежат в начале окна
        r["wo_settled"] = False
    g = uplift_table(pd.DataFrame(rows), ASOF).set_index("sku_key")
    assert not g.loc["s", "active"]      # все 6 дефицитных дней — в unsettled


def test_disabled_flag_never_touches_shelf():
    """Выключенный флаг (по умолчанию) не читает полку и не ходит в базу."""
    class Boom:
        def execute(self, *a, **k):
            raise AssertionError("выключенный флаг попытался пойти в базу")
    assert uplift_lookup(Boom(), ASOF, BuildCfg()) == {}
    assert BuildCfg().demand_recovery is False
