# -*- coding: utf-8 -*-
"""Таблицы для разметки ассортимента менеджерами — по одной книге на точку.

Отчёт по ассортименту готовой еды: «пришлите общий список позиций,
который у вас есть — помечу, что выведено, добавлю позиции и разобью по группам».

В книгу точки попадают ВСЕ позиции скоупа (Меню без перепродажи и бара),
по которым за окно были продажи ИЛИ заявки на точке, ИЛИ которые есть в текущей
автозаявке, ИЛИ упомянуты в политике ассортимента. Числа — из витрин (та же база,
что у движка). Справа — пустые колонки для разметки с выпадающими списками.

Запуск: PYTHONUTF8=1 PYTHONPATH=prognoz/src py -m prognoz.report.point_assortment [--out ПАПКА] [--days 90]
"""
from __future__ import annotations

import argparse
import sys
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

SRC = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(SRC))
sys.path.insert(0, str(SRC.parents[0] / "analysis" / "etl"))
from _db import OUTPUT, connect_reader                      # noqa: E402
from prognoz.assortment import load_policy                  # noqa: E402
from prognoz.scope import canon_cte, sku_filter, sku_twins_cte  # noqa: E402

# Предлагаемое деление заявки на две части (правило по группе 1С; менеджер правит)
SHAWARMA_GROUPS = {"ДЕМО Шаурма"}
PART_SHAWARMA, PART_KITCHEN = "Шаурма", "Кухня"

MARK_COLS = ["Готовим на точке / Заказываем с фабрики", "Часть заявки (Кухня / Шаурма)",
             "Убрать / Добавить", "Комментарий"]
MARK_LISTS = {
    "Готовим на точке / Заказываем с фабрики": ["Заказываем с фабрики", "Готовим на точке"],
    "Часть заявки (Кухня / Шаурма)": [PART_KITCHEN, PART_SHAWARMA],
    "Убрать / Добавить": ["Оставить", "Убрать из заявки", "Добавить в заявку"],
}
COLS = ["Позиция", "Группа 1С", "Продано 28 дн", "Продано 90 дн", "Заявки менеджера 28 дн",
        "Списано 28 дн", "Уценено, % (28 дн)", "Последняя продажа", "В автозаявке сейчас",
        "Почему нет в заявке", "Предлагаемая часть заявки", *MARK_COLS]


def _points(con) -> pd.DataFrame:
    return con.execute("""
        SELECT point_key, canon_name FROM mart.dim_point
        WHERE role IN ('market', 'market_hub') AND canon_name IS NOT NULL
        ORDER BY canon_name
    """).fetchdf()


def _facts(con, end: date, days: int) -> pd.DataFrame:
    """Пара (точка, SKU) × продажи/заявки/списания за окно и за 28 дней."""
    since, since28 = end - timedelta(days=days), end - timedelta(days=28)
    return con.execute(f"""
        WITH {canon_cte()}, {sku_twins_cte()},
        sl AS (
            SELECT c.pt AS point_key, coalesce(tw.live, f.sku_key) AS sku_key,
                   sum(f.qty_sold - f.qty_returned) AS sold_90,
                   sum(CASE WHEN f.sale_date >= ? THEN f.qty_sold - f.qty_returned ELSE 0 END) AS sold_28,
                   sum(CASE WHEN f.sale_date >= ? THEN f.u40 ELSE 0 END) AS u40_28,
                   max(f.sale_date) AS last_sale
            FROM mart.fact_sales_daily f
            JOIN c ON c.point_key = f.point_key
            LEFT JOIN tw ON tw.ghost = f.sku_key
            JOIN mart.dim_sku s ON s.sku_key = coalesce(tw.live, f.sku_key)
            WHERE {sku_filter('s')} AND f.sale_date BETWEEN ? AND ?
            GROUP BY 1, 2),
        od AS (
            SELECT c.pt AS point_key, coalesce(tw.live, o.sku_key) AS sku_key,
                   sum(CASE WHEN o.trade_date >= ? THEN o.qty_ordered ELSE 0 END) AS ordered_28,
                   sum(o.qty_ordered) AS ordered_90,
                   max(o.trade_date) AS last_order
            FROM mart.fact_orders_daily o
            JOIN c ON c.point_key = o.point_key
            LEFT JOIN tw ON tw.ghost = o.sku_key
            JOIN mart.dim_sku s ON s.sku_key = coalesce(tw.live, o.sku_key)
            WHERE {sku_filter('s')} AND o.channel = 'transfer' AND o.trade_date BETWEEN ? AND ?
            GROUP BY 1, 2),
        wo AS (
            SELECT c.pt AS point_key, coalesce(tw.live, w.sku_key) AS sku_key,
                   sum(w.qty_writeoff) AS wo_28
            FROM mart.fact_writeoff_daily w
            JOIN c ON c.point_key = w.point_key
            LEFT JOIN tw ON tw.ghost = w.sku_key
            JOIN mart.dim_sku s ON s.sku_key = coalesce(tw.live, w.sku_key)
            WHERE {sku_filter('s')} AND w.wo_date BETWEEN ? AND ?
            GROUP BY 1, 2),
        keys AS (
            SELECT point_key, sku_key FROM sl
            UNION SELECT point_key, sku_key FROM od
            UNION SELECT point_key, sku_key FROM wo)
        SELECT k.point_key, k.sku_key,
               coalesce(sl.sold_28, 0) AS sold_28, coalesce(sl.sold_90, 0) AS sold_90,
               coalesce(sl.u40_28, 0) AS u40_28, sl.last_sale,
               coalesce(od.ordered_28, 0) AS ordered_28, coalesce(od.ordered_90, 0) AS ordered_90,
               od.last_order, coalesce(wo.wo_28, 0) AS wo_28
        FROM keys k
        LEFT JOIN sl USING (point_key, sku_key)
        LEFT JOIN od USING (point_key, sku_key)
        LEFT JOIN wo USING (point_key, sku_key)
    """, [since28, since28, since, end, since28, since, end, since28, end]).fetchdf()


