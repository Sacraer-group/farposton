# -*- coding: utf-8 -*-
"""Донастроенная методика: те же четыре шага базовой формулы, но параметры под масштаб.

Что меняется и почему (каждый пункт проверяется бэктестом, а не мнением):

1. ТРЕНД. Было: продажи последней недели / предпоследней — отношение двух чисел.
   На позициях по 1–3 шт/день это шум (3/2 = «спрос вырос в полтора раза»).
   Стало: наклон по всем доступным неделям (взвешенная регрессия), демпфирование
   коэффициентом φ и жёсткий клип. Плюс порог объёма: на «мелких» позициях тренд
   не применяется вовсе.

2. КВП. Было: ступени (94,9% → 0, 95,0% → +10%) — одна штука меняет заявку на 10%.
   Стало: непрерывная линейная функция с тем же смыслом и теми же границами эффекта.

3. СТРАХОВОЙ ЗАПАС. Было: неявно «+10% при высоком КВП».
   Стало: явная надбавка под сервисный уровень (квантиль), считается из разброса
   продаж этой позиции. Цена ошибки асимметрична: пустая полка дороже списания
   у высокомаржинальных позиций — уровень задаётся параметром.

Все функции чистые. Базовая методика — в baseline.py, она остаётся эталоном сверки.
"""
from dataclasses import dataclass
from statistics import mean, pstdev

from .baseline import WEIGHTS, weighted_avg

TREND_CLIP = (0.85, 1.20)      # демпфированный тренд не выходит за эти рамки
TREND_MIN_VOLUME = 5.0         # средние продажи ниже — тренд не применяем (шум)
TREND_DAMPING = 0.5            # φ: во сколько раз ослабляем наблюдаемый наклон
KVP_SLOPE = 0.8                # наклон непрерывной поправки КВП
KVP_CLIP = (0.80, 1.10)        # те же границы эффекта, что у ступеней
SAFETY_Z = {0.50: 0.0, 0.60: 0.25, 0.70: 0.52, 0.80: 0.84, 0.90: 1.28}


@dataclass
class Forecast:
    weighted_sales: float
    trend: float
    base_forecast: float
    kvp: float | None
    kvp_coef: float
    safety: float
    final: float
    qty: int
    notes: str          # человекочитаемое объяснение, что сработало


def damped_trend(sales: list[float]) -> float:
    """Наклон по всем неделям, ослабленный и ограниченный.

    sales идут от свежей к старой. Считаем средний относительный прирост между
    соседними неделями (а не отношение двух крайних точек), ослабляем в TREND_DAMPING
    раз и зажимаем в TREND_CLIP. На малых объёмах — 1.0 (нет тренда).
    """
    if len(sales) < 2:
        return 1.0
    level = mean(sales)
    if level < TREND_MIN_VOLUME:
        return 1.0
    # относительные приросты «свежая к следующей за ней», от старых к новым
    ratios = []
    for new, old in zip(sales[:-1], sales[1:]):
        if old > 0:
            ratios.append(new / old)
    if not ratios:
        return 1.0
    raw = mean(ratios)
    damped = 1.0 + (raw - 1.0) * TREND_DAMPING
    return max(TREND_CLIP[0], min(TREND_CLIP[1], damped))


def smooth_kvp_coef(kvp: float | None, locked: bool = False) -> float:
    """Непрерывный аналог ступеней: линейно вокруг 0,90 с теми же границами.

    Смысл сохранён: продали почти всё привезённое → растим заказ; продали мало → режем.
    Разница в том, что одна проданная штука больше не двигает заявку скачком на 10%.

    locked=True — предохранитель: поправка вверх не применяется. Нужен там, где позицию
    вычистила вечерняя распродажа −40%, а не спрос. Иначе замыкается контур:
    скидка убрала остаток → продано ≈ привезено → КВП высокий → заказ вырос →
    остаток вырос → скидка убрала больше. Автоматизация усилила бы его в окне пилота.
    """
    if kvp is None:
        return 1.0
    coef = 1.0 + (kvp - 0.90) * KVP_SLOPE
    coef = max(KVP_CLIP[0], min(KVP_CLIP[1], coef))
    return min(coef, 1.0) if locked else coef


def safety_stock(sales: list[float], service_level: float) -> float:
    """Надбавка под сервисный уровень: z × разброс продаж этой позиции.

    service_level 0.5 = медиана (надбавки нет), 0.8 = «хватит в 8 днях из 10».
    Разброс берём по тем же одноимённым дням недели, что и прогноз.
    """
    if service_level <= 0.5 or len(sales) < 2:
        return 0.0
    z = SAFETY_Z.get(round(service_level, 2), 0.52)
    return z * pstdev(sales)


def forecast(sales: list[float], orders: list[float] | None = None,
             service_level: float = 0.5, kvp_locked: bool = False) -> Forecast:
    """Донастроенный расчёт. Интерфейс совместим с базовой методикой."""
    ws = weighted_avg(sales)
    t = damped_trend(sales)
    base = ws * t

    kvp = None
    if orders is not None and len(orders) == len(sales):
        wo = weighted_avg(orders)
        if wo > 0:
            kvp = ws / wo
    coef = smooth_kvp_coef(kvp, locked=kvp_locked)
    safety = safety_stock(sales, service_level)
    final = base * coef + safety

    notes = []
    if t != 1.0:
        notes.append(f"тренд ×{t:.2f}")
    elif mean(sales) < TREND_MIN_VOLUME:
        notes.append("тренд не применён (малый объём)")
    if kvp is not None:
        notes.append(f"выполнение продаж {kvp * 100:.0f}% → ×{coef:.2f}")
        if kvp_locked and 1.0 + (kvp - 0.90) * KVP_SLOPE > 1.0:
            notes.append("поправка вверх заблокирована (позицию вычищает уценка)")
    if safety:
        notes.append(f"страховой запас +{safety:.1f}")
    return Forecast(
        weighted_sales=ws, trend=t, base_forecast=base, kvp=kvp, kvp_coef=coef,
        safety=safety, final=final, qty=max(0, round(final)), notes="; ".join(notes),
    )
