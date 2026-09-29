# -*- coding: utf-8 -*-
"""Реестр SKU маркетов «сети» — «отдельный файл по всем позициям, которые продаются».

Скоуп: все SKU с продажами в точках-маркетах (role market/market_hub) за последние
12 месяцев от конца данных. Выход:
  - Реестр_SKU_маркетов.xlsx  — человеку (ответственному сотруднику/клиенту)
  - sku_registry.parquet/.csv — машине (для движка и анализа)

Запуск: PYTHONUTF8=1 py prognoz/src/prognoz/report/sku_registry.py
"""
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "analysis" / "etl"))
from _db import OUTPUT, connect_reader

MARKETS = ("market", "market_hub")


def build() -> pd.DataFrame:
    con = connect_reader()
    end_date = con.execute("SELECT max(sale_date) FROM mart.fact_sales_daily").fetchone()[0]

    df = con.execute("""
        WITH pts AS (  -- маркеты; merged-точки схлопываются в целевую
            SELECT point_key,
                   coalesce(nullif(merged_into, ''), point_key) AS canon_key
            FROM mart.dim_point
            WHERE role IN ('market', 'market_hub')
               OR (role = 'merged' AND merged_into IN
                   (SELECT point_key FROM mart.dim_point WHERE role IN ('market', 'market_hub')))
        ),
        canon AS (SELECT point_key, canon_name FROM mart.dim_point),
        sales AS (
            SELECT c.canon_name AS market, f.sku_key, f.sale_date,
                   f.qty_sold, f.qty_returned, f.revenue
            FROM mart.fact_sales_daily f
            JOIN pts ON pts.point_key = f.point_key
            JOIN canon c ON c.point_key = pts.canon_key
            WHERE f.sale_date > $end::DATE - INTERVAL 12 MONTH
        ),
        by_sku AS (
            SELECT sku_key,
                   min(sale_date)                            AS first_sale,
                   max(sale_date)                            AS last_sale,
                   sum(qty_sold - qty_returned)              AS qty_12m,
                   sum(CASE WHEN sale_date > $end::DATE - 28 THEN qty_sold - qty_returned ELSE 0 END) AS qty_28d,
                   sum(CASE WHEN sale_date > $end::DATE - 28 THEN revenue ELSE 0 END)                 AS revenue_28d,
                   count(DISTINCT CASE WHEN sale_date > $end::DATE - 56
                         THEN date_trunc('week', sale_date) END)                                      AS weeks_active_of_8
            FROM sales GROUP BY 1
        ),
        by_market AS (
            SELECT sku_key, market,
                   sum(CASE WHEN sale_date > $end::DATE - 28 THEN qty_sold - qty_returned ELSE 0 END) AS qty_28d
            FROM sales GROUP BY 1, 2
        ),
        in_orders AS (
            SELECT DISTINCT sku_key FROM mart.fact_orders_daily WHERE channel = 'transfer'
        )
        SELECT
            b.sku_key,
            s.code            AS "Код",
            s.name            AS "Наименование",
            s.group_path      AS "Группа",
            s.category        AS "Категория",
            s.is_weight       AS "Весовой",
            b.first_sale      AS "Первая продажа",
            b.last_sale       AS "Последняя продажа",
            round(b.qty_12m, 1)     AS "Продано за 12 мес, шт",
            round(b.qty_28d, 1)     AS "Продано за 28 дн, шт",
            round(b.revenue_28d, 0) AS "Выручка за 28 дн, руб",
            b.weeks_active_of_8     AS "Активных недель из 8",
            (b.sku_key IN (SELECT sku_key FROM in_orders)) AS "Есть в заявках",
            m.market, m.qty_28d AS market_qty
        FROM by_sku b
        JOIN mart.dim_sku s ON s.sku_key = b.sku_key
        LEFT JOIN by_market m ON m.sku_key = b.sku_key
    """, {"end": end_date}).fetchdf()

    base_cols = [c for c in df.columns if c not in ("market", "market_qty")]
    base = df[base_cols].drop_duplicates(subset="sku_key").set_index("sku_key")
    per_market = df.pivot_table(index="sku_key", columns="market", values="market_qty",
                                aggfunc="sum")
    per_market.columns = [f"28 дн: {c}" for c in per_market.columns]
    wide = base.join(per_market).reset_index()

    end = pd.Timestamp(end_date)
    first = pd.to_datetime(wide["Первая продажа"])
    last = pd.to_datetime(wide["Последняя продажа"])

    def status(row_first, row_last, weeks):
        if row_first > end - pd.Timedelta(days=14):
            return "новинка"
        if row_last < end - pd.Timedelta(days=14):
            return "выбывшая"
        if weeks <= 2:
            return "эпизодическая"
        return "активна"

    wide["Статус"] = [status(f, l, w) for f, l, w in
                      zip(first, last, wide["Активных недель из 8"])]
    total_rev = wide["Выручка за 28 дн, руб"].sum()
    wide["Доля выручки 28 дн, %"] = (100 * wide["Выручка за 28 дн, руб"] / total_rev).round(2)
    wide = wide.sort_values("Выручка за 28 дн, руб", ascending=False).reset_index(drop=True)
    con.close()
    return wide, end_date


def main():
    OUTPUT.mkdir(parents=True, exist_ok=True)
    df, end_date = build()

    df.drop(columns=["sku_key"]).to_excel(OUTPUT / "Реестр_SKU_маркетов.xlsx",
                                          index=False, freeze_panes=(1, 2))
    df.to_parquet(OUTPUT / "sku_registry.parquet", index=False)
    df.to_csv(OUTPUT / "sku_registry.csv", index=False, encoding="utf-8-sig")

    active = df[df["Статус"] == "активна"]
    print(f"Данные по: {end_date}. Всего SKU с продажами в маркетах за 12 мес: {len(df)}")
    print(f"Статусы: {df['Статус'].value_counts().to_dict()}")
    print(f"Активных SKU: {len(active)}, их выручка 28 дн: "
          f"{active['Выручка за 28 дн, руб'].sum():,.0f} руб")
    print("\nТоп-15 по выручке за 28 дней:")
    for _, r in df.head(15).iterrows():
        print(f"  {r['Наименование'][:45]:<47} {r['Продано за 28 дн, шт']:>7,.0f} шт "
              f"{r['Выручка за 28 дн, руб']:>10,.0f} руб  [{r['Статус']}]")
    print(f"\n→ {OUTPUT / 'Реестр_SKU_маркетов.xlsx'} (+ parquet, csv)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