def _skus(con) -> pd.DataFrame:
    df = con.execute(f"""
        SELECT s.sku_key, s.name, s.group_path FROM mart.dim_sku s WHERE {sku_filter('s')}
    """).fetchdf()
    df["group"] = df["group_path"].fillna("").map(lambda g: g.split(" / ")[-1])
    return df


def _latest_release() -> tuple[str | None, set[tuple[str, str]]]:
    """(дата цели, пары (точка-имя, sku) с ненулевой строкой) из свежего файла заявки."""
    log = OUTPUT / "forecast_log"
    files = sorted(log.glob("*.parquet")) if log.exists() else []
    if not files:
        return None, set()
    df = pd.read_parquet(files[-1])
    keep = df[df["Прогноз, шт"] > 0] if "Прогноз, шт" in df.columns else df
    return files[-1].stem, set(zip(keep["Точка"].astype(str), keep["_sku_key"].astype(str)))


def _policy_lookup() -> tuple[dict, str]:
    """(точка или None, sku) → Entry; версия политики."""
    policy = load_policy()
    table = {}
    for e in policy.entries:
        table[(e.point_key, e.sku_key)] = e
    return table, policy.version


def build(con, *, days: int = 90) -> dict[str, pd.DataFrame]:
    end = con.execute("SELECT max(sale_date) FROM mart.fact_sales_daily").fetchone()[0]
    end = pd.Timestamp(end).date()
    facts = _facts(con, end, days)
    skus = _skus(con).set_index("sku_key")
    points = _points(con)
    release_target, in_release = _latest_release()
    policy, policy_version = _policy_lookup()
    books: dict[str, pd.DataFrame] = {}
    for pt in points.itertuples():
        part = facts[facts["point_key"] == pt.point_key].set_index("sku_key")
        keys = set(part.index)
        keys |= {s for (p, s) in in_release if p == pt.canon_name}
        keys |= {s for (p, s) in policy if p in (pt.point_key, None)}
        rows = []
        for sku in keys:
            if sku not in skus.index:
                continue
            f = part.loc[sku] if sku in part.index else None
            entry = policy.get((pt.point_key, sku)) or policy.get((None, sku))
            excluded = entry is not None and not entry.factory_order
            released = (pt.canon_name, sku) in in_release
            sold28 = float(f["sold_28"]) if f is not None else 0.0
            why = ""
            if excluded:
                why = "исключено политикой ассортимента: " + (entry.reason or "").strip()
            elif not released:
                why = ("нет продаж и заявок за 28 дн" if sold28 == 0 and
                       (f is None or float(f["ordered_28"]) == 0) else
                       "прогноз нулевой / нет автозаявки на точку")
            group = skus.at[sku, "group"]
            rows.append({
                "Позиция": skus.at[sku, "name"],
                "Группа 1С": group,
                "Продано 28 дн": round(sold28, 1),
                "Продано 90 дн": round(float(f["sold_90"]), 1) if f is not None else 0.0,
                "Заявки менеджера 28 дн": round(float(f["ordered_28"]), 1) if f is not None else 0.0,
                "Списано 28 дн": round(float(f["wo_28"]), 1) if f is not None else 0.0,
                "Уценено, % (28 дн)": (round(100 * float(f["u40_28"]) / sold28)
                                       if f is not None and sold28 > 0 else 0),
                "Последняя продажа": (pd.Timestamp(f["last_sale"]).strftime("%d.%m.%Y")
                                      if f is not None and pd.notna(f["last_sale"]) else ""),
                "В автозаявке сейчас": "да" if released and not excluded else "нет",
                "Почему нет в заявке": why,
                "Предлагаемая часть заявки": PART_SHAWARMA if group in SHAWARMA_GROUPS else PART_KITCHEN,
                **{c: "" for c in MARK_COLS},
                "_sku_key": sku, "_point_key": pt.point_key,
            })
        df = pd.DataFrame(rows, columns=[*COLS, "_sku_key", "_point_key"])
        df = df.sort_values(["Предлагаемая часть заявки", "Группа 1С", "Позиция"]).reset_index(drop=True)
        df.attrs.update({"end": end, "days": days, "release_target": release_target,
                         "policy_version": policy_version})
        books[pt.canon_name] = df
    return books


