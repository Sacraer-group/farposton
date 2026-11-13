# -*- coding: utf-8 -*-
"""QC витрин прогноза: качество данных, известные грабли — числа вместо предположений.

Проверки:
1. Лаг проводки продаж (Posted=false по возрасту документа) → параметр L для бэктеста.
2. Непрерывность продаж по маркетам (дыры в днях за последние 90 дней).
3. Переходный март-2026: пересечение каналов заявок по (точка, день, SKU).
4. Весовые/дробные SKU в прогнозной области (ДЕМО кухня).
5. Сверка «заказано (transfer) vs собрано vs продано» по ДЕМО точка 1 за неделю.

Запуск: PYTHONUTF8=1 py prognoz/analysis/etl/30_qc.py
Отчёт: печать + prognoz/analysis/output/qc_report.md (вне git).
"""
import io
import sys
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _db import OUTPUT, connect_reader

MARKETS = "('market', 'market_hub')"


def q(con, sql, title):
    print(f"\n### {title}")
    rows = con.execute(sql).fetchall()
    cols = [d[0] for d in con.description]
    print("| " + " | ".join(cols) + " |")
    print("|" + "---|" * len(cols))
    for r in rows:
        print("| " + " | ".join(str(x) for x in r) + " |")
    return rows


def main():
    con = connect_reader()
    buf = io.StringIO()

    with redirect_stdout(buf):
        print("# QC витрин прогноза (данные до даты выгрузки 2026-07-21)")

        q(con, f"""
            SELECT (DATE '2026-07-20' - sale_date) AS age_days,
                   count(*) AS total_rows,
                   sum(CASE WHEN NOT all_posted THEN 1 ELSE 0 END) AS unposted_rows,
                   round(100.0 * sum(CASE WHEN NOT all_posted THEN 1 ELSE 0 END) / count(*), 1) AS pct_unposted
            FROM mart.fact_sales_daily f
            JOIN mart.dim_point p ON p.point_key = f.point_key
            WHERE p.role IN {MARKETS} AND sale_date >= DATE '2026-06-20'
            GROUP BY 1 ORDER BY 1
        """, "1. Лаг проводки: доля непроведённых строк продаж по возрасту дня (маркеты)")

        q(con, f"""
            WITH days AS (
                SELECT p.canon_name, generate_series AS d
                FROM mart.dim_point p,
                     generate_series(DATE '2026-04-20', DATE '2026-07-20', INTERVAL 1 DAY)
                WHERE p.role IN {MARKETS}
            ),
            have AS (
                SELECT DISTINCT p.canon_name, f.sale_date AS d
                FROM mart.fact_sales_daily f
                JOIN mart.dim_point p ON p.point_key = f.point_key
                WHERE p.role IN {MARKETS}
            )
            SELECT days.canon_name,
                   count(*) AS days_total,
                   sum(CASE WHEN have.d IS NULL THEN 1 ELSE 0 END) AS days_missing
            FROM days LEFT JOIN have ON have.canon_name = days.canon_name AND have.d = CAST(days.d AS DATE)
            GROUP BY 1 ORDER BY 1
        """, "2. Дыры в продажах маркетов, 20.04–20.07.2026")

        q(con, f"""
            WITH t AS (
                SELECT f.point_key, f.trade_date, f.sku_key, f.qty_ordered
                FROM mart.fact_orders_daily f WHERE channel = 'transfer'
                  AND trade_date BETWEEN DATE '2026-03-01' AND DATE '2026-03-31'
            ),
            z AS (
                SELECT f.point_key, f.trade_date, f.sku_key, f.qty_ordered
                FROM mart.fact_orders_daily f WHERE channel = 'zakaz'
                  AND trade_date BETWEEN DATE '2026-03-01' AND DATE '2026-03-31'
            )
            SELECT p.canon_name,
                   (SELECT count(*) FROM t WHERE t.point_key = p.point_key) AS transfer_rows,
                   (SELECT count(*) FROM z WHERE z.point_key = p.point_key) AS zakaz_rows,
                   (SELECT count(*) FROM t JOIN z USING (point_key, trade_date, sku_key)
                     WHERE t.point_key = p.point_key) AS same_day_sku_overlap
            FROM mart.dim_point p WHERE p.role IN {MARKETS} ORDER BY 1
        """, "3. Март-2026: пересечение каналов по (день, SKU) на маркетах")

        q(con, """
            SELECT s.is_weight, s.allows_fraction,
                   count(DISTINCT f.sku_key) AS skus,
                   CAST(sum(f.qty_ordered) AS BIGINT) AS qty
            FROM mart.fact_orders_daily f
            JOIN mart.dim_sku s ON s.sku_key = f.sku_key
            WHERE f.channel = 'transfer'
            GROUP BY 1, 2 ORDER BY qty DESC
        """, "4. Весовые/дробные SKU в transfer-заявках")

        q(con, """
            SELECT f.trade_date,
                   CAST(sum(f.qty_ordered) AS INT) AS ordered,
                   CAST(sum(f.qty_collected) AS INT) AS collected,
                   (SELECT CAST(sum(s.qty_sold) AS INT) FROM mart.fact_sales_daily s
                     WHERE s.point_key = f.point_key AND s.sale_date = f.trade_date
                       AND s.sku_key IN (SELECT DISTINCT sku_key FROM mart.fact_orders_daily
                                          WHERE channel='transfer' AND point_key = f.point_key)) AS sold_same_skus
            FROM mart.fact_orders_daily f
            WHERE f.channel = 'transfer'
              AND f.point_key = 'DEMO-POINT-01'  -- Демо точка 1
              AND f.trade_date BETWEEN current_date - INTERVAL 7 DAY AND current_date
            GROUP BY f.trade_date, f.point_key ORDER BY 1
        """, "5. Демо точка 1, выбранная неделя: заказано vs собрано vs продано (по заявочным SKU)")

        q(con, """
            SELECT round(100.0 * sum(CASE WHEN qty_collected IS NOT NULL
                                           AND qty_collected < qty_ordered THEN 1 ELSE 0 END) / count(*), 1)
                     AS pct_lines_undercollected,
                   round(100.0 * sum(CASE WHEN qty_collected IS NULL THEN 1 ELSE 0 END) / count(*), 1)
                     AS pct_lines_no_collected
            FROM mart.transfer_order_lines WHERE NOT deleted
        """, "6. Недосбор фабрикой: доля строк transfer с собрано<заказано")

        q(con, """
            SELECT p.canon_name, f.sale_date,
                   CAST(sum(f.qty_sold) AS INT) AS qty_from_cheki
            FROM mart.fact_sales_daily f
            JOIN mart.dim_point p ON p.point_key = f.point_key
            WHERE f.n_docs = 0
            GROUP BY 1, 2 ORDER BY 2
        """, "7. Точко-дни без документа продаж, добранные из чеков (патч 20_marts)")

        q(con, f"""
            WITH c AS (SELECT point_key, coalesce(nullif(merged_into,''), point_key) pt
                       FROM mart.dim_point WHERE role IN ('market','market_hub','merged')),
            chk AS (
                SELECT sum(cs.qty) q FROM mart.cheki_sales_net cs
                JOIN c ON c.point_key = cs.point_key
                JOIN mart.dim_sku s ON s.sku_key = cs.sku_key
                WHERE s.group_path LIKE 'Меню%'),
            dly AS (
                SELECT sum(f.qty_sold - f.qty_returned) q FROM mart.fact_sales_daily f
                JOIN c ON c.point_key = f.point_key
                JOIN mart.dim_sku s ON s.sku_key = f.sku_key
                WHERE s.group_path LIKE 'Меню%')
            SELECT CAST(chk.q AS INT) AS cheki_net_qty, CAST(dly.q AS INT) AS daily_qty,
                   round(100.0 * (chk.q - dly.q) / dly.q, 1) AS gap_pct
            FROM chk, dly
        """, "8. Разрыв «чеки-нетто vs витрина продаж» (остаток — разузлование наборов)")

    text = buf.getvalue()
    print(text)
    OUTPUT.mkdir(parents=True, exist_ok=True)
    (OUTPUT / "qc_report.md").write_text(text, encoding="utf-8")
    print(f"\n→ сохранено в {OUTPUT / 'qc_report.md'}")
    con.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
