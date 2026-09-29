# -*- coding: utf-8 -*-
"""Walk-forward бэктест прогноза заказов маркетов «сети».

Честность:
- прогноз на день D строится ТОЛЬКО из данных с датой < D (одноимённые дни недели
  D−7/14/21/28 — все в прошлом);
- множество строк дня D = (точка × SKU), на которые в этот день БЫЛА заявка человека
  (qty_ordered > 0) → модели сравниваются с человеком на одном множестве;
- «human» — фактическая заявка (бенчмарк «как сейчас»);
- факт = чистые продажи дня D (продано − возвраты); спрос обрезан привозом,
  поэтому метрики режутся по сегменту sellout (продали ≥ привезённого).

Склейка каналов «заказано»: для каждой точки с первой transfer-заявки канал = transfer
(zakaz в этот период игнорируется — двойной учёт переходного марта), до неё — zakaz.

Модели: human, naive (прошлая одноимённая неделя), ma4 (среднее 4 недель),
krap (базовая методика), krap_adj (методика × календарно-погодные коэффициенты,
оценённые ТОЛЬКО на трейне до начала тестового окна).

Запуск: PYTHONUTF8=1 py prognoz/src/prognoz/backtest/harness.py
Выход:  analysis/output/backtest_rows.parquet + печать сводки.
"""
import sys
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

SRC = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(SRC))
sys.path.insert(0, str(SRC.parents[0] / "analysis" / "etl"))
from _db import OUTPUT, connect_reader                      # noqa: E402
from prognoz.engine.baseline import forecast             # noqa: E402
from prognoz.engine.improved import forecast as forecast_v2  # noqa: E402
from prognoz.external import calendar_ru, weather           # noqa: E402
from prognoz.backtest.metrics import summarize              # noqa: E402

TEST_START = date(2026, 4, 1)
TEST_END = date(2026, 7, 20)
WEEK_LAGS = (7, 14, 21, 28)
RAIN_MM = 5.0          # порог «дождливый день»


def load_panels():
    con = connect_reader()
    sales = con.execute("""
        SELECT coalesce(nullif(p.merged_into, ''), f.point_key) AS point_key,
               f.sku_key, f.sale_date AS d,
               sum(f.qty_sold - f.qty_returned) AS qty,
               sum(f.revenue) AS revenue
        FROM mart.fact_sales_daily f
        JOIN mart.dim_point p ON p.point_key = f.point_key
        WHERE p.role IN ('market', 'market_hub')
           OR (p.role = 'merged' AND p.merged_into IN
               (SELECT point_key FROM mart.dim_point WHERE role IN ('market', 'market_hub')))
        GROUP BY 1, 2, 3
    """).fetchdf()

    orders = con.execute("""
        WITH markets AS (
            SELECT point_key, coalesce(nullif(merged_into, ''), point_key) AS canon
            FROM mart.dim_point
            WHERE role IN ('market', 'market_hub', 'merged')
        ),
        o AS (
            SELECT m.canon AS point_key, f.sku_key, f.trade_date AS d,
                   f.channel, sum(f.qty_ordered) AS qty
            FROM mart.fact_orders_daily f
            JOIN markets m ON m.point_key = f.point_key
            GROUP BY 1, 2, 3, 4
        ),
        t_start AS (
            SELECT point_key, min(d) AS t0 FROM o WHERE channel = 'transfer' GROUP BY 1
        )
        SELECT o.point_key, o.sku_key, o.d, sum(o.qty) AS qty
        FROM o LEFT JOIN t_start USING (point_key)
        WHERE (t_start.t0 IS NULL AND o.channel = 'zakaz')
           OR (o.d <  t_start.t0 AND o.channel = 'zakaz')
           OR (o.d >= t_start.t0 AND o.channel = 'transfer')
        GROUP BY 1, 2, 3
    """).fetchdf()

    names = con.execute("""
        SELECT point_key, canon_name FROM mart.dim_point WHERE canon_name IS NOT NULL
    """).fetchdf()
    sku_meta = con.execute("""
        SELECT sku_key, name, group_path, category FROM mart.dim_sku
    """).fetchdf()
    con.close()
    sales["d"] = pd.to_datetime(sales["d"]).dt.date
    orders["d"] = pd.to_datetime(orders["d"]).dt.date

    # средняя цена SKU (для рублёвых метрик)
    price = (sales.groupby("sku_key").agg(rev=("revenue", "sum"), q=("qty", "sum"))
             .assign(price=lambda t: (t.rev / t.q.replace(0, pd.NA)).fillna(0))["price"])
    return sales, orders, names, sku_meta, price


def build_lookups(sales, orders):
    s = {(r.point_key, r.sku_key, r.d): r.qty for r in sales.itertuples()}
    o = {(r.point_key, r.sku_key, r.d): r.qty for r in orders.itertuples()}
    return s, o


def calendar_features():
    cal = calendar_ru.load()
    cal_by_date = {r.date: r for r in cal.itertuples()}
    w = weather.load()
    wet = {r.date: (r.precipitation_sum or 0) >= RAIN_MM for r in w.itertuples()}
    return cal_by_date, wet


