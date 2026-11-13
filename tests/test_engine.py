# -*- coding: utf-8 -*-
"""Тесты движка методики базовой формулы. Запуск: PYTHONUTF8=1 py -m pytest prognoz/tests/ -q"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from prognoz.engine.baseline import Forecast, forecast, kvp_coef, trend, weighted_avg


def test_cezar_control_example():
    """Контрольный пример базовой формулы: цезарь → 19 шт (сверен с тестовым расчётом 23.07.2026)."""
    f = forecast(sales=[17, 15, 15, 13], orders=[17, 17, 15, 15])
    assert f.weighted_sales == pytest.approx(15.6)
    assert f.trend == pytest.approx(17 / 15)
    assert f.base_forecast == pytest.approx(15.6 * 17 / 15)      # 17.68 точно; 17,63 в примере — округления
    assert f.weighted_orders == pytest.approx(16.4)
    assert f.kvp == pytest.approx(15.6 / 16.4)                   # 95.12%
    assert f.kvp_coef == 1.10
    assert f.qty == 19


def test_weights_3_and_2_weeks():
    assert weighted_avg([10, 10, 10]) == pytest.approx(10)
    assert weighted_avg([20, 10, 10]) == pytest.approx(20 * 0.5 + 10 * 0.3 + 10 * 0.2)
    assert weighted_avg([20, 10]) == pytest.approx(20 * 0.6 + 10 * 0.4)
    with pytest.raises(ValueError):
        weighted_avg([1])
    with pytest.raises(ValueError):
        weighted_avg([1, 2, 3, 4, 5])


def test_kvp_thresholds_boundaries():
    assert kvp_coef(0.95) == 1.10       # ровно 95% — «95% и выше»
    assert kvp_coef(0.9499) == 1.00
    assert kvp_coef(0.85) == 1.00       # ровно 85% — «85–94,9%»
    assert kvp_coef(0.8499) == 0.90
    assert kvp_coef(0.70) == 0.90       # ровно 70% — «70–84,9%»
    assert kvp_coef(0.6999) == 0.80
    assert kvp_coef(None) == 1.0        # нет истории заказа — без корректировки


def test_trend_division_by_zero():
    assert trend([5, 0]) == 1.0
    assert trend([0, 5]) == 0.0
    f = forecast(sales=[5, 0, 0, 0], orders=None)
    assert f.qty >= 0


def test_zero_sales():
    f = forecast(sales=[0, 0, 0, 0], orders=[5, 5, 5, 5])
    assert f.kvp == 0.0
    assert f.kvp_coef == 0.80
    assert f.qty == 0


def test_zero_orders_kvp_none():
    f = forecast(sales=[10, 10, 10, 10], orders=[0, 0, 0, 0])
    assert f.kvp is None
    assert f.kvp_coef == 1.0


def test_trend_cap():
    f = forecast(sales=[30, 3, 10, 10], trend_cap=1.5)
    assert f.trend == 1.5
    f2 = forecast(sales=[1, 30, 10, 10], trend_cap=1.5)
    assert f2.trend == pytest.approx(1 / 1.5)


def test_negative_forecast_clipped():
    f = forecast(sales=[0, 1, 1, 1], orders=[10, 10, 10, 10])
    assert f.qty >= 0