def _how_to_fill(df: pd.DataFrame, point: str) -> pd.DataFrame:
    return pd.DataFrame({"Как заполнять": [
        f"Точка: {point}. Данные по {df.attrs['end']:%d.%m.%Y}, окно {df.attrs['days']} дней. "
        f"Текущая автозаявка: {df.attrs['release_target'] or '—'}; политика ассортимента: {df.attrs['policy_version']}.",
        "",
        "Лист «Позиции» — все позиции, которые продавались или заказывались на точке за окно, плюс то, что "
        "есть в автозаявке или в политике ассортимента. Числа — из 1С (продажи, заявки на перемещение, списания).",
        "",
        "Заполните ЧЕТЫРЕ жёлтые колонки справа (в трёх — выпадающий список):",
        "1. «Готовим на точке / Заказываем с фабрики» — что реально едет с фабрики, а что делаете сами.",
        "2. «Часть заявки (Кухня / Шаурма)» — в какую из двух заявок должна попадать позиция; "
        "колонка «Предлагаемая часть заявки» — наше предположение по группе 1С, поправьте, где не так.",
        "3. «Убрать / Добавить» — «Убрать из заявки», если позицию возить не надо; «Добавить в заявку», "
        "если её не хватает (в т.ч. новые позиции — допишите строкой внизу).",
        "4. «Комментарий» — любые пояснения (сезонность, минимальная партия, «только по выходным» и т.п.).",
        "",
        "«В автозаявке сейчас» = «нет» с причиной — позиция сегодня не попадает в заявку сервиса: "
        "либо исключена по итогам встречи 10.09, либо по ней не было продаж и заявок.",
        "Колонки с числами не редактируйте — они пересчитываются автоматически. Служебные колонки "
        "(_sku_key, _point_key) нужны, чтобы загрузить разметку обратно; не удаляйте их.",
    ]})


def to_excel(df: pd.DataFrame, point: str, path: Path) -> None:
    from openpyxl.styles import Alignment, Font, PatternFill    # noqa: PLC0415
    from openpyxl.utils import get_column_letter                # noqa: PLC0415
    from openpyxl.worksheet.datavalidation import DataValidation  # noqa: PLC0415

    path.parent.mkdir(parents=True, exist_ok=True)
    with pd.ExcelWriter(path, engine="openpyxl") as xl:
        _how_to_fill(df, point).to_excel(xl, sheet_name="Как заполнять", index=False)
        xl.sheets["Как заполнять"].column_dimensions["A"].width = 120
        for cell in xl.sheets["Как заполнять"]["A"]:
            cell.alignment = Alignment(wrap_text=True, vertical="top")
        df.to_excel(xl, sheet_name="Позиции", index=False)
        ws = xl.sheets["Позиции"]
        widths = {"Позиция": 42, "Группа 1С": 20, "Почему нет в заявке": 46, "Комментарий": 40,
                  "Готовим на точке / Заказываем с фабрики": 30, "Часть заявки (Кухня / Шаурма)": 26,
                  "Убрать / Добавить": 22, "Предлагаемая часть заявки": 18, "Последняя продажа": 14,
                  "_sku_key": 14, "_point_key": 14}
        yellow = PatternFill("solid", fgColor="FFF4C2")
        n = len(df) + 1
        for idx, col in enumerate(df.columns, 1):
            letter = get_column_letter(idx)
            ws.column_dimensions[letter].width = widths.get(col, 14)
            ws.cell(row=1, column=idx).font = Font(bold=True)
            ws.cell(row=1, column=idx).alignment = Alignment(wrap_text=True, vertical="top")
            if col in MARK_COLS:
                for r in range(2, n + 1):
                    ws.cell(row=r, column=idx).fill = yellow
            if col in MARK_LISTS and n > 1:
                dv = DataValidation(type="list", formula1='"' + ",".join(MARK_LISTS[col]) + '"',
                                    allow_blank=True)
                ws.add_data_validation(dv)
                dv.add(f"{letter}2:{letter}{n + 50}")   # запас строк под «добавить внизу»
        ws.freeze_panes = "B2"
        ws.auto_filter.ref = f"A1:{get_column_letter(len(df.columns))}{n}"
        ws.row_dimensions[1].height = 45


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, default=OUTPUT / "разметка")
    parser.add_argument("--days", type=int, default=90)
    args = parser.parse_args()
    con = connect_reader()
    try:
        books = build(con, days=args.days)
    finally:
        con.close()
    stamp = date.today().strftime("%Y-%m-%d")
    for point, df in books.items():
        safe = point.replace("/", "-").replace("(", "").replace(")", "")
        path = args.out / f"Разметка_ассортимента_{safe}_{stamp}.xlsx"
        to_excel(df, point, path)
        print(f"{point}: строк {len(df)}, в автозаявке {int((df['В автозаявке сейчас'] == 'да').sum())} → {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
