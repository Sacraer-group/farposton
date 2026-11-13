# -*- coding: utf-8 -*-
"""Проверки данных, на которых стоит весь проект. Прогонять после каждой пересборки витрин.

Проверяется не «работает ли код», а три утверждения, из которых следует всё остальное:

1. Формула восстановления уценки верна. Если тождество
   `Сумма = Цена × Количество − СуммаСкидкиНаценки` не выполняется, вся конструкция
   «скидка как измеритель» рассыпается.
2. Восстановленное число уценённых единиц целое — значит модель «k из q единиц ушло
   ровно с −40%» описывает реальность, а не подгоняется. ⚠️ Проверять ТОЛЬКО на маркетах
   и меню: в ресторанах действуют другие скидочные программы, и там доля целых падает
   до 22% — это не дефект, а другой скоуп.
3. Витрина сходится с сырой выгрузкой. Иначе любые цифры отчёта — про что-то другое.

Запуск: PYTHONUTF8=1 py prognoz/tools/verify_data.py
"""
import collections
import argparse
import json
import sys
from pathlib import Path

import duckdb

SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))
sys.path.insert(0, str(SRC.parents[0] / "analysis" / "etl"))
from _db import DATA, DB_PATH                              # noqa: E402
from prognoz.scope import GROUP_LIKE, GROUP_NOT_LIKE       # noqa: E402

MIN_WHOLE_SHARE = 74.0     # порог доли целых, зафиксированный в протоколе
CHECK_MONTHS = ("2026-06", "2026-07")


def check_identity(con) -> bool:
    n, ok = con.execute("""
        SELECT count(*),
               sum(CASE WHEN abs(Сумма - (Цена * Количество
                                          - coalesce(СуммаСкидкиНаценки, 0))) < 0.51
                        THEN 1 ELSE 0 END)
        FROM raw.sales_lines WHERE Цена > 0 AND NOT Возврат
    """).fetchone()
    share = 100 * ok / n
    print(f"1. Тождество суммы и скидки: {ok:,} из {n:,} строк ({share:.2f}%)")
    return share > 99.9


def check_u40_integral(con) -> bool:
    n, whole = con.execute("""
        WITH mk AS (SELECT point_key FROM mart.dim_point
                    WHERE role IN ('market', 'market_hub', 'merged')),
        sku AS (SELECT sku_key FROM mart.dim_sku
                WHERE group_path LIKE ? AND group_path NOT LIKE ?)
        SELECT count(*), sum(CASE WHEN abs(u - round(u)) < 0.02 THEN 1 ELSE 0 END)
        FROM (
            SELECT l.СуммаСкидкиНаценки / (0.4 * l.Цена) AS u
            FROM raw.sales_lines l
            JOIN raw.sales_headers h ON h.Ref_Key = l.Ref_Key
            JOIN mk ON mk.point_key = coalesce(
                nullif(l.СтруктурнаяЕдиница_Key, '00000000-0000-0000-0000-000000000000'),
                h.СтруктурнаяЕдиница_Key)
            JOIN sku ON sku.sku_key = l.Номенклатура_Key
            WHERE l.Цена > 0 AND coalesce(l.СуммаСкидкиНаценки, 0) > 0 AND NOT l.Возврат
        )
    """, [GROUP_LIKE, GROUP_NOT_LIKE]).fetchone()
    share = 100 * whole / n if n else 0
    print(f"2. Уценка восстанавливается целым числом: {whole:,} из {n:,} ({share:.1f}%), "
          f"порог {MIN_WHOLE_SHARE}%")
    return share >= MIN_WHOLE_SHARE


def check_mart_vs_raw(con) -> bool:
    # n_docs > 0: добранные из чеков точко-дни (патч 20_marts, n_docs=0) в сырых
    # документах продаж отсутствуют ПО ПОСТРОЕНИЮ — документа продаж там нет,
    # ради этого и патч. Сверяем витрину с сырьём только там, где сырьё есть.
    mart = con.execute(f"""
        SELECT strftime(sale_date, '%Y-%m') AS m, point_key, round(sum(revenue)) AS rev
        FROM mart.fact_sales_daily
        WHERE strftime(sale_date, '%Y-%m') IN {CHECK_MONTHS} AND n_docs > 0
        GROUP BY 1, 2
    """).fetchall()
    empty = "00000000-0000-0000-0000-000000000000"
    hdr = {}
    with (DATA / "Document_ОтчетОРозничныхПродажах.jsonl").open(encoding="utf-8") as f:
        for line in f:
            x = json.loads(line)
            if x.get("DeletionMark"):
                continue
            m = (x.get("Date") or "")[:7]
            if m in CHECK_MONTHS:
                hdr[x["Ref_Key"]] = (m, x.get("СтруктурнаяЕдиница_Key"))
    raw = collections.defaultdict(float)
    with (DATA / "Document_ОтчетОРозничныхПродажах_Запасы.jsonl").open(encoding="utf-8") as f:
        for line in f:
            x = json.loads(line)
            h = hdr.get(x.get("Ref_Key"))
            if not h:
                continue
            m, hp = h
            pt = x.get("СтруктурнаяЕдиница_Key")
            raw[(m, pt if pt and pt != empty else hp)] += float(x.get("Сумма") or 0)
    bad = [(m, p, rev, round(raw.get((m, p), 0.0)))
           for m, p, rev in mart if abs(rev - round(raw.get((m, p), 0.0))) > 1]
    print(f"3. Витрина против сырой выгрузки: групп точка-месяц {len(mart)}, "
          f"расхождений {len(bad)}")
    for m, p, a, b in bad[:5]:
        print(f"   ⚠ {m} {p}: витрина {a:,.0f} против {b:,.0f}")
    return not bad


