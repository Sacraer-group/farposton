# -*- coding: utf-8 -*-
"""Замер «ДО» и ряды для панели: продажи/уценка/списания по месяцам, профили, режимы.

Каждый показатель — в двух вариантах скоупа:
  before.*          — текущий скоуп прогноза (без бара);
  before.with_bar.* — скоуп замороженного снимка от 03.08 (с баром).
Так конфликт «выручка 147,7 или 148,6» перестаёт быть конфликтом: это два разных
факта с явными id, и таблица-мостик показывает, что разница — ровно бар.
"""
from __future__ import annotations

from datetime import date

import pandas as pd

from prognoz.assortment import sql_exclusion_predicate
from prognoz.facts.registry import block
from prognoz.scope import (COMPARABLE_FULL_PERIOD, GROUP_LIKE, GROUP_NOT_LIKE,
                           GROUP_NOT_LIKE_BAR, canon_cte)

SINCE, UNTIL = date(2025, 8, 1), date(2026, 7, 31)
SRC = "facts.blocks.before:compute"


def _filter(with_bar: bool, *, fact_alias: str, date_col: str) -> tuple[str, list]:
    sql = "s.group_path LIKE ? AND s.group_path NOT LIKE ?"
    params: list = [GROUP_LIKE, GROUP_NOT_LIKE]
    if not with_bar:
        sql += " AND s.group_path NOT LIKE ?"
        params.append(GROUP_NOT_LIKE_BAR)
    sql += " AND " + sql_exclusion_predicate(
        "metric_scope", point_expr="c.pt",
        sku_expr=f"{fact_alias}.sku_key",
        date_expr=f"{fact_alias}.{date_col}")
    return sql, params


def totals(con, with_bar: bool, comparable_only: bool = False) -> dict:
    f, p = _filter(with_bar, fact_alias="x", date_col="sale_date")
    fw, pw = _filter(with_bar, fact_alias="w", date_col="wo_date")
    pt_cond = ""
    if comparable_only:
        pts = ", ".join(f"'{x}'" for x in COMPARABLE_FULL_PERIOD)
        pt_cond = (f" AND c.pt IN (SELECT point_key FROM mart.dim_point "
                   f"WHERE canon_name IN ({pts}))")
    row = con.execute(f"""
        WITH {canon_cte()}
        SELECT round(sum(x.qty_sold - x.qty_returned))          AS sold,
               round(sum(x.revenue) / 1e6, 1)                   AS revenue_m,
               round(sum(x.revenue_gross) / 1e6, 1)             AS revenue_gross_m,
               round(sum(x.disc_sum) / 1e6, 1)                  AS disc_m,
               round(sum(x.u40_strict))                         AS u40_lo,
               round(sum(x.u40))                                AS u40_hi
        FROM mart.fact_sales_daily x
        JOIN c ON c.point_key = x.point_key
        JOIN mart.dim_sku s ON s.sku_key = x.sku_key
        WHERE {f} AND x.sale_date BETWEEN ? AND ?{pt_cond}
    """, p + [SINCE, UNTIL]).fetchdf().iloc[0]
    wo = con.execute(f"""
        WITH {canon_cte()}
        SELECT round(sum(w.qty_writeoff)) FROM mart.fact_writeoff_daily w
        JOIN c ON c.point_key = w.point_key
        JOIN mart.dim_sku s ON s.sku_key = w.sku_key
        WHERE {fw} AND w.wo_date BETWEEN ? AND ?{pt_cond}
    """, pw + [SINCE, UNTIL]).fetchone()[0] or 0
    d = row.to_dict()
    d["writeoff_units"] = wo
    # Формула протокола: списано ÷ продано (НЕ ÷(продано+списано))
    d["writeoff_share"] = round(100 * wo / d["sold"], 1) if d["sold"] else None
    d["u40_share_lo"] = round(100 * d["u40_lo"] / d["sold"], 1)
    d["u40_share_hi"] = round(100 * d["u40_hi"] / d["sold"], 1)
    return d


def by_month(con, with_bar: bool) -> pd.DataFrame:
    f, p = _filter(with_bar, fact_alias="x", date_col="sale_date")
    return con.execute(f"""
        WITH {canon_cte()}
        SELECT strftime(x.sale_date, '%Y-%m') AS month,
               nm.canon_name AS point,
               round(sum(x.qty_sold - x.qty_returned)) AS sold,
               round(sum(x.revenue) / 1e6, 2) AS revenue_m,
               round(100 * sum(x.u40) / nullif(sum(x.qty_sold - x.qty_returned), 0), 1)
                   AS u40_share
        FROM mart.fact_sales_daily x
        JOIN c ON c.point_key = x.point_key
        JOIN mart.dim_point nm ON nm.point_key = c.pt
        JOIN mart.dim_sku s ON s.sku_key = x.sku_key
        WHERE {f} AND x.sale_date BETWEEN ? AND ?
        GROUP BY 1, 2 ORDER BY 1, 2
    """, p + [SINCE, UNTIL]).fetchdf()


def writeoff_by_month(con, with_bar: bool) -> pd.DataFrame:
    f, p = _filter(with_bar, fact_alias="w", date_col="wo_date")
    return con.execute(f"""
        WITH {canon_cte()}
        SELECT strftime(w.wo_date, '%Y-%m') AS month,
               round(sum(w.qty_writeoff)) AS qty
        FROM mart.fact_writeoff_daily w
        JOIN c ON c.point_key = w.point_key
        JOIN mart.dim_sku s ON s.sku_key = w.sku_key
        WHERE {f} AND w.wo_date BETWEEN ? AND ?
        GROUP BY 1 ORDER BY 1
    """, p + [SINCE, UNTIL]).fetchdf()