def train_multipliers(sales, cal_by_date, wet):
    """Календарно-погодные множители на трейне (< TEST_START). Shrinkage к 1.0 не нужен:
    оцениваем по агрегату всех маркетов (тысячи наблюдений на тип дня)."""
    tr = sales[sales["d"] < TEST_START].copy()
    tr["weekday"] = pd.to_datetime(tr["d"]).dt.weekday
    daily = tr.groupby(["d", "weekday"])["qty"].sum().reset_index()
    daily["pre"] = daily["d"].map(lambda x: getattr(cal_by_date.get(x), "is_day_before_off", False))
    daily["wet"] = daily["d"].map(lambda x: wet.get(x, False))

    wk_mean = daily.groupby("weekday")["qty"].mean()
    daily["rel"] = daily.apply(lambda r: r.qty / wk_mean[r.weekday] if wk_mean[r.weekday] else 1, axis=1)
    pre_mult = daily.loc[daily["pre"], "rel"].mean()
    wet_mult = daily.loc[daily["wet"], "rel"].mean() / daily.loc[~daily["wet"], "rel"].mean()
    return (round(float(pre_mult), 3) if pd.notna(pre_mult) else 1.0,
            round(float(wet_mult), 3) if pd.notna(wet_mult) else 1.0)


def run():
    sales, orders, names, sku_meta, price = load_panels()
    s_look, o_look = build_lookups(sales, orders)
    cal_by_date, wet = calendar_features()
    pre_mult, wet_mult = train_multipliers(sales, cal_by_date, wet)
    print(f"Множители с трейна (<{TEST_START}): предпраздничный ×{pre_mult}, дождь ×{wet_mult}")

    test_orders = orders[(orders["d"] >= TEST_START) & (orders["d"] <= TEST_END)
                         & (orders["qty"] > 0)]
    rows = []
    for r in test_orders.itertuples():
        pt, sku, D = r.point_key, r.sku_key, r.d
        sh = [s_look.get((pt, sku, D - timedelta(days=lag)), 0.0) for lag in WEEK_LAGS]
        oh = [o_look.get((pt, sku, D - timedelta(days=lag)), 0.0) for lag in WEEK_LAGS]

        # сколько недель истории реально доступно этой паре (точка, SKU)
        weeks = 4
        while weeks > 2 and sh[weeks - 1] == 0 and oh[weeks - 1] == 0:
            weeks -= 1
        sh_w, oh_w = sh[:weeks], oh[:weeks]

        actual = s_look.get((pt, sku, D), 0.0)
        naive = sh[0]
        ma4 = sum(sh) / len(sh)
        oh_arg = oh_w if any(oh_w) else None
        if any(sh_w) or any(oh_w):
            krap = forecast(sh_w, oh_arg).qty
            krap_ct = forecast(sh_w, oh_arg, trend_cap=1.5).qty       # тренд ограничен [1/1.5; 1.5]
            f_nt = forecast(sh_w, oh_arg)
            krap_nt = max(0, round(f_nt.weighted_sales * f_nt.kvp_coef))   # без тренда
            krap_nk = max(0, round(forecast(sh_w, None).base_forecast))    # без КВП
            imp = forecast_v2(sh_w, oh_arg).qty                       # донастроенная методика
            imp_sl70 = forecast_v2(sh_w, oh_arg, service_level=0.70).qty
            imp_sl80 = forecast_v2(sh_w, oh_arg, service_level=0.80).qty
        else:
            krap = krap_ct = krap_nt = krap_nk = naive  # истории нет вовсе — деградация до наивной
            imp = imp_sl70 = imp_sl80 = naive
        cal_row = cal_by_date.get(D)
        mult = 1.0
        if cal_row is not None and getattr(cal_row, "is_day_before_off", False):
            mult *= pre_mult
        if wet.get(D, False):
            mult *= wet_mult
        krap_adj = round(krap * mult)
        ma4_adj = ma4 * mult

        sellout = actual >= r.qty
        for model, fc in (("human", r.qty), ("naive", naive), ("ma4", ma4),
                          ("krap", krap), ("krap_adj", krap_adj),
                          ("krap_ct", krap_ct), ("krap_nt", krap_nt), ("krap_nk", krap_nk),
                          ("ma4_adj", ma4_adj),
                          ("imp", imp), ("imp_sl70", imp_sl70), ("imp_sl80", imp_sl80),
                          ("imp_adj", round(imp * mult))):
            rows.append((D, pt, sku, model, float(fc), float(actual),
                         float(price.get(sku, 0.0)), sellout, weeks))

    df = pd.DataFrame(rows, columns=["date", "point_key", "sku_key", "model",
                                     "forecast", "actual", "price", "sellout", "weeks"])
    df = df.merge(names, on="point_key", how="left")
    df = df.merge(sku_meta[["sku_key", "group_path"]], on="sku_key", how="left")
    OUTPUT.mkdir(parents=True, exist_ok=True)
    df.to_parquet(OUTPUT / "backtest_rows.parquet", index=False)

    n_days = df["date"].nunique()
    print(f"\nОкно {TEST_START}..{TEST_END}: {n_days} дней, "
          f"{len(df[df.model == 'human']):,} строк (точка×SKU×день) на модель\n")

    print("=== Итог по моделям (все строки) ===")
    print(summarize(df, ["model"]).sort_values("wape").to_string(index=False))
    print("\n=== Только дни БЕЗ sellout (спрос не обрезан привозом; для human тавтологичен) ===")
    print(summarize(df[~df.sellout], ["model"]).sort_values("wape").to_string(index=False))
    df["vol"] = pd.cut(df["actual"], bins=[-0.1, 4.9, 14.9, 1e9],
                       labels=["0-4 шт", "5-14 шт", "15+ шт"])
    print("\n=== По объёму дня (штук факта), WAPE ===")
    piv = (summarize(df, ["model", "vol"]).pivot(index="model", columns="vol", values="wape"))
    print(piv.to_string())
    print("\n=== По точкам (human vs лучшие) ===")
    print(summarize(df[df.model.isin(['human', 'krap', 'ma4_adj'])], ["canon_name", "model"])
          .sort_values(["canon_name", "model"]).to_string(index=False))
    print(f"\n→ строки: {OUTPUT / 'backtest_rows.parquet'}")
    return 0


if __name__ == "__main__":
    sys.exit(run())
