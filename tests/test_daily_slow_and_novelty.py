# -*- coding: utf-8 -*-
"""Правила 10.09: потолок медленных позиций и пол новинок только для новых в сети."""
from datetime import date, timedelta

import pandas as pd
import pytest

from prognoz.predict import daily


def test_slow_cap_limits_rare_items(monkeypatch):
    monkeypatch.delenv("PROGNOZ_SLOW_RULE", raising=False)
    qty, note = daily.slow_demand_cap(9, sold28=3, lost28=0, n_obs_days=30)
    assert qty == 1 and "медленная" in note
    qty, note = daily.slow_demand_cap(5, sold28=10, lost28=2, n_obs_days=30)
    assert qty == 2 and note
    # выше 14 шт за 28 дней — правило не трогает
    assert daily.slow_demand_cap(6, sold28=15, lost28=0, n_obs_days=30) == (6, None)
    # уже ниже потолка — без изменений
    assert daily.slow_demand_cap(1, sold28=3, lost28=0, n_obs_days=30) == (1, None)


def test_slow_cap_skips_novelties_zero_and_switch(monkeypatch):
    monkeypatch.delenv("PROGNOZ_SLOW_RULE", raising=False)
    assert daily.slow_demand_cap(6, sold28=3, lost28=0, n_obs_days=5) == (6, None)
    assert daily.slow_demand_cap(6, sold28=0, lost28=0, n_obs_days=30) == (6, None)
    monkeypatch.setenv("PROGNOZ_SLOW_RULE", "0")
    assert daily.slow_demand_cap(9, sold28=3, lost28=0, n_obs_days=30) == (9, None)


def test_network_new_skus_by_first_sale():
    target = date(2026, 9, 11)
    sales = pd.DataFrame({
        "point_key": ["a", "b", "a", "b"],
        "sku_key": ["old", "old", "new", "fresh"],
        "d": [target - timedelta(days=200), target - timedelta(days=5),
              target - timedelta(days=10), target - timedelta(days=21)],
        "qty": [1, 1, 1, 1],
    })
    new = daily.network_new_skus(sales, target)
    assert "new" in new and "fresh" in new
    assert "old" not in new          # старая позиция, впервые проданная на точке b — не новинка
    assert daily.network_new_skus(sales.iloc[0:0], target) == set()
