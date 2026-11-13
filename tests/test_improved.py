# -*- coding: utf-8 -*-
"""Тесты донастроенной методики — той, что идёт в прод.

Базовый движок (baseline.py) остаётся эталоном сверки с автором, но в бою считает
improved.py, и до 01.08.2026 он не был покрыт ни одним тестом. Здесь проверяется
не «работает ли функция», а те свойства, ради которых донастройка вообще делалась:
тренд не разгоняется на шуме, поправка КВП непрерывна, предохранитель от уценки
действительно блокирует рост заказа.

Запуск: PYTHONUTF8=1 py -m pytest prognoz/tests/ -q
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from prognoz.engine.improved import (      # noqa: E402
    KVP_CLIP, TREND_CLIP, TREND_MIN_VOLUME, damped_trend, forecast,
    safety_stock, smooth_kvp_coef,
)


# --- тренд: главная претензия к методике «как есть» -------------------------------

def test_trend_not_applied_on_small_volume():
    """На позициях 1–3 шт/день отношение недель — это шум, а не тренд.
    Именно он давал −20 пунктов точности в бэктесте."""
    assert damped_trend([3, 2, 3, 2]) == 1.0
    assert damped_trend([1, 0, 1, 0]) == 1.0


def test_trend_damped_and_clipped():
    """Наблюдаемый рост ослабляется вдвое и зажимается рамкой."""
    # ровный рост 20% в неделю на достаточном объёме
    t = damped_trend([20, 16, 13, 10])
    assert 1.0 < t <= TREND_CLIP[1]
    # взрывной рост не пролезает за верхнюю границу
    assert damped_trend([100, 10, 10, 10]) == TREND_CLIP[1]
    # обвал не пролезает за нижнюю
    assert damped_trend([5, 50, 50, 50]) == TREND_CLIP[0]


def test_trend_needs_two_points():
    assert damped_trend([10]) == 1.0
    assert damped_trend([]) == 1.0


def test_trend_survives_zero_in_history():
    """Ноль в знаменателе не должен ронять расчёт — пара недель без продаж бывает."""
    t = damped_trend([10, 0, 10, 8])
    assert TREND_CLIP[0] <= t <= TREND_CLIP[1]


# --- КВП: ступени заменены на непрерывную линию ------------------------------------

def test_kvp_is_continuous_at_old_step_boundaries():
    """У ступеней одна проданная штука двигала заявку скачком на 10%.
    Проверяем, что вокруг старых границ больше нет разрывов."""
    for edge in (0.95, 0.85, 0.70):
        left, right = smooth_kvp_coef(edge - 1e-4), smooth_kvp_coef(edge + 1e-4)
        assert abs(left - right) < 1e-3


def test_kvp_direction_and_bounds():
    assert smooth_kvp_coef(1.00) > 1.0        # продали всё → растим
    assert smooth_kvp_coef(0.60) < 1.0        # продали мало → режем
    assert smooth_kvp_coef(None) == 1.0
    assert smooth_kvp_coef(5.0) == KVP_CLIP[1]
    assert smooth_kvp_coef(0.0) == KVP_CLIP[0]


def test_kvp_lock_blocks_only_upward_correction():
    """Предохранитель от контура «скидка вычистила остаток → растим заказ».
    Вниз поправка обязана продолжать работать, иначе система только разгоняется."""
    assert smooth_kvp_coef(1.00, locked=True) == 1.0
    assert smooth_kvp_coef(0.95, locked=True) == 1.0
    down = smooth_kvp_coef(0.60)
    assert smooth_kvp_coef(0.60, locked=True) == down


# --- страховой запас ---------------------------------------------------------------

def test_safety_stock_zero_at_median():
    assert safety_stock([10, 12, 8, 10], 0.5) == 0.0
    assert safety_stock([10], 0.8) == 0.0


def test_safety_stock_grows_with_service_level_and_spread():
    calm, wild = [10, 10, 10, 10], [2, 18, 4, 16]
    assert safety_stock(calm, 0.8) == 0.0                    # разброса нет — надбавки нет
    assert safety_stock(wild, 0.8) > safety_stock(wild, 0.7) > 0


# --- расчёт целиком ----------------------------------------------------------------

def test_forecast_returns_level_at_neutral_kvp():
    """Нейтральная точка методики — КВП 90%: продали девять из десяти привезённых.
    Там поправки нет, и на ровной истории расчёт обязан вернуть сам уровень."""
    f = forecast([9, 9, 9, 9], [10, 10, 10, 10])
    assert f.trend == 1.0
    assert f.kvp == pytest.approx(0.9)
    assert f.kvp_coef == pytest.approx(1.0)
    assert f.qty == 9


def test_forecast_raises_order_when_everything_sold_out():
    """Ключевая идея автора методики: продали всё привезённое → спрос был выше привоза →
    растим заявку, чтобы поймать упущенные продажи. Ровная история этого не отменяет.

    ⚠️ Именно здесь живёт риск контура: вечерняя распродажа −40% вычищает остаток,
    и «продано = привезено» получается не от спроса, а от скидки. Лечится kvp_locked."""
    f = forecast([10, 10, 10, 10], [10, 10, 10, 10])
    assert f.kvp == pytest.approx(1.0)
    assert f.kvp_coef > 1.0
    assert f.qty == 11


def test_forecast_never_negative():
    assert forecast([0, 0, 0, 0], [5, 5, 5, 5]).qty == 0


def test_forecast_lock_lowers_or_keeps_quantity():
    """Заблокированная поправка не может увеличить заявку — только оставить или уменьшить."""
    sales, orders = [18, 17, 16, 15], [18, 17, 16, 15]     # КВП = 100%, тянет вверх
    assert forecast(sales, orders, kvp_locked=True).qty <= forecast(sales, orders).qty


def test_forecast_notes_explain_what_worked():
    """Пояснение — не украшение: оно уходит в файл заявки, по нему сотрудник понимает,
    почему цифра такая. Пустым оно быть не должно там, где сработала поправка."""
    f = forecast([18, 17, 16, 15], [18, 17, 16, 15], kvp_locked=True)
    assert "выполнение продаж" in f.notes
    assert "заблокирована" in f.notes


def test_forecast_accepts_three_and_two_weeks():
    """Методика допускает 3 и 2 недели истории — веса пересчитываются, смысл сохраняется."""
    for sales, orders in (([9, 9, 9], [10, 10, 10]), ([9, 9], [10, 10])):
        f = forecast(sales, orders)
        assert f.kvp == pytest.approx(0.9)
        assert f.qty == 9
