# -*- coding: utf-8 -*-
"""Русское форматирование чисел и разбор чисел из русского текста.

Одно место на весь проект: 10,2 (десятичная запятая), 1 918 (тысячи через
неразрывный пробел). Обратный разбор нужен verify_docs — он читает числа из
markdown, где стоит «36,0 млн ₽» или «+10,2%».
"""
from __future__ import annotations

import re

NBSP = " "          # неразрывный пробел
_NUM = re.compile(r"[−\-+]?\d[\d\s  ]*(?:[.,]\d+)?")


def fmt_ru(x: float | int, digits: int = 1) -> str:
    """15234.5 → «15 234,5» (тысячи NBSP, запятая-десятичная)."""
    if isinstance(x, int) or (isinstance(x, float) and x == int(x) and digits == 0):
        s = f"{int(x):,}"
        return s.replace(",", NBSP)
    s = f"{x:,.{digits}f}"
    return s.replace(",", "\x00").replace(".", ",").replace("\x00", NBSP)


def parse_ru(text: str) -> float:
    """Первое число из русского текста: «+10,2 %» → 10.2, «1 918 шт» → 1918.0.

    Интервалы («18–26%») отдают первое число — якорь надо ставить к конкретному
    краю интервала, а не к интервалу целиком.
    """
    m = _NUM.search(text)
    if not m:
        raise ValueError(f"числа не найдено в: {text[:60]!r}")
    s = (m.group(0).replace(NBSP, "").replace(" ", "").replace(" ", "")
         .replace("−", "-").replace(",", "."))
    return float(s)
