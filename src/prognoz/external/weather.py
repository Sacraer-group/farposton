# -*- coding: utf-8 -*-
"""Погода локального времени UTC+10: Open-Meteo Archive API (ERA5), дневные значения, кеш в parquet.

Бесплатно, без ключа, лимит 10k запросов/день — нам нужен 1 запрос на всю историю.
Для прод-режима (прогноз на завтра) — тот же формат через Forecast API (forecast_days).

Запуск (обновить кеш): PYTHONUTF8=1 py prognoz/src/prognoz/external/weather.py
"""
import sys
import os
from datetime import date
from pathlib import Path

import pandas as pd
import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "analysis" / "etl"))
from _db import EXTERNAL

LAT = float(os.environ.get("APP_WEATHER_LAT", "0"))
LON = float(os.environ.get("APP_WEATHER_LON", "0"))
CACHE = EXTERNAL / "weather.parquet"
DAILY_VARS = [
    "temperature_2m_max", "temperature_2m_min", "apparent_temperature_max",
    "precipitation_sum", "rain_sum", "snowfall_sum",
    "windspeed_10m_max", "weathercode",
]


def fetch_history(start: str = "2024-11-01", end: str | None = None) -> pd.DataFrame:
    if not (-90 <= LAT <= 90 and -180 <= LON <= 180) or (LAT == 0 and LON == 0):
        raise ValueError("Set APP_WEATHER_LAT and APP_WEATHER_LON for weather data")
    end = end or date.today().isoformat()
    r = requests.get("https://archive-api.open-meteo.com/v1/archive", params={
        "latitude": LAT, "longitude": LON,
        "start_date": start, "end_date": end,
        "daily": ",".join(DAILY_VARS),
        "timezone": "Etc/GMT-10",
    }, timeout=120)
    r.raise_for_status()
    daily = r.json()["daily"]
    df = pd.DataFrame(daily).rename(columns={"time": "date"})
    df["date"] = pd.to_datetime(df["date"]).dt.date
    return df


def load(refresh: bool = False) -> pd.DataFrame:
    if CACHE.exists() and not refresh:
        return pd.read_parquet(CACHE)
    df = fetch_history()
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(CACHE, index=False)
    return df


def main():
    df = load(refresh=True)
    ok = df.dropna(subset=["temperature_2m_max"])
    print(f"Погода UTC+10: {len(df)} дней, {df['date'].min()} .. {df['date'].max()} "
          f"(с фактом: по {ok['date'].max()})")
    print(df.tail(3).to_string(index=False))
    print(f"→ {CACHE}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
