"""Read-only aggregate acceptance for the requested completed trading day."""
import argparse
import json

import duckdb


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--database', required=True)
    parser.add_argument('--day', required=True)
    args = parser.parse_args()
    with duckdb.connect(args.database, read_only=True) as con:
        rows = con.execute("""
            WITH points AS (
                SELECT point_key, canon_name FROM mart.dim_point
                WHERE role IN ('market', 'market_hub')
            ), sales AS (
                SELECT point_key, max(sale_date) last_sale,
                    count(*) FILTER (WHERE sale_date = CAST(? AS DATE)) sale_rows
                FROM mart.fact_sales_daily GROUP BY point_key
            ), receipts AS (
                SELECT h.point_key, count(*) headers, count(DISTINCT h.chek_key) unique_headers,
                    count(*) FILTER (WHERE h.op = 'Продажа' AND h.posted) posted_sales,
                    count(*) FILTER (WHERE h.op = 'Продажа' AND h.posted
                        AND NOT EXISTS (SELECT 1 FROM mart.cheki_lines l WHERE l.chek_key=h.chek_key)) missing_sale_lines
                FROM mart.cheki_headers h WHERE trade_day=CAST(? AS DATE)
                GROUP BY h.point_key
            )
            SELECT p.canon_name, s.last_sale, coalesce(s.sale_rows,0),
                coalesce(r.headers,0), coalesce(r.unique_headers,0),
                coalesce(r.posted_sales,0), coalesce(r.missing_sale_lines,0)
            FROM points p LEFT JOIN sales s USING(point_key)
            LEFT JOIN receipts r USING(point_key) ORDER BY p.canon_name
        """, [args.day, args.day]).fetchall()
    names = ('point', 'last_sale', 'sale_rows', 'headers', 'unique_headers', 'posted_sales', 'missing_sale_lines')
    results = [dict(zip(names, row)) for row in rows]
    passed = len(results) == 6 and all(
        row['sale_rows'] > 0 and row['posted_sales'] > 0
        and row['headers'] == row['unique_headers'] and row['missing_sale_lines'] == 0
        for row in results
    )
    print(json.dumps({'day': args.day, 'passed': passed, 'points': results}, ensure_ascii=True, default=str))
    return 0 if passed else 2


if __name__ == '__main__':
    raise SystemExit(main())
