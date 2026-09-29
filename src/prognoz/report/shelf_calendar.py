# -*- coding: utf-8 -*-
"""Календарь позиций: Excel-инструмент «когда что кончается и что списывают».

Все числа берутся из prognoz.shelf.core (та же реализация, что у блока фактов
и панели) через кэш со штампом — файл не может разойтись с реестром.

Запуск: PYTHONUTF8=1 PYTHONPATH=prognoz/src py -m prognoz.report.shelf_calendar [--days=60]
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

SRC = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(SRC))
sys.path.insert(0, str(SRC.parents[0] / "analysis" / "etl"))
from _db import OUTPUT, connect_reader                    # noqa: E402
from prognoz.assortment import filter_frame               # noqa: E402
from prognoz.report._shelf_tables import (net_summary, pair_summary,  # noqa: E402
                                          point_summary)
from prognoz.shelf.core import load_or_build, summarize   # noqa: E402
from prognoz.shelf.params import Params                   # noqa: E402

# символ исхода в ячейке календаря
SYM = {
    "остаток списан": "С",
    "остаток ушёл в уценку": "У",
    "не продавалось": "·",
    "кончилось рано": "д",          # прописная Д — если дефицит значим
    "распродано под закрытие": "Р",
}
COLORS = {"Д": "#FCE4D6", "д": "#FFF2CC", "Р": "#E2EFDA", "У": "#DDEBF7",
          "С": "#F2DCDB", "·": "#F2F2F2", "?": "#FFFFFF"}


def how_to_read(p: Params, s: dict) -> pd.DataFrame:
    return pd.DataFrame({"Пояснение": [
        f"Период: {p.period_ru}. Строка календаря = позиция, колонка = торговый день.",
        "",
        "СИМВОЛЫ В ЯЧЕЙКАХ КАЛЕНДАРЯ:",
        "  Д — кончилось рано И потеря значима (за остаток вечера ожидалось ≥2 шт);",
        "      рядом число — сколько штук недопродано по оценке",
        "  д — замолчала рано, но потеря ниже порога (редкая позиция, возможен шум)",
        "  Р — распродано под закрытие: идеальный день",
        "  У — остаток ушёл по вечерней скидке (товар был, дефицита не было)",
        "  С — остаток списан (перезаказ); рядом число — сколько списано",
        "  · — не продавалось    ? — акты списания ещё не пришли, исход предварительный",
        "",
        "ОТКУДА ЧИСЛА. Время продаж — чеки кассы (r_keeper через 1С). Количество",
        "проданного и уценённого — дневная витрина 1С. Списания — акты 1С; они",
        f"приходят с лагом ~7 дней, поэтому свежие дни помечены «?» ({s['prelim_days']}",
        "дней-позиций сейчас в этом статусе) и в годовые итоги не входят.",
        "",
        "ДЕНЬГИ. Недопродажа = штуки × средняя базовая цена × 0,67 (потерянная",
        "маржа); если в этот день была дневная уценка — × 0,27, потому что вечером",
        "товар ушёл бы со скидкой. Списание = штуки × цена × 0,33 (сгоревшая",
        "себестоимость). «Цена промаха» складывает ровно эти два числа. Вечерняя",
        "уценка в потери НЕ входит: уценённая единица продана и принесла прибыль",
        "(+27% цены против −33% у списанной) — это инструмент спасения остатка.",
        "",
        "ПРЕДОХРАНИТЕЛИ ОЦЕНКИ НЕДОПРОДАЖ (всё в сторону занижения):",
        "  · день с любым, даже дробным списанием — не дефицит;",
        "  · день с ВЕЧЕРНЕЙ уценкой — не дефицит (товар стоял на полке); дневная",
        "    скидка дефицит не снимает: это скидочная программа, а не распродажа;",
        "  · часовой профиль позиции включает и дефицитные дни (их вечер нулевой),",
        "    поэтому скорость занижена — оценка ближе к нижней границе;",
        f"  · порог значимости {p.signif_units:.0f} шт съедает "
        f"{s['lost_zeroed_units']:,.0f} шт длинного хвоста редких позиций — они",
        "    в итог не входят, но помечены строчной «д».",
        "",
        f"СРЫВЫ ПОСТАВКИ (отдельно от потерь заказа): {s['supply_gap_days']} точко-дней,",
        "где «заявка была, продаж ноль» сразу у 5 и более позиций — похоже на срыв",
        f"привоза или простой кассы; там могло быть продано ≈{s['supply_gap_units_est']:,.0f} шт.",
        "В деньги промаха это не включено: спрос тех дней неизвестен.",
        "",
        f"Параметры методики: профиль от {p.min_days} дней жизни пары; «рано» = раньше",
        f"чем за {p.early_gap_h:g} ч до закрытия (час закрытия — 95-й перцентиль",
        "последнего чека, свой на каждый месяц каждой точки); «вечерняя» уценка —",
        f"первая скидка не раньше чем за {p.markdown_evening_h:g} ч до закрытия.",
        "",
        "Списания включают питание сотрудников и порчу при приёмке — эта часть",
        "точным заказом не сокращается. Разделить можно будет после разметки типов",
        "актов; пока это оговорка ко всем рублям списаний.",
    ]})


def calendar_grid(df: pd.DataFrame, point: str, days: int) -> pd.DataFrame:
    d = df[df["canon_name"] == point].copy()
    if d.empty:
        return pd.DataFrame()
    last_days = sorted(d["trade_day"].unique())[-days:]
    d = d[d["trade_day"].isin(last_days)]

    def cell(r):
        if not r["wo_settled"] and r["outcome"] in ("кончилось рано",
                                                    "распродано под закрытие"):
            return "?"
        sym = SYM.get(r["outcome"], "")
        if r["outcome"] == "кончилось рано" and r["lost_units"] > 0:
            return f"Д{r['lost_units']:.0f}"
        if r["outcome"] == "остаток списан" and r["wo_units"] >= 1:
            return f"С{r['wo_units']:.0f}"
        return sym

    d["cell"] = d.apply(cell, axis=1)
    d["col"] = pd.to_datetime(d["trade_day"]).dt.strftime("%d.%m")
    head = d.groupby("sku_name").agg(**{
        "Продано": ("sold_units", "sum"),
        "Дней Д": ("lost_units", lambda x: (x > 0).sum()),
        "Недопродано ₽т": ("lost_rub_margin", lambda x: round(x.sum() / 1e3)),
        "Списано ₽т": ("wo_rub_cost", lambda x: round(x.sum() / 1e3)),
    }).round(0)
    grid = d.pivot_table(index="sku_name", columns="col", values="cell",
                         aggfunc="first", fill_value="")
    out = head.join(grid).reset_index().rename(columns={"sku_name": "Позиция"})
    return out.sort_values("Недопродано ₽т", ascending=False)


def to_excel(df: pd.DataFrame, path: Path, p: Params, days: int) -> None:
    s = summarize(df, p)
    sheets: dict[str, pd.DataFrame] = {
        "Как читать": how_to_read(p, s),
        "Сводка сети": net_summary(df),
        "По точкам": point_summary(df),
        "Позиции за год": pair_summary(df),
    }
    points = sorted(df["canon_name"].dropna().unique())

    with pd.ExcelWriter(path, engine="xlsxwriter") as xw:
        book = xw.book
        head_fmt = book.add_format({"bold": True, "bg_color": "#DDEBF7", "border": 1,
                                    "text_wrap": True, "valign": "top"})
        for name, sh in sheets.items():
            sh.to_excel(xw, sheet_name=name, index=False)
            ws = xw.sheets[name]
            for i, c in enumerate(sh.columns):
                width = 70 if name == "Как читать" else \
                        (36 if c in ("Позиция", "Точка") else 14)
                ws.set_column(i, i, width)
                ws.write(0, i, c, head_fmt)
            ws.freeze_panes(1, 1)
            if name == "Позиции за год":
                ws.autofilter(0, 0, len(sh), len(sh.columns) - 1)

        cell_fmts = {k: book.add_format({"bg_color": v, "align": "center",
                                         "font_size": 9})
                     for k, v in COLORS.items()}
        for pt in points:
            grid = calendar_grid(df, pt, days)
            if grid.empty:
                continue
            sheet = ("К " + pt)[:31]
            grid.to_excel(xw, sheet_name=sheet, index=False)
            ws = xw.sheets[sheet]
            ws.set_column(0, 0, 34)
            ws.set_column(1, 4, 11)
            ws.set_column(5, len(grid.columns), 5)
            for i, c in enumerate(grid.columns):
                ws.write(0, i, str(c), head_fmt)
            ws.freeze_panes(1, 5)
            # раскраска календарной части по первому символу ячейки
            for ri in range(len(grid)):
                for ci in range(5, len(grid.columns)):
                    val = grid.iloc[ri, ci]
                    if not isinstance(val, str) or not val:
                        continue
                    fmt = cell_fmts.get(val[0])
                    if fmt:
                        ws.write(ri + 1, ci, val, fmt)


def main() -> int:
    days = 60
    for a in sys.argv[1:]:
        if a.startswith("--days="):
            days = int(a.split("=", 1)[1])
    p = Params()
    con = connect_reader()
    df = filter_frame(
        load_or_build(con, p), "metric_scope", date_col="trade_day")
    path = OUTPUT / f"Календарь_позиций_{p.since:%Y-%m-%d}__{p.until:%Y-%m-%d}.xlsx"
    to_excel(df, path, p, days)
    s = summarize(df, p)
    print(f"промах за год: {s['miss_rub_m']:.1f} млн ₽ "
          f"(недодали {s['lost_rub_m']:.1f} + списали {s['wo_rub_m']:.1f})")
    print(f"→ {path}")
    con.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
