# -*- coding: utf-8 -*-
"""Упущенные продажи по чекам: сколько недопродали из-за того, что позиция кончилась.

Идея: время последней продажи дня × типичная скорость продаж этой позиции по часам
= сколько штук она бы ещё продала до закрытия, если бы не кончилась.

Три предохранителя от завышения (все консервативные):
  1. Если в тот день по паре было СПИСАНИЕ — потери 0: товар оставался, молчание
     вечером значит «не брали», а не «кончился».
  2. Если в тот день была УЦЕНКА (u40 > 0) — потери 0: вечером товар стоял на полке
     со скидкой, дефицита не было.
  3. Часовой профиль пары считается по всем дням её жизни, включая дни дефицита
     (где вечер уже нулевой) — то есть скорость сама занижена, и оценка потерь
     скорее НИЖНЯЯ граница, чем верхняя.

Деньги: потерянная маржа = 0,67 × средняя цена пары (экономика ошибки заказа).

Запуск: PYTHONUTF8=1 PYTHONPATH=prognoz/src py -m prognoz.report.lost_sales
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

SRC = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(SRC))
sys.path.insert(0, str(SRC.parents[0] / "analysis" / "etl"))
from _db import OUTPUT, connect_reader                    # noqa: E402
from prognoz.assortment import filter_frame               # noqa: E402
from prognoz.facts.blocks.economics import unit_costs     # noqa: E402
from prognoz.scope import GROUP_LIKE, GROUP_NOT_LIKE, GROUP_NOT_LIKE_BAR  # noqa: E402

PERIOD = ("2025-08-01", "2026-07-31")
MIN_DAYS = 30          # пары с меньшей историей не считаем — профиль ненадёжен
EARLY_GAP_H = 1.5      # «рано замолчала» = раньше, чем за полтора часа до закрытия
# Молчание значимо, только если за оставшиеся часы ожидалось ≥ 2 шт: для редкой
# позиции (набор, 1–2 продажи в день) тишина после обеда — норма пуассона, а не
# дефицит, и гейты её не прикрывают (сеты не уценивают и не списывают — списывают
# компоненты). При пороге 2 доля ложных «дефицитов» ≤ e⁻² ≈ 13%.
SIGNIF_UNITS = 2.0

SCOPE = (f"s.group_path LIKE '{GROUP_LIKE}' AND s.group_path NOT LIKE "
         f"'{GROUP_NOT_LIKE}' AND s.group_path NOT LIKE '{GROUP_NOT_LIKE_BAR}'")


def main() -> int:
    con = connect_reader()
    co, cu = unit_costs()

    # час закрытия точки: 95-й перцентиль последнего чека дня за год
    close = con.execute(f"""
        WITH last_per_day AS (
            SELECT point_key, trade_day, max(ts) AS t_last
            FROM mart.cheki_headers
            WHERE posted AND op = 'Продажа' AND point_key IS NOT NULL
              AND trade_day BETWEEN DATE '{PERIOD[0]}' AND DATE '{PERIOD[1]}'
            GROUP BY 1, 2)
        SELECT point_key,
               quantile_cont(extract(hour FROM t_last) + extract(minute FROM t_last)/60.0,
                             0.95) AS close_h
        FROM last_per_day GROUP BY 1
    """).fetchdf()
    con.execute("CREATE TEMP TABLE close_h AS SELECT * FROM close")

    # база: платные продажи скоупа за год
    con.execute(f"""
        CREATE TEMP TABLE base AS
        SELECT cs.point_key, cs.sku_key, cs.trade_day, cs.ts,
               extract(hour FROM cs.ts) AS h, cs.qty, cs.price
        FROM mart.cheki_sales cs
        JOIN mart.dim_sku s ON s.sku_key = cs.sku_key
        WHERE {SCOPE} AND cs.amount > 0
          AND cs.trade_day BETWEEN DATE '{PERIOD[0]}' AND DATE '{PERIOD[1]}'
    """)

    # часовой профиль пары: средние шт/час по дням жизни пары
    con.execute("""
        CREATE TEMP TABLE prof AS
        WITH days AS (
            SELECT point_key, sku_key, count(DISTINCT trade_day) AS n_days,
                   avg(price) AS avg_price
            FROM base GROUP BY 1, 2 HAVING count(DISTINCT trade_day) >= 30),
        by_hour AS (
            SELECT point_key, sku_key, h, sum(qty) AS q
            FROM base GROUP BY 1, 2, 3)
        SELECT b.point_key, b.sku_key, b.h, b.q / d.n_days AS rate,
               d.n_days, d.avg_price
        FROM by_hour b JOIN days d USING (point_key, sku_key)
    """)

    # день-пара: последняя продажа + гейты (списание, уценка)
    lost = con.execute(f"""
        WITH day_pair AS (
            SELECT point_key, sku_key, trade_day,
                   max(extract(hour FROM ts) + extract(minute FROM ts)/60.0) AS t_last,
                   sum(qty) AS sold
            FROM base GROUP BY 1, 2, 3),
        wo AS (
            SELECT w.point_key, w.sku_key, w.wo_date AS trade_day, sum(w.qty_writeoff) q
            FROM mart.fact_writeoff_daily w GROUP BY 1, 2, 3),
        u4 AS (
            SELECT f.point_key, f.sku_key, f.sale_date AS trade_day, sum(f.u40) q
            FROM mart.fact_sales_daily f GROUP BY 1, 2, 3),
        flagged AS (
            SELECT dp.*, c.close_h,
                   coalesce(wo.q, 0) > 0 AS had_writeoff,
                   coalesce(u4.q, 0) > 0 AS had_markdown
            FROM day_pair dp
            JOIN close_h c USING (point_key)
            LEFT JOIN wo USING (point_key, sku_key, trade_day)
            LEFT JOIN u4 USING (point_key, sku_key, trade_day))
        SELECT f.point_key, f.sku_key, f.trade_day, f.t_last, f.sold,
               f.had_writeoff, f.had_markdown,
               CASE WHEN f.had_writeoff OR f.had_markdown
                         OR f.t_last >= f.close_h - {EARLY_GAP_H}
                    THEN 0.0
                    ELSE (SELECT coalesce(sum(p.rate), 0) FROM prof p
                          WHERE p.point_key = f.point_key AND p.sku_key = f.sku_key
                            AND p.h > f.t_last AND p.h <= f.close_h)
               END AS lost_units
        FROM flagged f
        JOIN prof pr ON pr.point_key = f.point_key AND pr.sku_key = f.sku_key
                     AND pr.h = 12   -- существование профиля (любой час-якорь)
    """).fetchdf()

    prices = con.execute(
        "SELECT DISTINCT point_key, sku_key, avg_price FROM prof").fetchdf()
    names = con.execute("SELECT sku_key, name FROM mart.dim_sku").fetchdf()
    pts = con.execute(
        "SELECT point_key, coalesce(nullif(merged_into, ''), point_key) "
        "AS policy_point_key, canon_name FROM mart.dim_point "
        "WHERE canon_name IS NOT NULL").fetchdf()

    m = lost.merge(prices, on=["point_key", "sku_key"]) \
            .merge(pts, on="point_key").merge(names, on="sku_key")
    m = filter_frame(
        m, "metric_scope", point_col="policy_point_key", sku_col="sku_key",
        date_col="trade_day")
    m.loc[m["lost_units"] < SIGNIF_UNITS, "lost_units"] = 0.0
    m["lost_rub_margin"] = m["lost_units"] * m["avg_price"] * cu

    out = OUTPUT / "lost_sales_year.parquet"
    m.to_parquet(out)

    total_u = m["lost_units"].sum()
    total_r = m["lost_rub_margin"].sum()
    sold_u = m["sold"].sum()
    print(f"=== УПУЩЕННЫЕ ПРОДАЖИ за {PERIOD[0]}…{PERIOD[1]} (консервативная оценка) ===")
    print(f"продано {sold_u:,.0f} шт; недопродано ≈ {total_u:,.0f} шт "
          f"({100*total_u/sold_u:.1f}% от продаж)")
    print(f"потерянная маржа ≈ {total_r/1e6:,.1f} млн ₽ за год")
    dead_days = (m["lost_units"] > 0).sum()
    print(f"дней-позиций с дефицитом: {dead_days:,} из {len(m):,} "
          f"({100*dead_days/len(m):.0f}%)")

    print("\n=== по точкам ===")
    g = m.groupby("canon_name").agg(
        недопродано_шт=("lost_units", "sum"),
        маржа_млн=("lost_rub_margin", lambda x: round(x.sum() / 1e6, 2)),
        продано_шт=("sold", "sum")).reset_index()
    g["% от продаж"] = (100 * g["недопродано_шт"] / g["продано_шт"]).round(1)
    g["недопродано_шт"] = g["недопродано_шт"].round()
    print(g.sort_values("маржа_млн", ascending=False).to_string(index=False))

    print("\n=== топ-15 позиций по потерянной марже ===")
    t = m.groupby(["canon_name", "name"]).agg(
        шт=("lost_units", "sum"),
        тыс_руб=("lost_rub_margin", lambda x: round(x.sum() / 1e3))).reset_index()
    print(t.nlargest(15, "тыс_руб").to_string(index=False))
    print(f"\n→ {out}")
    con.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
