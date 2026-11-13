# -*- coding: utf-8 -*-
"""Инварианты честности бэктеста: прогноз на день D не видит данных дат ≥ D."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from prognoz.backtest import harness


def test_week_lags_are_all_in_the_past():
    """Методика смотрит только одноимённые дни прошлых недель — минимум 7 дней назад."""
    assert min(harness.WEEK_LAGS) >= 7


def test_multipliers_trained_before_test_window():
    """Календарно-погодные множители оцениваются строго до тестового окна (train_multipliers
    фильтрует d < TEST_START) — проверяем, что фильтр в коде остался."""
    import inspect
    src = inspect.getsource(harness.train_multipliers)
    assert "< TEST_START" in src
