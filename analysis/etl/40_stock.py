# -*- coding: utf-8 -*-
"""Остатки по маркетам — расчёт из движений (регистр 1С недоступен).

Почему так: AccumulationRegister_ЗапасыНаСкладах по OData отдаёт HTTP 500 (проверено 28.07,
три попытки с отступлением) — вслепую боевой сервер не долбим. Все движения у нас есть,
плюс частые инвентаризации дают якорь факта: по Точка 1 1 617 документов
за 20 месяцев, по Точки 3 1 102 — то есть считают почти ежедневно по группам товаров.

Схема для каждой пары (точка, SKU):
    остаток(D) = факт последней инвентаризации ≤ D
                 + приходы (перемещения на точку, приходные накладные)
                 − расходы (продажи, списания, перемещения с точки)
                 за период (дата якоря; D]

Выход:
  mart.fact_moves_daily — все движения день × точка × SKU со знаком и типом
  mart.fact_stock_daily — остаток на конец дня + дата якоря и «возраст» якоря
  mart.stock_accuracy   — контроль: расчёт против факта следующей инвентаризации

Запуск: PYTHONUTF8=1 py prognoz/analysis/etl/40_stock.py
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _db import connect_writer, jsonl

MARKETS = "('market', 'market_hub')"

RAW = {
    "inv_headers": ("Document_ИнвентаризацияЗапасов", {
        "Ref_Key": "VARCHAR", "Number": "VARCHAR", "Date": "TIMESTAMP", "Posted": "BOOLEAN",
        "DeletionMark": "BOOLEAN", "Организация_Key": "VARCHAR", "СтруктурнаяЕдиница_Key": "VARCHAR",
    }),
    "inv_lines": ("Document_ИнвентаризацияЗапасов_Запасы", {
        "Ref_Key": "VARCHAR", "LineNumber": "BIGINT", "Номенклатура_Key": "VARCHAR",
        "Количество": "DOUBLE", "КоличествoУчет": "DOUBLE", "Цена": "DOUBLE", "Сумма": "DOUBLE",
    }),
    "purchase_headers": ("Document_ПриходнаяНакладная", {
        "Ref_Key": "VARCHAR", "Date": "TIMESTAMP", "Posted": "BOOLEAN", "DeletionMark": "BOOLEAN",
        "СтруктурнаяЕдиница_Key": "VARCHAR",
    }),
    "purchase_lines": ("Document_ПриходнаяНакладная_Запасы", {
        "Ref_Key": "VARCHAR", "LineNumber": "BIGINT", "Номенклатура_Key": "VARCHAR",
        "Количество": "DOUBLE", "Сумма": "DOUBLE", "СтруктурнаяЕдиница_Key": "VARCHAR",
    }),
    "writeoff_headers": ("Document_СписаниеЗапасов", {
        "Ref_Key": "VARCHAR", "Number": "VARCHAR", "Date": "TIMESTAMP", "Posted": "BOOLEAN",
        "DeletionMark": "BOOLEAN", "СтруктурнаяЕдиница_Key": "VARCHAR",
        "ПричинаСписания": "VARCHAR", "Комментарий": "VARCHAR",
    }),
    "writeoff_lines": ("Document_СписаниеЗапасов_Запасы", {
        "Ref_Key": "VARCHAR", "LineNumber": "BIGINT", "Номенклатура_Key": "VARCHAR",
        "Количество": "DOUBLE", "Сумма": "DOUBLE",
    }),
}


def ingest_raw(con):
    for table, (src, cols) in RAW.items():
        spec = ", ".join(f"'{k}': '{v}'" for k, v in cols.items())
        t0 = time.time()
        con.execute(f"""
            CREATE OR REPLACE TABLE raw.{table} AS
            SELECT * FROM read_json('{jsonl(src)}', format='newline_delimited', columns={{{spec}}})
        """)
        n = con.execute(f"SELECT count(*) FROM raw.{table}").fetchone()[0]
        print(f"  raw.{table:<18} {n:>10,} строк ({time.time() - t0:.1f} с)", flush=True)


def build_moves(con):
    """Все движения по складам маркетов: знак + тип. Перемещения дают и приход, и расход."""
    con.execute(f"""
        CREATE OR REPLACE TABLE mart.fact_moves_daily AS
        WITH pts AS (
            SELECT point_key, coalesce(nullif(merged_into, ''), point_key) AS canon
            FROM mart.dim_point
            WHERE role IN {MARKETS}
               OR (role = 'merged' AND merged_into IN
                   (SELECT point_key FROM mart.dim_point WHERE role IN {MARKETS}))
        ),
        m AS (
            -- приход: перемещение НА точку
            SELECT CAST(h.Date AS DATE) d, h.СтруктурнаяЕдиницаПолучатель_Key pk,
                   l.Номенклатура_Key sku, l.Количество qty, 'move_in' AS kind
            FROM raw.move_headers h JOIN raw.move_lines l ON l.Ref_Key = h.Ref_Key
            WHERE NOT h.DeletionMark
            UNION ALL
            -- расход: перемещение С точки
            SELECT CAST(h.Date AS DATE), h.СтруктурнаяЕдиница_Key,
                   l.Номенклатура_Key, -l.Количество, 'move_out'
            FROM raw.move_headers h JOIN raw.move_lines l ON l.Ref_Key = h.Ref_Key
            WHERE NOT h.DeletionMark
            UNION ALL
            -- приход: внешняя поставка
            SELECT CAST(h.Date AS DATE),
                   coalesce(nullif(l.СтруктурнаяЕдиница_Key, '00000000-0000-0000-0000-000000000000'),
                            h.СтруктурнаяЕдиница_Key),
                   l.Номенклатура_Key, l.Количество, 'purchase'
            FROM raw.purchase_headers h JOIN raw.purchase_lines l ON l.Ref_Key = h.Ref_Key
            WHERE NOT h.DeletionMark
            UNION ALL
            -- расход: продажи (чистые, за вычетом возвратов)
            SELECT sale_date, point_key, sku_key, -(qty_sold - qty_returned), 'sale'
            FROM mart.fact_sales_daily
            UNION ALL
            -- расход: списания
            SELECT CAST(h.Date AS DATE), h.СтруктурнаяЕдиница_Key,
                   l.Номенклатура_Key, -l.Количество, 'writeoff'
            FROM raw.writeoff_headers h JOIN raw.writeoff_lines l ON l.Ref_Key = h.Ref_Key
            WHERE NOT h.DeletionMark
        )
        SELECT pts.canon AS point_key, m.d AS move_date, m.sku AS sku_key, m.kind,
               sum(m.qty) AS qty
        FROM m JOIN pts ON pts.point_key = m.pk
        GROUP BY 1, 2, 3, 4
    """)


def build_stock(con):
    """Остаток = якорь инвентаризации + движения после него."""
    con.execute(f"""
        CREATE OR REPLACE TABLE mart.stock_anchor AS
        WITH pts AS (
            SELECT point_key, coalesce(nullif(merged_into, ''), point_key) AS canon
            FROM mart.dim_point
            WHERE role IN {MARKETS}
               OR (role = 'merged' AND merged_into IN
                   (SELECT point_key FROM mart.dim_point WHERE role IN {MARKETS}))
        )
        SELECT pts.canon AS point_key, CAST(h.Date AS DATE) AS anchor_date,
               l.Номенклатура_Key AS sku_key, sum(l.Количество) AS qty_fact
        FROM raw.inv_headers h
        JOIN raw.inv_lines l ON l.Ref_Key = h.Ref_Key
        JOIN pts ON pts.point_key = h.СтруктурнаяЕдиница_Key
        WHERE NOT h.DeletionMark AND h.Posted
        GROUP BY 1, 2, 3
    """)
    # Дневная сетка от первого движения до последнего — только там, где были движения/якоря
    con.execute("""
        CREATE OR REPLACE TABLE mart.fact_stock_daily AS
        WITH days AS (
            SELECT DISTINCT point_key, sku_key, move_date AS d FROM mart.fact_moves_daily
            UNION
            SELECT DISTINCT point_key, sku_key, anchor_date FROM mart.stock_anchor
        ),
        anchored AS (
            SELECT d.point_key, d.sku_key, d.d,
                   (SELECT max(a.anchor_date) FROM mart.stock_anchor a
                     WHERE a.point_key = d.point_key AND a.sku_key = d.sku_key
                       AND a.anchor_date <= d.d) AS anchor_date
            FROM days d
        )
        SELECT a.point_key, a.sku_key, a.d AS stock_date, a.anchor_date,
               coalesce(anc.qty_fact, 0) AS anchor_qty,
               coalesce(anc.qty_fact, 0) + coalesce((
                   SELECT sum(m.qty) FROM mart.fact_moves_daily m
                    WHERE m.point_key = a.point_key AND m.sku_key = a.sku_key
                      AND m.move_date > coalesce(a.anchor_date, DATE '1900-01-01')
                      AND m.move_date <= a.d), 0) AS stock_end,
               a.d - a.anchor_date AS anchor_age_days
        FROM anchored a
        LEFT JOIN mart.stock_anchor anc
               ON anc.point_key = a.point_key AND anc.sku_key = a.sku_key
              AND anc.anchor_date = a.anchor_date
    """)


def build_accuracy(con):
    """Контроль качества: расчётный остаток накануне инвентаризации против её факта."""
    con.execute("""
        CREATE OR REPLACE TABLE mart.stock_accuracy AS
        SELECT a.point_key, a.sku_key, a.anchor_date AS inv_date, a.qty_fact,
               s.stock_end AS qty_calc, s.anchor_age_days,
               s.stock_end - a.qty_fact AS diff
        FROM mart.stock_anchor a
        JOIN mart.fact_stock_daily s
          ON s.point_key = a.point_key AND s.sku_key = a.sku_key
         AND s.stock_date = a.anchor_date - INTERVAL 1 DAY
        WHERE s.anchor_date IS NOT NULL
    """)


def main():
    con = connect_writer()
    con.execute("CREATE SCHEMA IF NOT EXISTS raw")
    con.execute("CREATE SCHEMA IF NOT EXISTS mart")
    print("1) сырьё:")
    ingest_raw(con)
    for step, fn in (("2) движения", build_moves), ("3) остатки", build_stock),
                     ("4) контроль", build_accuracy)):
        t0 = time.time()
        fn(con)
        print(f"{step}: {time.time() - t0:.1f} с", flush=True)

    print("\n=== Движения по маркетам (строк по типам) ===")
    for r in con.execute("""
        SELECT kind, count(*) AS rows, CAST(sum(abs(qty)) AS BIGINT) AS qty,
               min(move_date) AS d0, max(move_date) AS d1
        FROM mart.fact_moves_daily GROUP BY 1 ORDER BY rows DESC
    """).fetchall():
        print(f"  {r[0]:<10} {r[1]:>9,} строк  {r[2]:>10,} шт  {r[3]} .. {r[4]}")

    print("\n=== Сходимость расчёта с фактом инвентаризации ===")
    for r in con.execute("""
        SELECT p.canon_name,
               count(*) AS checks,
               round(100.0 * sum(CASE WHEN abs(diff) < 0.001 THEN 1 ELSE 0 END) / count(*), 1) AS pct_exact,
               round(100.0 * sum(CASE WHEN abs(diff) <= 1 THEN 1 ELSE 0 END) / count(*), 1) AS pct_within_1,
               round(median(abs(diff)), 2) AS med_abs_diff
        FROM mart.stock_accuracy a JOIN mart.dim_point p ON p.point_key = a.point_key
        WHERE a.anchor_age_days <= 14
        GROUP BY 1 ORDER BY checks DESC
    """).fetchall():
        print(f"  {r[0]:<26} сверок {r[1]:>7,}  точно {r[2]:>5}%  ±1 шт {r[3]:>5}%  медиана |откл| {r[4]}")
    con.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
