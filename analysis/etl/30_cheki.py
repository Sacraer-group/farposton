# -*- coding: utf-8 -*-
"""Витрина чеков маркетов: время каждой продажи из r_keeper (через 1С).

Источники (odata/data/, вне git):
  шапки:  Document_УОП_ДанныеПродажФронта.jsonl (год 01.08.2025–31.07.2026)
        + Document_УОП_ДанныеПродажФронта_август.jsonl (дни пилота, добор ежедневный)
  строки: Document_УОП_ДанныеПродажФронта_Запасы.jsonl (год, 10 полей)
        + _чеки_строки_часть1_окт-ноя.jsonl (кусок первой выгрузки, тот же формат)
        + Document_УОП_ДанныеПродажФронта_Запасы_свежие.jsonl (пилот, навигация, все поля)

Ключевые решения:
  · торговый день = date(СменаНачало): чек в 00:10 после полуночи принадлежит
    вчерашней смене (проверено на живом чеке 30.11→01.12);
  · в витрину входят только posted-чеки с ВидОперации='Продажа' — отмены (≈5%)
    и возвраты отсеиваются, но лежат в cheki_headers для сверки;
  · точка чека: Подразделение_Key → его склад-потомок из справочника структурных
    единиц (у каждого подразделения маркета ровно один склад, проверено 04.08).

Запуск: PYTHONUTF8=1 py prognoz/analysis/etl/30_cheki.py
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _db import DATA, connect_writer

SRC_HEAD = [DATA / "Document_УОП_ДанныеПродажФронта.jsonl",
            DATA / "Document_УОП_ДанныеПродажФронта_август.jsonl"]
SRC_LINE = [DATA / "Document_УОП_ДанныеПродажФронта_Запасы.jsonl",
            DATA / "_чеки_строки_часть1_окт-ноя.jsonl",
            DATA / "Document_УОП_ДанныеПродажФронта_Запасы_свежие.jsonl"]
UNITS = DATA / "Catalog_СтруктурныеЕдиницы.jsonl"


def unit_to_store() -> dict:
    """Подразделение → склад-потомок (point_key нашей витрины)."""
    rows = [json.loads(l) for l in UNITS.open(encoding="utf-8")]
    return {r["Parent_Key"]: r["Ref_Key"] for r in rows
            if r.get("ТипСтруктурнойЕдиницы") == "Склад"
            and r.get("Parent_Key", "0" * 8) != "00000000-0000-0000-0000-000000000000"}


def main() -> int:
    con = connect_writer()
    heads = [p.as_posix() for p in SRC_HEAD if p.exists()]
    lines = [p.as_posix() for p in SRC_LINE if p.exists()]
    print(f"шапки: {len(heads)} файлов, строки: {len(lines)} файлов")

    m = unit_to_store()
    con.execute("CREATE OR REPLACE TABLE _unit2store (unit VARCHAR, store VARCHAR)")
    con.executemany("INSERT INTO _unit2store VALUES (?, ?)", list(m.items()))

    con.execute(f"""
        CREATE OR REPLACE TABLE mart.cheki_headers AS
        SELECT DISTINCT ON (CAST(Ref_Key AS VARCHAR))
               CAST(Ref_Key AS VARCHAR)                    AS chek_key,
               Date::TIMESTAMP                             AS ts,
               coalesce(CAST(СменаНачало AS TIMESTAMP),
                        Date::TIMESTAMP - INTERVAL 4 HOUR)::DATE AS trade_day,
               u.store                                     AS point_key,
               CAST(Подразделение_Key AS VARCHAR)          AS unit_key,
               ВидОперации                                 AS op,
               Posted                                      AS posted,
               СуммаДокумента                              AS total
        FROM read_json_auto({heads}, ignore_errors=true, union_by_name=true) h
        LEFT JOIN _unit2store u ON u.unit = CAST(h.Подразделение_Key AS VARCHAR)
    """)
    n_h = con.execute("SELECT count(*) FROM mart.cheki_headers").fetchone()[0]

    con.execute(f"""
        CREATE OR REPLACE TABLE mart.cheki_lines AS
        SELECT DISTINCT ON (CAST(Ref_Key AS VARCHAR), CAST(LineNumber AS BIGINT))
               CAST(Ref_Key AS VARCHAR)           AS chek_key,
               CAST(LineNumber AS BIGINT)         AS line_no,
               CAST(Номенклатура_Key AS VARCHAR)  AS sku_key,
               CAST(Количество AS DOUBLE)         AS qty,
               CAST(Цена AS DOUBLE)               AS price,
               CAST(Сумма AS DOUBLE)              AS amount,
               CAST(СуммаСкидкиНаценки AS DOUBLE) AS disc_manual,
               CAST(СуммаАвтоматическойСкидки AS DOUBLE) AS disc_auto,
               CAST(СоСписанием AS BOOLEAN)       AS with_writeoff
        FROM read_json_auto({lines}, ignore_errors=true, union_by_name=true)
    """)
    n_l = con.execute("SELECT count(*) FROM mart.cheki_lines").fetchone()[0]

    # рабочее представление: продажи маркетов, отмены/возвраты отсеяны
    con.execute("""
        CREATE OR REPLACE VIEW mart.cheki_sales AS
        SELECT h.point_key, h.trade_day, h.ts, l.sku_key, l.qty, l.price,
               l.amount, l.disc_manual, l.disc_auto, l.with_writeoff
        FROM mart.cheki_lines l
        JOIN mart.cheki_headers h ON h.chek_key = l.chek_key
        WHERE h.posted AND h.op = 'Продажа' AND h.point_key IS NOT NULL
    """)

    # Возвраты (op='Возврат', ~1,4 тыс чеков/год): пары «пробили — вернули».
    # cheki_sales их не видит, и чеки получаются «жирнее» витрины продаж — клиент,
    # сверив наш пример по своим чекам, увидел бы 8 шт против наших 4 (находка 4
    # панели проверки полки). Отмены (op='Отмена') сюда НЕ входят: отменённый чек
    # не попадает в продажи вовсе, вычитать ему нечего.
    con.execute("""
        CREATE OR REPLACE VIEW mart.cheki_refunds AS
        SELECT h.point_key, h.trade_day, h.ts, l.sku_key, l.qty, l.price,
               l.amount, l.disc_manual, l.disc_auto, l.with_writeoff
        FROM mart.cheki_lines l
        JOIN mart.cheki_headers h ON h.chek_key = l.chek_key
        WHERE h.posted AND h.op = 'Возврат' AND h.point_key IS NOT NULL
    """)

    # Продажи-нетто: платные строки продаж плюс возвраты со знаком минус на времени
    # возвратного чека. Суммы qty/amount по дню выходят за вычетом возвратов — как
    # qty_sold − qty_returned в fact_sales_daily; время продаж (t_last и пр.)
    # потребитель берёт только по строкам sign > 0.
    con.execute("""
        CREATE OR REPLACE VIEW mart.cheki_sales_net AS
        SELECT point_key, trade_day, ts, sku_key, qty, price, amount,
               disc_manual, disc_auto, with_writeoff, 1 AS sign
        FROM mart.cheki_sales WHERE amount > 0
        UNION ALL
        SELECT point_key, trade_day, ts, sku_key, -qty, price, -amount,
               -disc_manual, -disc_auto, with_writeoff, -1
        FROM mart.cheki_refunds WHERE amount > 0
    """)
    con.execute("DROP TABLE _unit2store")

    print(f"mart.cheki_headers: {n_h:,}")
    print(f"mart.cheki_lines:   {n_l:,}")
    stat = con.execute("""
        SELECT min(trade_day), max(trade_day), count(*), round(sum(qty))
        FROM mart.cheki_sales
    """).fetchone()
    print(f"mart.cheki_sales:  {stat[2]:,} строк, {stat[3]:,.0f} шт, "
          f"торговые дни {stat[0]} … {stat[1]}")
    ref = con.execute(
        "SELECT count(*), round(coalesce(sum(qty), 0)) FROM mart.cheki_refunds"
    ).fetchone()
    print(f"mart.cheki_refunds: {ref[0]:,} строк, {ref[1]:,.0f} шт (вычитаются в _net)")
    con.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