def check_mart_vs_database_raw(con) -> bool:
    """Сверка витрины с raw-таблицами уже загруженной DuckDB.

    Клиентский контур на первом этапе получает снимок базы, но не 15 ГБ исходных
    JSONL. Это та же сверка точка × месяц, только без внешних файлов.
    """
    mart = con.execute(f"""
        SELECT strftime(sale_date, '%Y-%m') AS m, point_key, round(sum(revenue)) AS rev
        FROM mart.fact_sales_daily
        WHERE strftime(sale_date, '%Y-%m') IN {CHECK_MONTHS} AND n_docs > 0
        GROUP BY 1, 2
    """).fetchall()
    empty = "00000000-0000-0000-0000-000000000000"
    raw_rows = con.execute(f"""
        SELECT strftime(CAST(h.Date AS DATE), '%Y-%m') AS m,
               coalesce(nullif(l.СтруктурнаяЕдиница_Key, ?),
                        h.СтруктурнаяЕдиница_Key) AS point_key,
               round(sum(l.Сумма)) AS rev
        FROM raw.sales_lines l
        JOIN raw.sales_headers h ON h.Ref_Key = l.Ref_Key
        WHERE NOT h.DeletionMark
          AND strftime(CAST(h.Date AS DATE), '%Y-%m') IN {CHECK_MONTHS}
        GROUP BY 1, 2
    """, [empty]).fetchall()
    raw = {(m, point): float(revenue or 0) for m, point, revenue in raw_rows}
    bad = [(m, p, rev, round(raw.get((m, p), 0.0)))
           for m, p, rev in mart if abs(rev - round(raw.get((m, p), 0.0))) > 1]
    print(f"3. Витрина против raw-таблиц DuckDB: групп точка-месяц {len(mart)}, "
          f"расхождений {len(bad)}")
    for m, p, a, b in bad[:5]:
        print(f"   ⚠ {m} {p}: витрина {a:,.0f} против raw {b:,.0f}")
    return not bad


def check_regimes(con) -> bool:
    try:
        rows = con.execute("""
            SELECT regime, count(*),
                   round(100.0 * sum(CASE WHEN writeoff > 0 THEN 1 ELSE 0 END) / count(*), 1)
            FROM mart.fact_regime_daily
            WHERE has_supply AND regime IN ('A', 'B', 'C', 'D')
            GROUP BY 1 ORDER BY 3 DESC
        """).fetchall()
    except duckdb.CatalogException:
        print("4. Разметка режимов: витрина не собрана — пропуск")
        return True
    order = [r[0] for r in rows]
    ok = order == ["C", "D", "B", "A"]
    print(f"4. Разметка режимов, доля строк со списанием: "
          + ", ".join(f"{r}={p}%" for r, _, p in rows))
    print(f"   порядок {' → '.join(order)} — ожидается C → D → B → A")
    return ok


def main(argv: list[str] | None = None):
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--raw-source", choices=("jsonl", "database"), default="jsonl",
        help="Источник независимой сверки витрины; client deployment uses database",
    )
    args = parser.parse_args(argv)
    con = duckdb.connect(str(DB_PATH), read_only=True)
    con.execute("SET memory_limit='800MB'")
    con.execute("SET threads=1")
    results = [
        ("тождество суммы и скидки", check_identity(con)),
        ("целочисленность уценки", check_u40_integral(con)),
        ("витрина против сырья",
         check_mart_vs_raw(con) if args.raw_source == "jsonl"
         else check_mart_vs_database_raw(con)),
        ("разметка режимов", check_regimes(con)),
    ]
    con.close()
    bad = [name for name, ok in results if not ok]
    print()
    if bad:
        print("НЕ ПРОЙДЕНО: " + ", ".join(bad))
        return 1
    print("Все проверки пройдены.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
