# -*- coding: utf-8 -*-
"""Базовая формула прогноза спроса по одноимённым дням недели.

Прогноз продаж SKU на день D по одноимённым дням недели за последние 2–4 недели:
  1) средневзвешенные продажи (веса 40/30/20/10, 50/30/20 или 60/40 — от свежей к старой);
  2) тренд = продажи последней недели / предпоследней;
  3) прогноз = средневзвешенное × тренд;
  4) КВП = средневзвеш. продажи / средневзвеш. заказ (те же веса);
     ≥95% → ×1,10; 85–94,9% → ×1,00; 70–84,9% → ×0,90; <70% → ×0,80;
  5) округление до целого.

Контрольный пример («цезарь»): продано 17/15/15/13, заказано 17/17/15/15 → 19 шт.

Все функции чистые, без IO. history-списки идут ОТ СВЕЖЕЙ недели К СТАРОЙ:
sales[0] — прошлая неделя, sales[1] — 2 недели назад, ...
"""
from dataclasses import dataclass

WEIGHTS = {
    4: (0.4, 0.3, 0.2, 0.1),
    3: (0.5, 0.3, 0.2),
    2: (0.6, 0.4),
}


@dataclass
class Forecast:
    """Прогноз с промежуточными значениями — для объяснимости («почему столько»)."""
    weighted_sales: float
    trend: float
    base_forecast: float          # средневзвешенное × тренд
    weighted_orders: float | None
    kvp: float | None             # доля, напр. 0.9512; None если заказа нет
    kvp_coef: float
    final: float                  # до округления
    qty: int                      # финальная заявка


def weighted_avg(values: list[float]) -> float:
    """Средневзвешенное по весам методики; len(values) ∈ {2, 3, 4}."""
    n = len(values)
    if n not in WEIGHTS:
        raise ValueError(f"нужно 2–4 недели истории, получено {n}")
    w = WEIGHTS[n]
    return sum(v * wi for v, wi in zip(values, w))


def trend(sales: list[float]) -> float:
    """Тренд = последняя неделя / предпоследняя. Деление на ноль → 1.0 (нет тренда)."""
    if len(sales) < 2 or sales[1] == 0:
        return 1.0
    return sales[0] / sales[1]


def kvp_coef(kvp: float | None) -> float:
    """Корректировка по коэффициенту выполнения продаж (КВП, доля)."""
    if kvp is None:
        return 1.0
    if kvp >= 0.95:
        return 1.10
    if kvp >= 0.85:
        return 1.00
    if kvp >= 0.70:
        return 0.90
    return 0.80


def forecast(sales: list[float], orders: list[float] | None = None,
             trend_cap: float | None = None) -> Forecast:
    """Полный расчёт методики.

    sales  — продажи одноимённых дней, от свежей недели к старой (2–4 значения);
    orders — заказы тех же дней (та же длина) или None, если истории заказа нет
             (тогда КВП-шаг пропускается, коэффициент 1.0);
    trend_cap — опциональное ограничение тренда сверху/снизу (например 1.5),
             в базовой методике отсутствует (None).
    """
    ws = weighted_avg(sales)
    t = trend(sales)
    if trend_cap is not None:
        t = max(1.0 / trend_cap, min(trend_cap, t))
    base = ws * t

    wo = kvp = None
    if orders is not None:
        if len(orders) != len(sales):
            raise ValueError("orders и sales должны быть одной длины")
        wo = weighted_avg(orders)
        kvp = (ws / wo) if wo > 0 else None

    coef = kvp_coef(kvp)
    final = base * coef
    return Forecast(
        weighted_sales=ws, trend=t, base_forecast=base,
        weighted_orders=wo, kvp=kvp, kvp_coef=coef,
        final=final, qty=max(0, round(final)),
    )
