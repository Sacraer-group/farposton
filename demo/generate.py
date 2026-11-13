"""Build a deterministic, wholly fictional shawarma-chain forecast demo database.

Usage: python generate.py /absolute/path/prognoz.duckdb
The target must not exist. No customer export or network service is read.
"""
from __future__ import annotations

import argparse
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import duckdb

def generate(target: Path, as_of: date) -> None:
    target = target.resolve()
    if target.exists():
        raise FileExistsError(f"Refusing to overwrite {target}")
    target.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(target))
    try:
        end = as_of - timedelta(days=1)
        start = end - timedelta(days=484)
        third_point_start = end - timedelta(days=119)
        novelty_start = end - timedelta(days=16)
        con.execute("SET memory_limit='500MB'")
        con.execute("SET threads=1")
        con.execute("CREATE SCHEMA mart")
        con.execute("""
            CREATE TABLE mart.dim_point AS SELECT * FROM (VALUES
              ('DEMO-POINT-01','D01','ДЕМО — Вокзальная','market','ДЕМО Шаурма',false,false,'ДЕМО — Вокзальная','market',''),
              ('DEMO-POINT-02','D02','ДЕМО — Университет','market','ДЕМО Шаурма',false,false,'ДЕМО — Университет','market',''),
              ('DEMO-POINT-03','D03','ДЕМО — Набережная','market','ДЕМО Шаурма',false,false,'ДЕМО — Набережная','market','')
            ) v(point_key,code,name,unit_type,org_name,inactive,deleted,canon_name,role,merged_into)
        """)
        con.execute("""
            CREATE TABLE mart.dim_sku AS SELECT
              'DEMO-SKU-' || lpad(i::VARCHAR,2,'0') sku_key,
              'S' || lpad(i::VARCHAR,2,'0') code,
              'ДЕМО — ' || name AS name,
              'ДЕМО — ' || name AS full_name,
              'Меню / ДЕМО ' || grp AS group_path,
              'ДЕМО ' || grp AS category,
              false AS is_weight, false AS allows_fraction, true AS forbids_fraction,
              grp = 'Напитки' AS is_drink, 'ДЕМО готовый товар' AS sku_type,
              false AS deleted, false AS inactive
            FROM (VALUES
              (1,'Шаурма с курицей','Шаурма'),(2,'Шаурма с говядиной','Шаурма'),
              (3,'Шаурма с фалафелем','Шаурма'),(4,'Шаурма острая','Шаурма'),
              (5,'Шаурма сырная','Шаурма'),(6,'Шаурма мини','Шаурма'),
              (7,'Картофель по-деревенски','Гарниры'),(8,'Хумус с питой','Гарниры'),
              (9,'Салат табуле','Гарниры'),(10,'Лимонад гранатовый','Напитки'),
              (11,'Айран','Напитки'),(12,'Соус чесночный','Гарниры'),
              (13,'Шаурма тыквенная — новинка','Шаурма'),
              (14,'Фалафель-бокс — новинка','Гарниры')
            ) v(i,name,grp)
        """)
        con.execute(f"""
            CREATE TEMP TABLE scenario AS
            WITH days AS (SELECT d::DATE d FROM generate_series(DATE '{start}', DATE '{end}', INTERVAL 1 DAY) t(d)),
            base AS (
              SELECT d, p.point_key, s.sku_key, s.name, s.category,
                     CAST(right(s.sku_key,2) AS INT) i,
                     CAST(right(p.point_key,2) AS INT) j,
                     (hash(p.point_key || s.sku_key || d::VARCHAR) % 7)::INT jitter
              FROM days CROSS JOIN mart.dim_point p CROSS JOIN mart.dim_sku s
              WHERE (p.point_key <> 'DEMO-POINT-03' OR d >= DATE '{third_point_start}')
                AND (s.sku_key NOT IN ('DEMO-SKU-13','DEMO-SKU-14') OR d >= DATE '{novelty_start}')
            ), quantities AS (
              SELECT *,
                greatest(0, round((CASE WHEN i <= 6 THEN 10 WHEN i <= 9 THEN 6 WHEN i <= 11 THEN 8 ELSE 3 END)
                  * (CASE WHEN dayofweek(d) IN (0,6) THEN 1.25 ELSE 1 END)
                  * (CASE WHEN month(d) IN (6,7,8) AND i IN (10,11) THEN 1.45 ELSE 1 END)
                  * (CASE WHEN j = 2 THEN 1.3 WHEN j = 3 THEN 0.8 ELSE 1 END)
                  + jitter - 3, 0))::INT demand
              FROM base
            )
            SELECT *,
              greatest(0, demand - CASE WHEN jitter = 0 AND i <= 6 THEN 4 ELSE 0 END)::INT sold,
              (CASE WHEN jitter = 0 AND i <= 6 THEN 0 WHEN jitter = 6 THEN 2 ELSE 1 END)::INT wo,
              (CASE WHEN jitter IN (5,6) THEN 1 ELSE 0 END)::INT markdown,
              (CASE WHEN i <= 6 THEN 290 + i*19 WHEN i <= 9 THEN 180 + i*8
                    WHEN i <= 11 THEN 140 + i*4 ELSE 95 END)::DOUBLE price
            FROM quantities
        """)
        con.execute("""
            CREATE TABLE mart.fact_sales_daily AS
            SELECT d sale_date, point_key, sku_key, sold::DOUBLE qty_sold,
                   0.0 qty_returned, (sold*price - markdown*price*0.4)::DOUBLE revenue,
                   (sold*price)::DOUBLE revenue_gross, (markdown*price*0.4)::DOUBLE disc_sum,
                   markdown::DOUBLE u40, markdown::DOUBLE u40_strict,
                   1 n_docs, true all_posted FROM scenario
        """)
        con.execute("""
            CREATE TABLE mart.fact_orders_daily AS
            SELECT d trade_date, point_key, sku_key, (demand+wo+CASE WHEN jitter=1 THEN 2 ELSE 0 END)::DOUBLE qty_ordered,
                   (demand+wo)::DOUBLE qty_collected, 'transfer' channel,
                   1 n_docs, true all_posted, 0 n_unposted FROM scenario
        """)
        con.execute("""
            CREATE TABLE mart.fact_supply_daily AS
            SELECT d supply_date, point_key, sku_key, (sold+wo+markdown+CASE WHEN jitter=6 THEN 2 ELSE 0 END)::DOUBLE qty,
                   'move' channel, true all_posted, 0 n_unposted FROM scenario
        """)
        con.execute("""
            CREATE TABLE mart.fact_writeoff_daily AS
            SELECT d wo_date, point_key, sku_key, wo::DOUBLE qty_writeoff,
                   (wo*price*0.33)::DOUBLE wo_sum, 1 n_docs, true all_posted, 0 n_unposted
            FROM scenario WHERE wo > 0
        """)
        con.execute("""
            CREATE TABLE mart.cheki_sales_net AS
            SELECT point_key, d trade_day,
                   d + (CASE WHEN h=1 THEN INTERVAL 12 HOUR
                             WHEN h=2 THEN INTERVAL 17 HOUR ELSE INTERVAL 21 HOUR END) ts,
                   sku_key,
                   (sold / CASE WHEN jitter=0 AND i<=6 THEN 2.0 ELSE 3.0 END)::DOUBLE qty,
                   price,
                   (sold*price / CASE WHEN jitter=0 AND i<=6 THEN 2.0 ELSE 3.0 END
                    - CASE WHEN h=3 THEN markdown*price*0.4 ELSE 0 END)::DOUBLE amount,
                   (CASE WHEN h=3 THEN markdown*price*0.4 ELSE 0 END)::DOUBLE disc_manual,
                   0.0 disc_auto, false with_writeoff, 1 sign
            FROM scenario CROSS JOIN generate_series(1,3) t(h)
            WHERE sold > 0 AND (jitter<>0 OR i>6 OR h<=2)
        """)
        con.execute("CREATE VIEW mart.cheki_sales AS SELECT * FROM mart.cheki_sales_net")
        con.execute("""
            CREATE TABLE mart.cheki_headers AS
            SELECT 'DEMO-CHECK-' || point_key || '-' || trade_day::VARCHAR || '-'
                   || sku_key || '-' || extract(hour FROM ts)::INT::VARCHAR AS chek_key,
                   ts, trade_day, point_key, 'DEMO-UNIT' AS unit_key,
                   'Продажа' AS op, true AS posted, amount AS total
            FROM mart.cheki_sales_net
        """)
        con.execute("""
            CREATE TABLE mart.cheki_lines AS
            SELECT 'DEMO-CHECK-' || point_key || '-' || trade_day::VARCHAR || '-'
                   || sku_key || '-' || extract(hour FROM ts)::INT::VARCHAR AS chek_key,
                   1 AS line_no, sku_key, qty, price, amount, disc_manual,
                   disc_auto, with_writeoff FROM mart.cheki_sales_net
        """)
        con.execute("""
            CREATE TABLE mart.fact_regime_daily AS
            SELECT d trade_date, point_key, sku_key, sold::DOUBLE sold,
                   markdown::DOUBLE u40, markdown::DOUBLE u40_strict,
                   (sold+wo+markdown)::DOUBLE delivered, wo::DOUBLE writeoff,
                   true has_supply,
                   CASE WHEN jitter=0 AND i<=6 THEN 'A'
                        WHEN markdown>0 THEN 'C' ELSE 'D' END regime
            FROM scenario
        """)
        con.execute("""
            CREATE TABLE mart.transfer_order_lines AS
            SELECT 'DEMO-ORDER-' || point_key || '-' || d::VARCHAR doc_key,
                   d doc_date, true posted, false deleted, d move_date,
                   point_key, 'DEMO-HUB' source_key, 'DEMO synthetic order' AS "comment",
                   sku_key, (demand+wo)::DOUBLE qty_ordered,
                   (demand+wo)::DOUBLE qty_collected, 'шт' unit_key,
                   '' line_comment FROM scenario
        """)
        con.execute("ANALYZE")
        counts = con.execute("SELECT (SELECT count(*) FROM mart.fact_sales_daily), (SELECT count(*) FROM mart.cheki_sales_net)").fetchone()
        assert counts[0] > 10000 and counts[1] > counts[0]
        print(f"DEMO database: {target}, {counts[0]} point/SKU/day facts, {counts[1]} check lines")
    finally:
        con.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("target", type=Path)
    parser.add_argument("--as-of", type=date.fromisoformat,
                        help="issue date (YYYY-MM-DD; default: today in Etc/GMT-10)")
    args = parser.parse_args()
    if not args.target.is_absolute():
        parser.error("target must be an absolute path")
    generate(args.target, args.as_of or datetime.now(ZoneInfo('Etc/GMT-10')).date())