def weekday_profile(con, with_bar: bool) -> pd.DataFrame:
    f, p = _filter(with_bar, fact_alias="x", date_col="sale_date")
    # ⚠️ DuckDB: 0=вс..6=сб; сдвиг в понедельник-первый через (dow+6)%7
    return con.execute(f"""
        WITH {canon_cte()},
        d AS (
            SELECT x.sale_date, (dayofweek(x.sale_date) + 6) % 7 AS wd,
                   sum(x.qty_sold - x.qty_returned) AS qty
            FROM mart.fact_sales_daily x
            JOIN c ON c.point_key = x.point_key
            JOIN mart.dim_sku s ON s.sku_key = x.sku_key
            WHERE {f} AND x.sale_date BETWEEN ? AND ?
            GROUP BY 1, 2)
        SELECT wd, round(avg(qty)) AS qty,
               round(100.0 * avg(qty) / (SELECT avg(qty) FROM d) - 100, 1) AS dev_pct
        FROM d GROUP BY 1 ORDER BY 1
    """, p + [SINCE, UNTIL]).fetchdf()


def regimes(con) -> pd.DataFrame:
    return con.execute(f"""
        WITH {canon_cte()}
        SELECT regime, count(*) AS rows_n,
               round(100.0 * count(*) / sum(count(*)) OVER (), 1) AS share_pct,
               round(100.0 * avg(CASE WHEN writeoff >= 0.5 THEN 1 ELSE 0 END), 1)
                   AS writeoff_row_share
        FROM mart.fact_regime_daily r
        JOIN c ON c.point_key = r.point_key
        WHERE {sql_exclusion_predicate(
            "metric_scope", point_expr="c.pt", sku_expr="r.sku_key",
            date_expr="r.trade_date")}
        GROUP BY 1 ORDER BY 1
    """).fetchdf()


def points_age(con, with_bar: bool) -> pd.DataFrame:
    f, p = _filter(with_bar, fact_alias="x", date_col="sale_date")
    return con.execute(f"""
        WITH {canon_cte()}
        SELECT nm.canon_name AS point,
               min(x.sale_date) AS first_sale,
               count(DISTINCT x.sale_date) AS days_with_sales,
               count(DISTINCT CASE WHEN x.sale_date >=
                     (SELECT max(sale_date) FROM mart.fact_sales_daily) - 27
                     THEN x.sku_key END) AS sku_28d
        FROM mart.fact_sales_daily x
        JOIN c ON c.point_key = x.point_key
        JOIN mart.dim_point nm ON nm.point_key = c.pt
        JOIN mart.dim_sku s ON s.sku_key = x.sku_key
        WHERE {f}
        GROUP BY 1 ORDER BY 2
    """, p).fetchdf()


@block("before")
def compute(ctx, reg) -> None:
    period = f"{SINCE:%d.%m.%Y}–{UNTIL:%d.%m.%Y}"
    facts = []
    UNITS = {"sold": "шт", "revenue_m": "млн ₽", "revenue_gross_m": "млн ₽",
             "disc_m": "млн ₽", "u40_lo": "шт", "u40_hi": "шт",
             "writeoff_units": "шт", "writeoff_share": "%",
             "u40_share_lo": "%", "u40_share_hi": "%"}

    def method_for(key: str, comparable: bool) -> str:
        base = ("сеть ПЕРЕМЕННОГО состава: 2 точки в начале года → 5 в конце; "
                "сравнение год-к-году корректно только по before.comparable.*"
                if not comparable else
                "только точки с полным годом (Точки 1 + Точка 2) — "
                "единственный корректный контур для сравнения ДО→ПОСЛЕ")
        if key.startswith("writeoff"):
            base += "; формула: списано ÷ продано; покрытие списаний 2025 неполное (добор)"
        if key.startswith("u40"):
            base += "; интервал: нижняя оценка предполагает прочие скидки −10%, верхняя — всё −40%"
        return base

    variants = ((False, "before", False), (True, "before.with_bar", False),
                (False, "before.comparable", True))
    for with_bar, prefix, comparable in variants:
        t = totals(ctx.con, with_bar, comparable_only=comparable)
        label = ("сопоставимые точки, без бара" if comparable
                 else "с баром" if with_bar else "без бара (скоуп прогноза)")
        facts += [{"id": f"{prefix}.{k}", "value": v, "unit": UNITS.get(k, ""),
                   "title": f"замер ДО ({label}): {k}", "period": period,
                   "method": method_for(k, comparable)}
                  for k, v in t.items() if v is not None]

    reg.table("before.by_month", by_month(ctx.con, False),
              title="Продажи и уценка по месяцам и точкам (без бара)")
    reg.table("before.writeoff_by_month", writeoff_by_month(ctx.con, False),
              title="Списания по месяцам (без бара)")
    reg.table("before.weekday", weekday_profile(ctx.con, False),
              title="Профиль дня недели (без бара)")
    reg.table("before.regimes", regimes(ctx.con), title="Режимы A–D")
    age = points_age(ctx.con, False)
    reg.table("before.points_age", age, title="Возраст точек")
    for r in age.itertuples():
        facts.append({"id": f"points.days_with_sales.{r.point}",
                      "value": int(r.days_with_sales), "unit": "дн.",
                      "title": f"{r.point}: дней с продажами",
                      "period": f"по {ctx.data_stamp.get('max_sale_date', '')}"})
    reg.add_many("before", SRC, facts)
