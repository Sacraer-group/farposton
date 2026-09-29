# -*- coding: utf-8 -*-
"""Недозаказ по точкам: лист на каждую точку со ВСЕМИ её позициями.

Отвечает на вопрос «что конкретно на этой точке недовозят»: полный список
позиций (не топ-N), отсортированный по потерянным деньгам, с историей
дефицитных дней по месяцам и вердиктом «что делать».

Числа — из того же кэша полки, что панель и календарь (prognoz.shelf.core),
таблицы — из общего prognoz.report._shelf_tables: разойтись не могут.

Запуск: PYTHONUTF8=1 PYTHONPATH=prognoz/src py -m prognoz.report.point_positions
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

SRC = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(SRC))
sys.path.insert(0, str(SRC.parents[0] / "analysis" / "etl"))
from _db import OUTPUT, connect_reader                      # noqa: E402
from prognoz.assortment import filter_frame                 # noqa: E402
from prognoz.report._shelf_tables import (deficit_history,  # noqa: E402
                                          pair_summary, point_summary)
from prognoz.shelf.core import load_or_build, summarize     # noqa: E402
from prognoz.shelf.params import Params                     # noqa: E402

# Колонки листа точки в порядке чтения: сначала «сколько теряем», потом «почему»
COLS = ["Позиция", "Группа", "Что делать", "Недопродано, тыс ₽", "Недопродано, шт",
        "Недозаказ, % спроса", "Дней дефицита", "Дефицит, % дней",
        "Средний час затишья", "Списано, тыс ₽", "Списано, шт",
        "Списания, % дней", "Продано, шт", "Дней в обороте",
        "Цена промаха, тыс ₽", "Последние дни дефицита"]


def how_to_read(p: Params, s: dict) -> pd.DataFrame:
    return pd.DataFrame({"Пояснение": [
        f"НЕДОЗАКАЗ ПО ТОЧКАМ. Период: {p.period_ru}.",
        "На каждую точку — свой лист со ВСЕМИ её позициями, сверху те, на которых",
        "теряем больше денег. Числа те же, что на панели показателей.",
        "",
        "КАК ЧИТАЕТСЯ СТРОКА",
        "  Недопродано, шт — сколько штук позиция продала бы до закрытия, если бы",
        "     не кончилась днём. Считается по её же обычной скорости продаж в эти",
        "     часы. Дни со списанием и с вечерней уценкой сюда не попадают: там",
        "     товар на полке был.",
        "  Недозаказ, % спроса — недопродано / (продано + недопродано).",
        "  Дней дефицита — в скольких днях позиция замолчала рано и потеря значима.",
        "  Средний час затишья — во сколько в среднем позиция перестаёт продаваться.",
        "  Списано — обратная ошибка: привезли больше, чем купили.",
        "  Последние дни дефицита — пять последних дат, когда не хватило.",
        "",
        "ЧТО ДЕЛАТЬ (автоматический вердикт, повод посмотреть, а не приказ)",
        "  «систематически не хватает — поднять» — дефицит часто, списаний почти",
        "     нет: позицию стабильно недовозят, уровень заказа занижен.",
        "  «стабильно лишнее — снизить» — списания часто, дефицита почти нет:",
        "     возят с запасом, который не продаётся.",
        "  «скачет: то пусто, то густо» — И дефицит, И списания высокие: уровень",
        "     в среднем верный, промахи по дням в обе стороны. Поднимать уровень",
        "     вредно (вырастут списания) — тут нужен точный прогноз на каждый",
        "     день. Именно на таких позициях самые большие деньги промаха.",
        "  «в норме» / «мало данных» (позиция в обороте меньше 30 дней).",
        "",
        "ДЕНЬГИ. Недопродажа × 0,67 цены (потерянная маржа), списание × 0,33 цены",
        "(сгоревшая себестоимость). Вечерняя уценка в потери НЕ входит: товар продан",
        "со скидкой и всё равно принёс прибыль.",
        "",
        "ОЦЕНКА КОНСЕРВАТИВНА — она НИЖНЯЯ граница:",
        f"  · порог значимости {p.signif_units:.0f} шт отбросил "
        f"{s['lost_zeroed_units']:,.0f} шт мелкого хвоста;",
        "  · скорость продаж считается по всем дням, включая дефицитные (их вечер",
        "    нулевой) — то есть занижена;",
        f"  · {s['supply_gap_days']} точко-дней срывов поставки (продаж ноль сразу",
        f"    у 5+ позиций, ≈{s['supply_gap_units_est']:,.0f} шт) в потери не включены вовсе.",
        "",
        "Списания включают питание сотрудников и порчу при приёмке — эта часть",
        "точным заказом не сокращается.",
    ]})


def build_sheets(df: pd.DataFrame) -> dict[str, pd.DataFrame]:
    pairs = pair_summary(df)
    hist = deficit_history(df)
    month_cols = [c for c in hist.columns
                  if c not in ("Точка", "Позиция", "Последние дни дефицита")]
    merged = pairs.merge(hist, on=["Точка", "Позиция"], how="left")
    merged[month_cols] = merged[month_cols].fillna(0).astype(int)
    merged["Последние дни дефицита"] = merged["Последние дни дефицита"].fillna("")
    out = {}
    for pt, g in merged.groupby("Точка"):
        cols = COLS + month_cols
        out[str(pt)] = (g[cols].sort_values("Недопродано, тыс ₽", ascending=False)
                        .reset_index(drop=True))
    return out


def to_excel(df: pd.DataFrame, path: Path, p: Params) -> None:
    s = summarize(df, p)
    sheets = build_sheets(df)
    with pd.ExcelWriter(path, engine="xlsxwriter") as xw:
        book = xw.book
        head = book.add_format({"bold": True, "bg_color": "#DDEBF7", "border": 1,
                                "text_wrap": True, "valign": "top"})
        warn = book.add_format({"bg_color": "#FCE4D6"})
        over = book.add_format({"bg_color": "#DDEBF7"})

        for name, tbl in (("Как читать", how_to_read(p, s)),
                          ("Все точки", point_summary(df))):
            tbl.to_excel(xw, sheet_name=name, index=False)
            ws = xw.sheets[name]
            for i, c in enumerate(tbl.columns):
                ws.set_column(i, i, 78 if name == "Как читать" else 16)
                ws.write(0, i, str(c), head)
            ws.freeze_panes(1, 1)

        for pt, tbl in sheets.items():
            sheet = pt[:31]
            tbl.to_excel(xw, sheet_name=sheet, index=False)
            ws = xw.sheets[sheet]
            for i, c in enumerate(tbl.columns):
                width = 38 if c == "Позиция" else (
                    26 if c in ("Что делать", "Последние дни дефицита", "Группа")
                    else (13 if c in COLS else 6))
                ws.set_column(i, i, width)
                ws.write(0, i, str(c), head)
            ws.freeze_panes(1, 1)
            ws.autofilter(0, 0, len(tbl), len(tbl.columns) - 1)
            # подсветка вердикта: чего не хватает — оранжевым, лишнее — синим
            col = tbl.columns.get_loc("Что делать")
            for ri, v in enumerate(tbl["Что делать"]):
                if v.startswith("систематически"):
                    ws.write(ri + 1, col, v, warn)
                elif v.startswith("стабильно"):
                    ws.write(ri + 1, col, v, over)


def main() -> int:
    p = Params()
    con = connect_reader()
    df = filter_frame(
        load_or_build(con, p), "metric_scope", date_col="trade_day")
    path = OUTPUT / f"Недозаказ_по_точкам_{p.since:%Y-%m-%d}__{p.until:%Y-%m-%d}.xlsx"
    to_excel(df, path, p)
    pairs = pair_summary(df)
    print(f"позиций всего: {len(pairs)}, из них «не хватает»: "
          f"{(pairs['Что делать'] == 'систематически не хватает — поднять').sum()}, "
          f"«лишнее»: {(pairs['Что делать'] == 'стабильно лишнее — снизить').sum()}")
    print(point_summary(df).to_string(index=False))
    print(f"\n→ {path}")
    con.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
