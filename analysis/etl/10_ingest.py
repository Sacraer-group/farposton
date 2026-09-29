# -*- coding: utf-8 -*-
"""Ingest прогноза: JSONL-выгрузка 1С → DuckDB raw.* (prognoz.duckdb).

Схемы таблиц выверены на этой же выгрузке. Специфика прогноза: + ЗаказПоставщику
(старый канал заявок, с 11.2024) к ЗаказНаПеремещение (текущий канал, с 03.2026).

Каждая таблица читается ОДНИМ read_json с ЯВНЫМ списком колонок (columns=...):
- без авто-сниффинга схемы (критично для продаж 6,5 ГБ — иначе двойной проход);
- лишние поля (включая *@navigationLinkUrl) отбрасываются на лету.
Идемпотентно: CREATE OR REPLACE, можно перезапускать. Один процесс-писатель.

Запуск: PYTHONUTF8=1 py prognoz/analysis/etl/10_ingest.py [имя_таблицы ...]
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _db import connect_writer, jsonl

G = "VARCHAR"      # GUID-ключи 1С храним строками
TS = "TIMESTAMP"
N = "DOUBLE"
B = "BOOLEAN"
I = "BIGINT"

# таблица → (файл выгрузки, {поле: тип})
TABLES = {
    # --- справочники ---
    "orgs": ("Catalog_Организации", {
        "Ref_Key": G, "Code": "VARCHAR", "Description": "VARCHAR",
        "НаименованиеПолное": "VARCHAR", "ИНН": "VARCHAR",
        "ГоловнаяОрганизация_Key": G, "_фжн_Организациясети": B,
    }),
    "points": ("Catalog_СтруктурныеЕдиницы", {
        "Ref_Key": G, "DeletionMark": B, "Parent_Key": G, "Code": "VARCHAR",
        "Description": "VARCHAR", "НаименованиеПолное": "VARCHAR",
        "ТипСтруктурнойЕдиницы": "VARCHAR", "Организация_Key": G,
        "Недействителен": B, "_фжн_Продуктовый": B, "_фжн_СкладБП": G,
    }),
    "units": ("Catalog_ЕдиницыИзмерения", {
        "Ref_Key": G, "DeletionMark": B, "Owner": G, "Owner_Type": "VARCHAR",
        "Code": "VARCHAR", "Description": "VARCHAR", "Коэффициент": N,
        "ЕдиницаИзмеренияПоКлассификатору_Key": G, "ЕдиницаИзмеренияВладельца_Key": G,
    }),
    "categories": ("Catalog_КатегорииНоменклатуры", {
        "Ref_Key": G, "Parent_Key": G, "IsFolder": B, "Code": "VARCHAR",
        "Description": "VARCHAR", "УОП_ЭтоКатегорияБлюд": B, "ЕдиницаИзмерения_Key": G,
    }),
    "nomenclature": ("Catalog_Номенклатура", {
        "Ref_Key": G, "DeletionMark": B, "Parent_Key": G, "IsFolder": B,
        "Code": "VARCHAR", "Description": "VARCHAR", "НаименованиеПолное": "VARCHAR",
        "Артикул": "VARCHAR", "Вес": N, "Весовой": B, "Недействителен": B,
        "ТипНоменклатуры": "VARCHAR", "КатегорияНоменклатуры_Key": G,
        "ЗапретитьВводДробногоКоличества": B, "УОП_ДробноеКоличество": B,
        "УОП_ЭтоНапиток": B, "УОП_ТоварнаяГруппа_Key": G,
        "ЕдиницаИзмерения_Key": G,
    }),
    # --- продажи (крупнейший файл, 6,5 ГБ) ---
    "sales_headers": ("Document_ОтчетОРозничныхПродажах", {
        "Ref_Key": G, "Number": "VARCHAR", "Date": TS, "Posted": B, "DeletionMark": B,
        "Организация_Key": G, "СтруктурнаяЕдиница_Key": G,
    }),
    # Скидки: строка = агрегат за сутки по (документ × SKU), поэтому процент
    # средневзвешенный (20% = 40/2, 13,33% = 40/3). Число уценённых единиц
    # восстанавливается как СуммаСкидкиНаценки/(0,4×Цена) — см. 20_marts.py.
    "sales_lines": ("Document_ОтчетОРозничныхПродажах_Запасы", {
        "Ref_Key": G, "LineNumber": I, "Номенклатура_Key": G, "Количество": N,
        "Возврат": N, "Цена": N, "Сумма": N, "Всего": N,
        "ПроцентСкидкиНаценки": N, "СуммаСкидкиНаценки": N,
        "ВидСкидкиНаценки_Key": G, "ДисконтнаяКарта_Key": G,
        "СтруктурнаяЕдиница_Key": G, "НоменклатураНабора_Key": G,
        "ЕдиницаИзмерения": G, "ЕдиницаИзмерения_Type": "VARCHAR",
    }),
    # --- заявки: текущий канал (точки ← склад «Точка 3», с 03.2026) ---
    "transfer_orders": ("Document_ЗаказНаПеремещение", {
        "Ref_Key": G, "Number": "VARCHAR", "Date": TS, "Posted": B, "DeletionMark": B,
        "Организация_Key": G, "СтруктурнаяЕдиницаРезерв_Key": G,
        "СтруктурнаяЕдиницаПолучатель_Key": G, "ДатаПеремещения": TS,
        "Автор_Key": G, "Комментарий": "VARCHAR", "СостояниеЗаказа_Key": G,
        "Запасы": "JSON",   # ТЧ вложена (документ качали целиком): Количество, КоличествоСобрано
    }),
    # --- заявки: старый канал (ЗаказПоставщику, с 11.2024) ---
    "zakaz_headers": ("Document_ЗаказПоставщику", {
        "Ref_Key": G, "Number": "VARCHAR", "Date": TS, "Posted": B, "DeletionMark": B,
        "Организация_Key": G, "СтруктурнаяЕдиница_Key": G, "Контрагент_Key": G,
        "ВидОперации": "VARCHAR",
    }),
    "zakaz_lines": ("Document_ЗаказПоставщику_Запасы", {
        "Ref_Key": G, "LineNumber": I, "Номенклатура_Key": G, "Количество": N,
        "Цена": N, "Сумма": N, "Всего": N, "ДатаПоступления": TS,
        "СтруктурнаяЕдиницаРезерв_Key": G, "УОП_Комментарий": "VARCHAR",
        "ЕдиницаИзмерения": G, "ЕдиницаИзмерения_Type": "VARCHAR",
    }),
    # --- перемещения: канал привоза на маркеты С МАРТА 2026 ---
    "move_headers": ("Document_ПеремещениеЗапасов", {
        "Ref_Key": G, "Number": "VARCHAR", "Date": TS, "Posted": B, "DeletionMark": B,
        "Организация_Key": G, "СтруктурнаяЕдиница_Key": G, "СтруктурнаяЕдиницаПолучатель_Key": G,
    }),
    "move_lines": ("Document_ПеремещениеЗапасов_Запасы", {
        "Ref_Key": G, "LineNumber": I, "Номенклатура_Key": G, "Количество": N,
        "ЕдиницаИзмерения": G, "ЕдиницаИзмерения_Type": "VARCHAR",
        "Себестоимость": N, "Сумма": N, "Цена": N, "Вес": N,
    }),
    # --- передачи между организациями: канал привоза ДО МАРТА 2026 ---
    # Сеть перестроила учёт в марте 2026 (точка = отдельное юрлицо → внутренний склад).
    # Без этого канала приход до марта отсутствует — отсюда и несходимость остатков.
    "supply_headers": ("Document_ПередачаТоваровМеждуОрганизациями", {
        "Ref_Key": G, "Number": "VARCHAR", "Date": TS, "Posted": B, "DeletionMark": B,
        "ВидОперации": "VARCHAR", "Организация_Key": G, "ОрганизацияПолучатель_Key": G,
        "СтруктурнаяЕдиница_Key": G, "УОП_СтруктурнаяЕдиницаПриход_Key": G,
    }),
    "supply_lines": ("Document_ПередачаТоваровМеждуОрганизациями_Запасы", {
        "Ref_Key": G, "LineNumber": I, "Номенклатура_Key": G, "Количество": N,
        "Цена": N, "Сумма": N, "Себестоимость": N,
        "СтруктурнаяЕдиница_Key": G, "УОП_СтруктурнаяЕдиницаПриход_Key": G,
    }),
    # --- сборка/выпуск продукции: часть товара точки делают сами (риск R1) ---
    # Здесь же Спецификация_Key (рецептура → путь к себестоимости) и сроки годности.
    "assembly_headers": ("Document_СборкаЗапасов", {
        "Ref_Key": G, "Number": "VARCHAR", "Date": TS, "Posted": B, "DeletionMark": B,
        "ВидОперации": "VARCHAR", "Организация_Key": G, "СтруктурнаяЕдиница_Key": G,
        "СтруктурнаяЕдиницаЗапасов_Key": G, "СтруктурнаяЕдиницаПродукции_Key": G,
    }),
    "assembly_lines": ("Document_СборкаЗапасов_Запасы", {
        "Ref_Key": G, "LineNumber": I, "Номенклатура_Key": G, "Количество": N,
        "Спецификация_Key": G, "УОП_СрокГодности": TS, "УОП_СрокХранения": "VARCHAR",
        "СтруктурнаяЕдиница_Key": G,
    }),
    # --- списания (главная бизнес-метрика «как есть») ---
    # ⚠️ Не запускать, пока идёт добор odata/fetch_writeoff_markets.py — он дописывает файл.
    "writeoff_headers": ("Document_СписаниеЗапасов", {
        "Ref_Key": G, "Number": "VARCHAR", "Date": TS, "Posted": B, "DeletionMark": B,
        "Организация_Key": G, "СтруктурнаяЕдиница_Key": G,
        "ПричинаСписания": "VARCHAR", "Комментарий": "VARCHAR", "СуммаДокумента": N,
    }),
    "writeoff_lines": ("Document_СписаниеЗапасов_Запасы", {
        "Ref_Key": G, "LineNumber": I, "Номенклатура_Key": G, "Количество": N,
        "Цена": N, "Сумма": N,
    }),
}


def ingest(con, table: str):
    src, cols = TABLES[table]
    path = jsonl(src)
    col_spec = ", ".join(f"'{name}': '{typ}'" for name, typ in cols.items())
    t0 = time.time()
    con.execute(f"""
        CREATE OR REPLACE TABLE raw.{table} AS
        SELECT * FROM read_json('{path}',
            format='newline_delimited',
            columns={{{col_spec}}})
    """)
    n = con.execute(f"SELECT count(*) FROM raw.{table}").fetchone()[0]
    print(f"raw.{table:<16} {n:>12,} строк  за {time.time() - t0:6.1f} с  ({src})", flush=True)
    return n


def main():
    only = [a for a in sys.argv[1:] if not a.startswith("-")]
    con = connect_writer()
    con.execute("CREATE SCHEMA IF NOT EXISTS raw")
    total = 0
    for table in TABLES:
        if only and table not in only:
            continue
        total += ingest(con, table)
    con.close()
    print(f"Итого строк в raw.*: {total:,}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
