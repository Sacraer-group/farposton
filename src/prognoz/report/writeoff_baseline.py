# -*- coding: utf-8 -*-
"""Замер «как есть»: списания готовой продукции по маркетам — база для сравнения до/после.

Считает по каждой точке и позиции за период: продано, списано, доля списания и её оценку
в рублях по средней цене продажи. Это точка отсчёта пилота: «сколько выбрасываем сейчас».

⚠️ Оценка в рублях — по ЦЕНАМ ПРОДАЖИ (в строках списания суммы заполнены лишь в 5% случаев).
Реальные потери по себестоимости ниже: в общепите это обычно 30–40% от цены продажи.
Считаем и то и другое, коэффициент себестоимости — параметр (по умолчанию 0,35).

Скоуп: единый скоуп прогноза из scope.py (готовая еда без расходников и бара) —
упаковка и хознужды из документа списания исключены, к прогнозу они отношения не имеют.

Запуск: PYTHONUTF8=1 py prognoz/src/prognoz/report/writeoff_baseline.py [--since=2026-01-01]
"""
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "analysis" / "etl"))
from _db import OUTPUT, connect_reader
from prognoz.assortment import sql_exclusion_predicate  # noqa: E402

# Доля себестоимости в цене продажи. С 03.08.2026 это демонстрационное допущение (базовая формула на встрече:
# «ориентир грубо 33%»), а не наша гипотеза — до этого стояло 0,35 «на глазок».
# Проверить по 1С нечем: у 75,9% списанных единиц сумма списания равна нулю.
COST_RATIO = 0.33
# Скоуп — из scope.py, как везде. До 04.08 здесь жило ТРЕТЬЕ, независимое определение
# скоупа (FOOD_GROUP = "%ДЕМО кухня%") — оно давало другие числа, чем замер «ДО»
# и прогноз, и никто этого не видел. Числа замера при переходе изменились — см. HISTORY.
from prognoz.scope import GROUP_LIKE, GROUP_NOT_LIKE, GROUP_NOT_LIKE_BAR  # noqa: E402

WO_ASSORTMENT = sql_exclusion_predicate(
    "metric_scope", point_expr="pts.canon", sku_expr="l.Номенклатура_Key",
    date_expr="h.Date")
SALES_ASSORTMENT = sql_exclusion_predicate(
    "metric_scope", point_expr="pts.canon", sku_expr="f.sku_key",
    date_expr="f.sale_date")

SQL = f"""
WITH pts AS (
    SELECT point_key, coalesce(nullif(merged_into, ''), point_key) AS canon
    FROM mart.dim_point WHERE role IN ('market', 'market_hub', 'merged')
),
wo AS (
    SELECT pts.canon AS pk, l.Номенклатура_Key AS sku, sum(l.Количество) AS q
    FROM raw.writeoff_headers h
    JOIN raw.writeoff_lines l ON l.Ref_Key = h.Ref_Key
    JOIN pts ON pts.point_key = h.СтруктурнаяЕдиница_Key
    JOIN mart.dim_sku s ON s.sku_key = l.Номенклатура_Key
    WHERE NOT h.DeletionMark AND h.Date >= $since::DATE AND s.group_path LIKE $grp
      AND s.group_path NOT LIKE $ngrp AND s.group_path NOT LIKE $nbar
      AND {WO_ASSORTMENT}
    GROUP BY 1, 2
),
sl AS (
    SELECT pts.canon AS pk, f.sku_key AS sku,
           sum(f.qty_sold - f.qty_returned) AS q, sum(f.revenue) AS rev
    FROM mart.fact_sales_daily f
    JOIN pts ON pts.point_key = f.point_key
    JOIN mart.dim_sku s ON s.sku_key = f.sku_key
    WHERE f.sale_date >= $since::DATE AND s.group_path LIKE $grp
      AND s.group_path NOT LIKE $ngrp AND s.group_path NOT LIKE $nbar
      AND {SALES_ASSORTMENT}
    GROUP BY 1, 2
)
SELECT p.canon_name AS "Точка", s.name AS "Позиция", s.group_path AS "Группа",
       coalesce(sl.q, 0) AS "Продано, шт",
       coalesce(wo.q, 0) AS "Списано, шт",
       round(100.0 * coalesce(wo.q, 0) / nullif(coalesce(sl.q, 0) + coalesce(wo.q, 0), 0), 1)
           AS "Доля списания, %",
       round(sl.rev / nullif(sl.q, 0), 1) AS "Средняя цена, руб",
       round(coalesce(wo.q, 0) * (sl.rev / nullif(sl.q, 0)), 0) AS "Списано в ценах продажи, руб"
FROM sl FULL OUTER JOIN wo USING (pk, sku)
JOIN mart.dim_point p ON p.point_key = coalesce(sl.pk, wo.pk)
JOIN mart.dim_sku s ON s.sku_key = coalesce(sl.sku, wo.sku)
WHERE coalesce(wo.q, 0) > 0 OR coalesce(sl.q, 0) > 0
"""


def main():
    since = "2026-01-01"
    for arg in sys.argv[1:]:
        if arg.startswith("--since="):
            since = arg.split("=", 1)[1]

    con = connect_reader()
    df = con.execute(SQL, {"since": since, "grp": GROUP_LIKE,
                          "ngrp": GROUP_NOT_LIKE, "nbar": GROUP_NOT_LIKE_BAR}).fetchdf()
    con.close()

    df["Потери по себестоимости, руб"] = (df["Списано в ценах продажи, руб"] * COST_RATIO).round(0)
    df = df.sort_values("Списано в ценах продажи, руб", ascending=False)

    OUTPUT.mkdir(parents=True, exist_ok=True)
    df.to_excel(OUTPUT / "Списания_маркетов_замер.xlsx", index=False, freeze_panes=(1, 2))
    df.to_parquet(OUTPUT / "writeoff_baseline.parquet", index=False)

    by_point = df.groupby("Точка").agg(
        продано=("Продано, шт", "sum"), списано=("Списано, шт", "sum"),
        руб=("Списано в ценах продажи, руб", "sum"),
        себест=("Потери по себестоимости, руб", "sum")).reset_index()
    by_point["доля_%"] = (100 * by_point["списано"] /
                          (by_point["продано"] + by_point["списано"])).round(2)

    months = (pd.Timestamp("2026-07-26") - pd.Timestamp(since)).days / 30.4
    print(f"ЗАМЕР «КАК ЕСТЬ»: готовая продукция маркетов с {since} (~{months:.1f} мес)\n")
    print(by_point.sort_values("руб", ascending=False).to_string(index=False))
    total_rub = by_point["руб"].sum()
    total_cost = by_point["себест"].sum()
    print(f"\nВСЕГО списано: {by_point['списано'].sum():,.0f} шт")
    print(f"  в ценах продажи: {total_rub:,.0f} руб  (в год ≈ {total_rub / months * 12:,.0f})")
    print(f"  по себестоимости (×{COST_RATIO}): {total_cost:,.0f} руб  "
          f"(в год ≈ {total_cost / months * 12:,.0f})")
    print(f"\nЕсли снизить списания на 20% → экономия ≈ {total_cost / months * 12 * 0.2:,.0f} руб/год")
    print(f"на 30% → ≈ {total_cost / months * 12 * 0.3:,.0f} руб/год")
    print(f"\n→ {OUTPUT / 'Списания_маркетов_замер.xlsx'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
