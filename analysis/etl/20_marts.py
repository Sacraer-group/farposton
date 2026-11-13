# -*- coding: utf-8 -*-
"""Витрины прогноза: raw.* → mart.*.

mart.transfer_order_lines — распакованная ТЧ ЗаказНаПеремещение (Запасы JSON → строки)
mart.dim_sku            — номенклатура с путём группы и категорией
mart.dim_point          — точки/склады + разметка из config/points_map.csv (если есть)
mart.fact_sales_daily   — продано: день × точка × SKU (qty, returns, revenue, all_posted)
mart.fact_orders_daily  — заказано: день × точка × SKU, склейка двух каналов
                          ('transfer' с 03.2026 — с фактом сборки; 'zakaz' — старый)

Дата «заказано» — день, НА который везут (ДатаПеремещения / ДатаПоступления),
а не день создания документа: сопоставляется с продажами того же дня.
Шаблоны-болванки (Комментарий ~ 'шаблон') отфильтровываются.

Запуск: PYTHONUTF8=1 py prognoz/analysis/etl/20_marts.py
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _db import CONFIG, connect_writer

EMPTY_GUID = "00000000-0000-0000-0000-000000000000"

STEPS = []


def step(name):
    def wrap(fn):
        STEPS.append((name, fn))
        return fn
    return wrap


@step("transfer_order_lines")
def transfer_order_lines(con):
    """Разворот табличной части заявок из JSON.

    ⚠️ Порциями намеренно: json_each() разворачивает массив в памяти и не умеет
    спиллить на диск. Одним запросом на всей таблице оно съедало 763 МБ при 1 392
    документах и падало на лимите ночного прогона (800 МБ), а документы копятся
    каждый день. Порция в 250 документов держит расход в десятках мегабайт
    независимо от того, сколько их накопится за год.
    """
    body = """
        SELECT
            t.Ref_Key                              AS doc_key,
            t.Number                               AS doc_number,
            t.Date                                 AS doc_date,
            t.Posted                               AS posted,
            t.DeletionMark                         AS deleted,
            t.ДатаПеремещения                      AS move_date,
            t.СтруктурнаяЕдиницаПолучатель_Key     AS point_key,
            t.СтруктурнаяЕдиницаРезерв_Key         AS source_key,
            t.Комментарий                          AS comment,
            json_extract_string(je.value, '$.Номенклатура_Key')            AS sku_key,
            CAST(json_extract(je.value, '$.Количество') AS DOUBLE)         AS qty_ordered,
            CAST(json_extract(je.value, '$.КоличествоСобрано') AS DOUBLE)  AS qty_collected,
            json_extract_string(je.value, '$.ЕдиницаИзмерения')            AS unit_key,
            json_extract_string(je.value, '$.УОП_Комментарий')             AS line_comment
        FROM ({src}) t, json_each(t.Запасы) je
    """
    con.execute("CREATE OR REPLACE TABLE mart.transfer_order_lines AS "
                + body.format(src="SELECT * FROM raw.transfer_orders LIMIT 0"))
    total = con.execute("SELECT count(*) FROM raw.transfer_orders").fetchone()[0]
    batch = 250
    for offset in range(0, total, batch):
        con.execute(
            "INSERT INTO mart.transfer_order_lines " + body.format(
                src=f"SELECT * FROM raw.transfer_orders ORDER BY Ref_Key "
                    f"LIMIT {batch} OFFSET {offset}"))


@step("dim_sku")
def dim_sku(con):
    con.execute("""
        CREATE OR REPLACE TABLE mart.dim_sku AS
        WITH RECURSIVE chain AS (
            SELECT Ref_Key AS sku_key, Ref_Key AS node, Parent_Key AS parent,
                   CAST('' AS VARCHAR) AS path, 0 AS depth
            FROM raw.nomenclature WHERE NOT IsFolder
            UNION ALL
            SELECT c.sku_key, g.Ref_Key, g.Parent_Key,
                   g.Description || CASE WHEN c.path = '' THEN '' ELSE ' / ' END || c.path,
                   c.depth + 1
            FROM chain c
            JOIN raw.nomenclature g ON g.Ref_Key = c.parent AND g.IsFolder
            WHERE c.depth < 8
        ),
        paths AS (
            SELECT sku_key, path,
                   row_number() OVER (PARTITION BY sku_key ORDER BY depth DESC) AS rn
            FROM chain
        )
        SELECT
            n.Ref_Key            AS sku_key,
            n.Code               AS code,
            n.Description        AS name,
            n.НаименованиеПолное AS full_name,
            p.path               AS group_path,
            c.Description        AS category,
            n.Весовой            AS is_weight,
            n.УОП_ДробноеКоличество      AS allows_fraction,
            n.ЗапретитьВводДробногоКоличества AS forbids_fraction,
            n.УОП_ЭтоНапиток     AS is_drink,
            n.ТипНоменклатуры    AS sku_type,
            n.DeletionMark       AS deleted,
            n.Недействителен     AS inactive
        FROM raw.nomenclature n
        LEFT JOIN (SELECT sku_key, path FROM paths WHERE rn = 1) p ON p.sku_key = n.Ref_Key
        LEFT JOIN raw.categories c ON c.Ref_Key = n.КатегорияНоменклатуры_Key
        WHERE NOT n.IsFolder
    """)


@step("dim_point")
def dim_point(con):
    cfg = CONFIG / "points_map.csv"
    if cfg.exists():
        con.execute(f"""
            CREATE OR REPLACE TEMP TABLE _points_cfg AS
            SELECT * FROM read_csv('{cfg.as_posix()}', header=true, all_varchar=true)
        """)
    else:
        con.execute("""
            CREATE OR REPLACE TEMP TABLE _points_cfg (
                point_key VARCHAR, canon_name VARCHAR, role VARCHAR, merged_into VARCHAR
            )
        """)
    con.execute("""
        CREATE OR REPLACE TABLE mart.dim_point AS
        SELECT
            p.Ref_Key            AS point_key,
            p.Code               AS code,
            p.Description        AS name,
            p.ТипСтруктурнойЕдиницы AS unit_type,
            o.Description        AS org_name,
            p.Недействителен     AS inactive,
            p.DeletionMark       AS deleted,
            cfg.canon_name,
            cfg.role,                          -- market / restaurant / factory / hub / other
            coalesce(cfg.merged_into, '')     AS merged_into
        FROM raw.points p
        LEFT JOIN raw.orgs o ON o.Ref_Key = p.Организация_Key
        LEFT JOIN _points_cfg cfg ON cfg.point_key = p.Ref_Key
    """)


@step("fact_sales_daily")
def fact_sales_daily(con):
    """Продано за день + восстановление вечерней уценки −40%.

    Клиент подтвердил (01.08.2026): каждый день на каждой точке стартует распродажа
    −40% на готовую еду. Строка 1С — агрегат за сутки по (документ × SKU), поэтому
    ПроцентСкидкиНаценки средневзвешенный: 20% = 40/2, 13,33% = 40/3, 6,67% = 40/6.

    Две оценки числа уценённых единиц, потому что параллельно работает программа −10%
    и различить их построчно нельзя (ВидСкидкиНаценки_Key всегда пустой):
      u40        — верхняя: считаем, что вся скидка это −40%;
      u40_strict — нижняя: считаем, что все остальные единицы получили −10%,
                   и смотрим, сколько единиц −40% нужно, чтобы объяснить остаток суммы.
    В отчётах давать интервалом, не точкой.
    """
    con.execute(f"""
        CREATE OR REPLACE TABLE mart.fact_sales_daily AS
        SELECT
            CAST(h.Date AS DATE)  AS sale_date,
            CASE WHEN l.СтруктурнаяЕдиница_Key IS NOT NULL
                  AND l.СтруктурнаяЕдиница_Key <> '{EMPTY_GUID}'
                 THEN l.СтруктурнаяЕдиница_Key
                 ELSE h.СтруктурнаяЕдиница_Key END AS point_key,
            l.Номенклатура_Key    AS sku_key,
            SUM(l.Количество)     AS qty_sold,
            SUM(l.Возврат)        AS qty_returned,
            SUM(l.Сумма)          AS revenue,
            SUM(l.Цена * l.Количество)   AS revenue_gross,
            SUM(coalesce(l.СуммаСкидкиНаценки, 0)) AS disc_sum,
            SUM(CASE WHEN l.Цена > 0 AND coalesce(l.СуммаСкидкиНаценки, 0) > 0
                     THEN least(l.Количество, l.СуммаСкидкиНаценки / (0.4 * l.Цена))
                     ELSE 0 END)         AS u40,
            SUM(CASE WHEN l.Цена > 0 AND coalesce(l.СуммаСкидкиНаценки, 0) > 0
                     THEN greatest(0.0, least(l.Количество,
                          (l.СуммаСкидкиНаценки - 0.1 * l.Цена * l.Количество)
                          / (0.3 * l.Цена)))
                     ELSE 0 END)         AS u40_strict,
            COUNT(DISTINCT h.Ref_Key) AS n_docs,
            bool_and(h.Posted)    AS all_posted
        FROM raw.sales_lines l
        JOIN raw.sales_headers h ON h.Ref_Key = l.Ref_Key
        WHERE NOT h.DeletionMark
        GROUP BY 1, 2, 3
    """)

    # Добор точко-дней, где документ продаж так и не создан, а чеки есть (за год
    # таких 5: Точки 4 03.10, Точка 3 26.02, Точка 2 23.07, Точки 5 25–26.07 — всего
    # ~1,3 тыс шт). Метки добранных строк: n_docs=0, all_posted=false — документа
    # действительно нет, данные из чеков. NOT EXISTS по канонической точке:
    # если 1С задним числом создаст документ, патч сам исчезнет при пересборке.
    have_cheki = con.execute("""
        SELECT count(*) FROM information_schema.tables
        WHERE table_schema = 'mart' AND table_name = 'cheki_headers'
    """).fetchone()[0]
    if have_cheki:
        con.execute("""
            INSERT INTO mart.fact_sales_daily
            SELECT cs.trade_day AS sale_date, cs.point_key, cs.sku_key,
                   SUM(cs.qty) AS qty_sold, 0.0 AS qty_returned,
                   SUM(cs.amount) AS revenue,
                   SUM(cs.price * cs.qty) AS revenue_gross,
                   SUM(coalesce(cs.disc_manual, 0)) AS disc_sum,
                   SUM(CASE WHEN cs.price > 0 AND coalesce(cs.disc_manual, 0) > 0
                            THEN least(cs.qty, cs.disc_manual / (0.4 * cs.price))
                            ELSE 0 END) AS u40,
                   SUM(CASE WHEN cs.price > 0 AND coalesce(cs.disc_manual, 0) > 0
                            THEN greatest(0.0, least(cs.qty,
                                 (cs.disc_manual - 0.1 * cs.price * cs.qty)
                                 / (0.3 * cs.price)))
                            ELSE 0 END) AS u40_strict,
                   0 AS n_docs, false AS all_posted
            FROM mart.cheki_sales cs
            JOIN mart.dim_point pc ON pc.point_key = cs.point_key
            WHERE cs.amount > 0
              AND pc.role IN ('market', 'market_hub', 'merged')
              AND NOT EXISTS (
                  SELECT 1 FROM mart.fact_sales_daily f
                  JOIN mart.dim_point p1 ON p1.point_key = f.point_key
                  WHERE coalesce(nullif(p1.merged_into, ''), f.point_key)
                        = coalesce(nullif(pc.merged_into, ''), cs.point_key)
                    AND f.sale_date = cs.trade_day)
            GROUP BY 1, 2, 3
        """)
        patched = con.execute("""
            SELECT count(DISTINCT (point_key, sale_date)), count(*)
            FROM mart.fact_sales_daily WHERE n_docs = 0
        """).fetchone()
        if patched[0]:
            print(f"  добрано из чеков: {patched[0]} точко-дней без документа "
                  f"продаж ({patched[1]} строк)")


@step("fact_orders_daily")
def fact_orders_daily(con):
    con.execute("""
        CREATE OR REPLACE TABLE mart.fact_orders_daily AS
        WITH transfer AS (
            SELECT
                CAST(move_date AS DATE)   AS trade_date,
                point_key,
                sku_key,
                SUM(qty_ordered)          AS qty_ordered,
                SUM(qty_collected)        AS qty_collected,
                'transfer'                AS channel,
                COUNT(DISTINCT doc_key)   AS n_docs,
                -- 17.09: проведение документов — для отчёта о документной дисциплине
                BOOL_AND(posted)          AS all_posted,
                COUNT(DISTINCT doc_key) FILTER (WHERE NOT posted) AS n_unposted
            FROM mart.transfer_order_lines
            WHERE NOT deleted
              AND NOT regexp_matches(lower(coalesce(comment, '')), 'шаблон')
            GROUP BY 1, 2, 3
        ),
        zakaz AS (
            SELECT
                CAST(l.ДатаПоступления AS DATE) AS trade_date,
                h.СтруктурнаяЕдиница_Key  AS point_key,
                l.Номенклатура_Key        AS sku_key,
                SUM(l.Количество)         AS qty_ordered,
                CAST(NULL AS DOUBLE)      AS qty_collected,
                'zakaz'                   AS channel,
                COUNT(DISTINCT h.Ref_Key) AS n_docs,
                BOOL_AND(h.Posted)        AS all_posted,
                COUNT(DISTINCT h.Ref_Key) FILTER (WHERE NOT h.Posted) AS n_unposted
            FROM raw.zakaz_lines l
            JOIN raw.zakaz_headers h ON h.Ref_Key = l.Ref_Key
            WHERE NOT h.DeletionMark
            GROUP BY 1, 2, 3
        )
        SELECT * FROM transfer
        UNION ALL
        SELECT * FROM zakaz
    """)


@step("fact_supply_daily")
def fact_supply_daily(con):
    """Факт привоза на точку: день × точка × SKU, два канала.

    Сеть перестроила учёт в марте 2026: до марта точки были отдельными юрлицами и товар
    шёл «Передачей между организациями», с марта — внутренним «Перемещением запасов».
    Склейка по точке: с первого перемещения берём move, раньше — supply.
    """
    con.execute("""
        CREATE OR REPLACE TABLE mart.fact_supply_daily AS
        WITH mv AS (
            SELECT CAST(h.Date AS DATE) AS supply_date,
                   h.СтруктурнаяЕдиницаПолучатель_Key AS point_key,
                   l.Номенклатура_Key AS sku_key,
                   SUM(l.Количество) AS qty, 'move' AS channel,
                   BOOL_AND(h.Posted) AS all_posted,
                   COUNT(DISTINCT h.Ref_Key) FILTER (WHERE NOT h.Posted) AS n_unposted
            FROM raw.move_lines l JOIN raw.move_headers h ON h.Ref_Key = l.Ref_Key
            WHERE NOT h.DeletionMark
            GROUP BY 1, 2, 3
        ),
        sp AS (
            SELECT CAST(h.Date AS DATE) AS supply_date,
                   h.УОП_СтруктурнаяЕдиницаПриход_Key AS point_key,
                   l.Номенклатура_Key AS sku_key,
                   SUM(l.Количество) AS qty, 'supply' AS channel,
                   BOOL_AND(h.Posted) AS all_posted,
                   COUNT(DISTINCT h.Ref_Key) FILTER (WHERE NOT h.Posted) AS n_unposted
            FROM raw.supply_lines l JOIN raw.supply_headers h ON h.Ref_Key = l.Ref_Key
            WHERE NOT h.DeletionMark
            GROUP BY 1, 2, 3
        ),
        t0 AS (SELECT point_key, min(supply_date) AS d0 FROM mv GROUP BY 1)
        SELECT * FROM mv
        UNION ALL
        SELECT sp.* FROM sp LEFT JOIN t0 USING (point_key)
        WHERE t0.d0 IS NULL OR sp.supply_date < t0.d0
    """)


@step("fact_writeoff_daily")
def fact_writeoff_daily(con):
    """Списания: день × точка × SKU. Внимание — 94% строк по маркетам это расходники
    (салфетки, упаковка); готовую продукцию отбирать фильтром группы «ДЕМО кухня».
    Внутри также сидит питание сотрудников — прогнозом оно не сокращается."""
    con.execute("""
        CREATE OR REPLACE TABLE mart.fact_writeoff_daily AS
        SELECT CAST(h.Date AS DATE) AS wo_date,
               h.СтруктурнаяЕдиница_Key AS point_key,
               l.Номенклатура_Key AS sku_key,
               SUM(l.Количество) AS qty_writeoff,
               SUM(coalesce(l.Сумма, 0)) AS wo_sum,
               COUNT(DISTINCT h.Ref_Key) AS n_docs,
               BOOL_AND(h.Posted) AS all_posted,
               COUNT(DISTINCT h.Ref_Key) FILTER (WHERE NOT h.Posted) AS n_unposted
        FROM raw.writeoff_lines l JOIN raw.writeoff_headers h ON h.Ref_Key = l.Ref_Key
        WHERE NOT h.DeletionMark
        GROUP BY 1, 2, 3
    """)


@step("fact_regime_daily")
def fact_regime_daily(con):
    """Режим наблюдения спроса по каждой паре точка×SKU×день — ключевая витрина проекта.

    Логика: если товар дожил до вечерней распродажи (были уценённые единицы) или что-то
    осталось и было списано — значит спроса по полной цене хватило на весь привоз,
    и он наблюдаем точно. Настоящий дефицит — только когда распродали ВСЁ и при этом
    ни одна единица не ушла со скидкой и ничего не списали.

      A — распродано без уценки и без списания  → истинный дефицит (спрос цензурирован)
      B — распродано, но часть ушла по уценке   → спрос по полной цене наблюдаем
      C — остался товар, часть по уценке        → наблюдаем полностью
      D — остался товар, уценки не было         → наблюдаем полностью

    Проверка классификации независимым документом: доля строк со списанием должна падать
    в порядке C → D → B → A (у остатка списаний много, у дефицита почти нет).
    """
    con.execute("""
        CREATE OR REPLACE TABLE mart.fact_regime_daily AS
        WITH canon AS (
            SELECT point_key, coalesce(nullif(merged_into, ''), point_key) AS pt
            FROM mart.dim_point
            WHERE role IN ('market', 'market_hub', 'merged')
        ),
        s AS (
            SELECT c.pt, f.sku_key, f.sale_date AS d,
                   SUM(f.qty_sold - f.qty_returned) AS sold,
                   SUM(f.u40) AS u40, SUM(f.u40_strict) AS u40_strict
            FROM mart.fact_sales_daily f JOIN canon c ON c.point_key = f.point_key
            GROUP BY 1, 2, 3
        ),
        q AS (
            SELECT c.pt, f.sku_key, f.supply_date AS d, SUM(f.qty) AS delivered
            FROM mart.fact_supply_daily f JOIN canon c ON c.point_key = f.point_key
            GROUP BY 1, 2, 3
        ),
        w AS (
            SELECT c.pt, f.sku_key, f.wo_date AS d, SUM(f.qty_writeoff) AS wo
            FROM mart.fact_writeoff_daily f JOIN canon c ON c.point_key = f.point_key
            GROUP BY 1, 2, 3
        )
        SELECT s.pt AS point_key, s.sku_key, s.d AS trade_date,
               s.sold, s.u40, s.u40_strict,
               coalesce(q.delivered, 0) AS delivered,
               coalesce(w.wo, 0) AS writeoff,
               q.delivered IS NOT NULL AS has_supply,
               CASE
                 WHEN q.delivered IS NULL THEN 'нет данных о привозе'
                 WHEN s.sold >= q.delivered - 1e-9 AND s.u40 < 0.5 AND coalesce(w.wo, 0) < 0.5 THEN 'A'
                 WHEN s.sold >= q.delivered - 1e-9 THEN 'B'
                 WHEN s.u40 >= 0.5 THEN 'C'
                 ELSE 'D'
               END AS regime
        FROM s
        LEFT JOIN q ON q.pt = s.pt AND q.sku_key = s.sku_key AND q.d = s.d
        LEFT JOIN w ON w.pt = s.pt AND w.sku_key = s.sku_key AND w.d = s.d
    """)
    # Самопроверка независимой сущностью, а не самой собой: если разметка верна, доля
    # строк со списанием обязана падать в порядке C → D → B → A. У остатка с уценкой
    # списаний много, у настоящего дефицита почти нет. Если порядок нарушен —
    # классификация неверна, и всё, что на ней построено, считать нельзя.
    chk = con.execute("""
        SELECT regime AS режим, count(*) AS строк,
               round(100.0 * sum(CASE WHEN writeoff > 0 THEN 1 ELSE 0 END) / count(*), 1)
                   AS "со списанием, %"
        FROM mart.fact_regime_daily
        WHERE has_supply AND regime IN ('A', 'B', 'C', 'D')
        GROUP BY 1 ORDER BY 3 DESC
    """).fetchall()
    if chk:
        order = [r[0] for r in chk]
        ok = order == ["C", "D", "B", "A"]
        print("  проверка разметки режимов (доля строк со списанием):")
        for reg, n, pct in chk:
            print(f"    {reg}: {n:>7,} строк, {pct:>5.1f}% со списанием")
        print(f"    порядок {' → '.join(order)} — "
              f"{'ожидаемый ✓' if ok else '⚠ НЕ ожидаемый, разметку перепроверить'}")


def main():
    only = [a for a in sys.argv[1:] if not a.startswith("-")]
    con = connect_writer()
    con.execute("CREATE SCHEMA IF NOT EXISTS mart")
    for name, fn in STEPS:
        if only and name not in only:
            continue
        t0 = time.time()
        fn(con)
        n = con.execute(f"SELECT count(*) FROM mart.{name}").fetchone()[0]
        print(f"mart.{name:<22} {n:>12,} строк  за {time.time() - t0:6.1f} с", flush=True)
    con.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
