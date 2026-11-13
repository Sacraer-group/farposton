# -*- coding: utf-8 -*-
"""Тесты календарных поправок.

Главное, что здесь проверяется, — что поправка МОЛЧИТ в обычные дни. До 03.08.2026
множитель «канун выходного» ×1,045 срабатывал на каждой пятнице поверх и без того
пятничного уровня, и заявка по пятницам систематически завышалась. Тест на обычную
пятницу стоит первым именно поэтому.

Запуск: PYTHONUTF8=1 py -m pytest prognoz/tests/ -q
"""
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from prognoz.external.calendar_effects import (   # noqa: E402
    ANOMALY_THRESHOLD, day_factor, is_anomalous, relative_factor,
)

WEEK_LAGS = (7, 14, 21, 28)


def hist(target: date):
    return [target - timedelta(days=l) for l in WEEK_LAGS]


# --- поправка обязана молчать в обычные дни ---------------------------------------

def test_ordinary_friday_gets_no_correction():
    """Пятница предсказывается по пятницам — календарь тут не добавляет ничего.
    Это ровно тот двойной счёт, из-за которого поправку и переписали."""
    fri = date(2026, 8, 21)
    assert fri.weekday() == 4
    mult, _ = relative_factor(fri, hist(fri))
    assert mult == 1.0


def test_ordinary_days_all_silent():
    """В августе–сентябре 2026 из календаря особенно только 1 сентября. Двигаться
    должны ровно оно и следующие вторники, у которых 1 сентября попало в историю."""
    d = date(2026, 8, 4)
    moved = []
    while d <= date(2026, 9, 30):
        mult, _ = relative_factor(d, hist(d))
        if mult != 1.0:
            moved.append((d, mult))
        d += timedelta(days=1)
    assert [x[0] for x in moved] == [date(2026, 9, 1), date(2026, 9, 8),
                                     date(2026, 9, 15), date(2026, 9, 22),
                                     date(2026, 9, 29)]
    # само 1 сентября вверх, последующие вторники — слегка вниз, и очень слабо
    assert moved[0][1] > 1.0
    assert all(0.97 < m < 1.0 for _, m in moved[1:])


# --- поправка обязана сработать там, где спрос действительно другой ----------------

def test_new_year_collapses_order():
    """1 января спрос падает впятеро — это самый сильный и самый устойчивый эффект."""
    nye = date(2027, 1, 1)
    mult, why = relative_factor(nye, hist(nye))
    assert mult < 0.30
    assert "января" in why


def test_march_eight_raises_order():
    """8 марта — сильнейшая дата года в обе стороны наблюдений."""
    d = date(2027, 3, 8)
    mult, _ = relative_factor(d, hist(d))
    assert mult > 1.4


def test_disputed_dates_do_not_move_order():
    """Где два года спорят между собой, поправки быть не должно: разнобой — не знание."""
    for d in (date(2027, 2, 23), date(2027, 5, 1), date(2027, 6, 12)):
        mult, why = relative_factor(d, hist(d))
        assert mult == 1.0, f"{d} сдвинулась на {mult}"
        assert why, f"{d}: молчим, но причину назвать обязаны"


def test_holiday_in_history_corrects_ordinary_day():
    """Если праздник попал в ИСТОРИЮ, обычный день тоже требует поправки — вверх:
    считать по заниженным дням и не поправить значит занизить заявку."""
    d = date(2027, 1, 29)          # пятница, а 1 января в лагах — тоже пятница
    assert any(h == date(2027, 1, 1) for h in hist(d))
    mult, _ = relative_factor(d, hist(d))
    assert mult > 1.0


# --- флаг аномалии: по нему движок отпускает предохранители ------------------------

def test_anomaly_flag_matches_threshold():
    assert is_anomalous(date(2027, 1, 1))
    assert is_anomalous(date(2027, 3, 8))
    assert not is_anomalous(date(2026, 8, 21))
    f, _ = day_factor(date(2027, 1, 1))
    assert abs(f - 1) >= ANOMALY_THRESHOLD


def test_weekend_is_not_treated_as_holiday():
    """Календарь помечает нерабочими и субботу с воскресеньем. Если считать их
    праздниками, множитель 0,94 сядет на каждые выходные — а их вес уже в дне недели."""
    sat, sun = date(2026, 8, 22), date(2026, 8, 23)
    assert day_factor(sat)[0] == 1.0
    assert day_factor(sun)[0] == 1.0
