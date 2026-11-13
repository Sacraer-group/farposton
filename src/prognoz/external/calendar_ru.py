# -*- coding: utf-8 -*-
"""Календарь РФ: выходные/праздники/предпраздничные через isdayoff.ru, кеш в parquet.

isdayoff.ru: GET /api/getdata?year=YYYY&pre=1 → строка из кодов на каждый день года:
0 — рабочий, 1 — нерабочий (выходной/праздник), 2 — сокращённый (предпраздничный),
4 — covid-нерабочий (не используется). Бесплатно, без ключа.

Запуск (обновить кеш): PYTHONUTF8=1 py prognoz/src/prognoz/external/calendar_ru.py
"""
import sys
from datetime import date, timedelta
from functools import lru_cache
from pathlib import Path

import pandas as pd
import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "analysis" / "etl"))
from _db import EXTERNAL

CACHE = EXTERNAL / "calendar_ru.parquet"


def years_range() -> tuple[int, ...]:
    """2024..текущий+1. Раньше была константа (2024, 2025, 2026) — с наступлением
    2027 года календарь молча перестал бы покрывать заявки на будущие даты.
    2024 — начало истории точек (Точка 2 и точка 1 с 01.12.2024), правый край —
    следующий год: заявка на D+2 и канун Нового года требуют дат впереди."""
    return tuple(range(2024, date.today().year + 2))


YEARS = years_range()


def fetch_year(year: int) -> pd.DataFrame:
    r = requests.get("https://isdayoff.ru/api/getdata",
                     params={"year": year, "pre": 1}, timeout=60)
    r.raise_for_status()
    codes = r.text.strip()
    d0 = date(year, 1, 1)
    rows = [{"date": d0 + timedelta(days=i), "code": int(c)} for i, c in enumerate(codes)]
    return pd.DataFrame(rows)


@lru_cache(maxsize=4)
def load(refresh: bool = False) -> pd.DataFrame:
    """Календарь одним DataFrame. lru_cache: day_factor() зовёт load() на каждую
    дату — без кэша parquet перечитывался с диска сотни раз за прогон.
    ВАЖНО: результат общий на процесс — потребители не мутируют его
    (itertuples/copy), новые потребители должны делать .copy() перед правками."""
    if CACHE.exists() and not refresh:
        return pd.read_parquet(CACHE)
    df = pd.concat([fetch_year(y) for y in YEARS], ignore_index=True)
    df["is_dayoff"] = df["code"] == 1
    df["is_preholiday"] = df["code"] == 2
    dt = pd.to_datetime(df["date"])
    df["weekday"] = dt.dt.weekday                       # 0=пн
    # «нерабочий завтра при рабочем сегодня» — канун выходных/праздников
    df["is_day_before_off"] = df["is_dayoff"].shift(-1).fillna(False) & ~df["is_dayoff"]
    return df


def main():
    df = load(refresh=True)
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(CACHE, index=False)
    print(f"Календарь РФ: {len(df)} дней, {df['date'].min()} .. {df['date'].max()}, "
          f"нерабочих {int(df['is_dayoff'].sum())}, предпраздничных {int(df['is_preholiday'].sum())}")
    print(f"→ {CACHE}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
