# -*- coding: utf-8 -*-
"""Тесты экономики ошибки заказа.

Главный здесь — «тест на 3 строки», который ловит двойной счёт Co = 0,379P,
ушедший клиенту в первой редакции документа. Если кто-нибудь снова добавит
лишней единице «недополученную маржу с уценки», тест упадёт.
"""
import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from prognoz.facts.blocks.economics import (   # noqa: E402
    COST_RATIO, losses, multiplier_scan, optimal_quantile, unit_costs,
)


def test_unit_costs_are_symmetric_shares():
    co, cu = unit_costs(0.33)
    assert co == pytest.approx(0.33)
    assert cu == pytest.approx(0.67)
    # недозаказ дороже перезаказа ровно вдвое — не в 1,77 раза, как в отозванной формуле
    assert cu / co == pytest.approx(2.0303, abs=0.001)


def test_optimal_quantile():
    co, cu = unit_costs(0.33)
    assert optimal_quantile(co, cu) == pytest.approx(0.670, abs=0.001)
    # симметричные издержки дали бы медиану
    assert optimal_quantile(1.0, 1.0) == pytest.approx(0.5)


def test_three_line_trap_for_double_counting():
    """Заказ 10, продано 7 → излишек ровно 3 единицы по цене Co = 0,33P каждая.
    Отозванная формула 0,379P давала бы 1,137P вместо 0,99P."""
    rows = pd.DataFrame({
        "date": ["2026-08-01"], "actual": [7.0], "human": [10.0],
        "Прогноз, шт": [10.0],
    })
    d = losses(rows, "human", avg_price=100.0, cost_ratio=0.33)
    assert d["over_units"] == 3
    assert d["under_units"] == 0
    assert d["loss_p"] == pytest.approx(3 * 0.33, abs=0.01)


def test_zero_bias_is_not_zero_loss():
    """Вопрос сотрудника, с которого всё началось: недовезли 7 шаурмы, перевезли
    7 шаурмы — перекос ноль, а потерь ДВЕ, и недовоз дороже."""
    rows = pd.DataFrame({
        "date": ["2026-08-01"] * 2,
        "actual": [19.0, 8.0],
        "human": [12.0, 15.0],          # −7 и +7
        "Прогноз, шт": [12.0, 15.0],
    })
    d = losses(rows, "human", avg_price=100.0, cost_ratio=0.33)
    assert d["net_units"] == 0                       # перекос действительно ноль
    assert d["loss_p"] == pytest.approx(7 * 0.33 + 7 * 0.67, abs=0.01)
    # и потеря недовоза больше половины всей суммы
    assert 7 * 0.67 > d["loss_p"] / 2


def test_rows_do_not_offset_each_other():
    """Излишек шаурмы не закрывает дефицит шаурмы: потери считаются построчно."""
    two = pd.DataFrame({
        "date": ["2026-08-01"] * 2, "actual": [10.0, 10.0],
        "human": [5.0, 15.0], "Прогноз, шт": [5.0, 15.0],
    })
    one = pd.DataFrame({
        "date": ["2026-08-01"], "actual": [20.0],
        "human": [20.0], "Прогноз, шт": [20.0],
    })
    d2 = losses(two, "human", avg_price=100.0)
    d1 = losses(one, "human", avg_price=100.0)
    assert d1["loss_p"] == 0
    assert d2["loss_p"] > 0


def test_multiplier_scan_shape():
    rows = pd.DataFrame({
        "date": ["2026-08-01"] * 3, "actual": [4.0, 6.0, 2.0],
        "human": [5.0, 5.0, 3.0], "Прогноз, шт": [4.0, 5.0, 3.0],
    })
    sc = multiplier_scan(rows, avg_price=100.0, mults=(1.0, 1.2))
    assert list(sc["множитель"]) == [1.0, 1.2]
    # больший множитель на этих данных наращивает излишек
    assert sc.iloc[1]["over_units"] >= sc.iloc[0]["over_units"]


def test_cost_ratio_is_client_number():
    """0,33 — число базовой формулы (встреча 03.08). Смена — только решением, с правкой
    этого теста, а не тихо в константе."""
    assert COST_RATIO == 0.33
