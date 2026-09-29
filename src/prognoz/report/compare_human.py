# -*- coding: utf-8 -*-
"""Модель против человека на реальном дне: позиция за позицией + свод по точкам.

Три числа в одной строке: что предложила модель, что заказал сотрудник, сколько
продали. Дальше — кто из двоих оказался ближе к факту.

⚠️ Три ловушки, из-за которых наивное сравнение врёт. Отчёт разводит их по разным
множествам, а не смешивает в одну среднюю:

1. **Продажи упираются в привоз сотрудника.** Возили по его заявке, поэтому его
   недозаказ почти невидим (продали ровно сколько привёз — ошибка «ноль»), а
   перезаказ виден полностью. Сравнение по этой причине к нему благосклоннее,
   чем к нам, а не наоборот.
2. **Часть позиций приезжает вне заявки.** На Точки 1 шаурму, шаурмаы,
   хот-доги приходят внутри наборов и разузловываются в чеке: заявки на них нет,
   продажи есть. Там «заявка 0 при продажах 200» — не промах сотрудника, а другой
   канал поставки. Такие строки помечены и вынесены из основного счёта.
3. **Позиции, где кто-то один не участвовал**, дают другому фиктивную победу.
   Основное сравнение — только парные строки, где ненулевые оба.

Запуск: PYTHONUTF8=1 PYTHONPATH=prognoz/src py -m prognoz.report.compare_human 2026-08-03
"""
from __future__ import annotations

import sys
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

SRC = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(SRC))
sys.path.insert(0, str(SRC.parents[0] / "analysis" / "etl"))
from _db import OUTPUT, connect_reader                                      # noqa: E402
from prognoz.assortment import (filter_frame,                               # noqa: E402
                                sql_exclusion_predicate)
from prognoz.facts.blocks.economics import avg_price_gross, unit_costs      # noqa: E402
from prognoz.predict.reconcile import FC, LOG_DIR, facts_for_day, scope_skus  # noqa: E402
from prognoz.scope import canon_cte                                          # noqa: E402

WD = ("понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье")
NO_CHANNEL_DAYS = 60


def no_channel_pairs(con, upto: date) -> set[tuple[str, str]]:
    """Пары «точка × позиция», по которым за 60 дней не было ни одной заявки."""
    df = con.execute(f"""
        WITH {canon_cte()}
        SELECT c.pt AS pt, o.sku_key AS sk, sum(o.qty_ordered) AS q
        FROM mart.fact_orders_daily o
        JOIN c ON c.point_key = o.point_key
        WHERE o.channel = 'transfer' AND o.trade_date BETWEEN ? AND ?
          AND {sql_exclusion_predicate(
              "factory_order", point_expr="c.pt", sku_expr="o.sku_key",
              date_expr="o.trade_date")}
        GROUP BY 1, 2 HAVING sum(o.qty_ordered) > 0
    """, [upto - timedelta(days=NO_CHANNEL_DAYS), upto]).fetchdf()
    return set(zip(df["pt"], df["sk"]))


def order_frequency(con, upto: date, days: int = 30) -> pd.DataFrame:
    """Сколько дней из последних 30 позицию вообще заказывали.

    Нужно, чтобы не считать недозаказом то, что им не является: позицию с длинным
    сроком (нарезка лосося, Ready to cook) заказывают через день, и в день без
    заявки она продаётся С ОСТАТКА прошлого привоза. Сравнивать «заявка сегодня»
    с «продажи сегодня» у такой позиции бессмысленно."""
    return con.execute(f"""
        WITH {canon_cte()}
        SELECT c.pt AS _point_key, o.sku_key AS _sku_key,
               count(DISTINCT o.trade_date) AS "дней с заявкой из 30"
        FROM mart.fact_orders_daily o
        JOIN c ON c.point_key = o.point_key
        WHERE o.channel = 'transfer' AND o.trade_date BETWEEN ? AND ?
          AND {sql_exclusion_predicate(
              "factory_order", point_expr="c.pt", sku_expr="o.sku_key",
              date_expr="o.trade_date")}
        GROUP BY 1, 2
    """, [upto - timedelta(days=days), upto]).fetchdf()


def collect(day: date, con) -> pd.DataFrame:
    src = LOG_DIR / f"{day:%Y-%m-%d}.parquet"
    if not src.exists():
        raise SystemExit(f"нет журнала прогноза за {day:%d.%m.%Y}")
    keep = scope_skus(con)
    fc = pd.read_parquet(src)
    fc = fc.copy()
    fc["_metric_date"] = day
    fc = filter_frame(
        fc, "factory_order", point_col="_point_key", sku_col="_sku_key",
        date_col="_metric_date")
    fc = fc[fc["_sku_key"].isin(keep)]
    m = fc.merge(facts_for_day(con, day), on=["_point_key", "_sku_key"], how="outer")
    for c in (FC, "actual", "u40", "human"):
        m[c] = m[c].fillna(0)

    names = con.execute("SELECT point_key AS _point_key, canon_name FROM mart.dim_point "
                        "WHERE canon_name IS NOT NULL").fetchdf()
    sku = con.execute("SELECT sku_key AS _sku_key, name AS Позиция_, group_path "
                      "FROM mart.dim_sku").fetchdf()
    m = m.drop(columns=["Точка"], errors="ignore").merge(names, on="_point_key", how="left")
    m = m.merge(sku, on="_sku_key", how="left")
    m["_metric_date"] = day
    m = filter_frame(
        m, "metric_scope", point_col="_point_key", sku_col="_sku_key",
        date_col="_metric_date")
    m["Позиция"] = m["Позиция"].fillna(m["Позиция_"])
    m["Группа"] = m["Группа"].fillna(
        m["group_path"].fillna("").str.split(" / ").str[-1]).replace("", "вне расчёта")

    has_ch = no_channel_pairs(con, day)
    m["возится с фабрики"] = [(p, s) in has_ch for p, s in zip(m["_point_key"], m["_sku_key"])]

    m = m.merge(order_frequency(con, day), on=["_point_key", "_sku_key"], how="left")
    m["дней с заявкой из 30"] = m["дней с заявкой из 30"].fillna(0).astype(int)
    # Заявки сегодня нет, но позицию регулярно возят → продано с прошлого привоза.
    # Записывать это сотруднику в недозаказ нельзя: он заказал вчера.
    m["продано с остатка"] = (m["human"] == 0) & (m["actual"] > 0) & \
                             (m["дней с заявкой из 30"] > 0)

    m["ошибка модели"] = m[FC] - m["actual"]
    m["ошибка человека"] = m["human"] - m["actual"]
    m["ближе"] = "ничья"
    am, ah = m["ошибка модели"].abs(), m["ошибка человека"].abs()
    m.loc[am < ah, "ближе"] = "модель"
    m.loc[ah < am, "ближе"] = "человек"
    # распродано без уценки → факт это нижняя граница спроса
    m["распродано подчистую"] = (m["u40"] < 0.5) & (m["actual"] > 0)
    return m


def wape(g: pd.DataFrame, col: str) -> float:
    a = g["actual"].sum()
    return round(100 * (g[col] - g["actual"]).abs().sum() / a, 1) if a else float("nan")


def bias(g: pd.DataFrame, col: str) -> float:
    a = g["actual"].sum()
    return round(100 * (g[col] - g["actual"]).sum() / a, 1) if a else float("nan")


def money(g: pd.DataFrame, col: str, avg_price: float) -> float:
    """Цена ошибки за день, ₽. Лишняя единица стоит списанной себестоимости (0,33 цены),
    недостающая — потерянной маржи (0,67). Построчно, без сальдирования: излишек
    шаурмы не закрывает дефицит шаурмы."""
    co, cu = unit_costs()
    e = g[col] - g["actual"]
    return round((e[e > 0].sum() * co + (-e[e < 0].sum()) * cu) * avg_price)


def by_point(df: pd.DataFrame, avg_price: float) -> pd.DataFrame:
    rows = []
    for pt, g in df.groupby("canon_name"):
        em, eh = g[FC] - g["actual"], g["human"] - g["actual"]
        rows.append({
            "Точка": pt, "Позиций": len(g),
            "Прогноз модели": round(g[FC].sum()),
            "Заявка человека": round(g["human"].sum()),
            "Продано": round(g["actual"].sum()),
            "Перекос модели, %": bias(g, FC),
            "Перекос человека, %": bias(g, "human"),
            "Ошибка модели, %": wape(g, FC),
            "Ошибка человека, %": wape(g, "human"),
            "Лишнего у модели, шт": round(em[em > 0].sum()),
            "Лишнего у человека, шт": round(eh[eh > 0].sum()),
            "Недодали: модель, шт": round(-em[em < 0].sum()),
            "Недодали: человек, шт": round(-eh[eh < 0].sum()),
            "Цена ошибки модели, ₽": money(g, FC, avg_price),
            "Цена ошибки человека, ₽": money(g, "human", avg_price),
            "Строк: модель ближе": int((g["ближе"] == "модель").sum()),
            "Строк: человек ближе": int((g["ближе"] == "человек").sum()),
            "Строк: ничья": int((g["ближе"] == "ничья").sum()),
        })
    out = pd.DataFrame(rows).sort_values("Продано", ascending=False)
    itog = {"Точка": "ИТОГО", "Позиций": out["Позиций"].sum()}
    for c in out.columns[2:]:
        itog[c] = out[c].sum() if "%" not in c else None
    g = df
    itog["Перекос модели, %"] = bias(g, FC)
    itog["Перекос человека, %"] = bias(g, "human")
    itog["Ошибка модели, %"] = wape(g, FC)
    itog["Ошибка человека, %"] = wape(g, "human")
    return pd.concat([out, pd.DataFrame([itog])], ignore_index=True)


def datasets(df: pd.DataFrame) -> tuple[dict[str, pd.DataFrame], list[str]]:
    """Три множества, отвечающие на три разных вопроса — их нельзя смешивать."""
    live = df[df["canon_name"].notna()].copy()
    alive = live.groupby("canon_name")["actual"].sum()
    dead = list(alive[alive == 0].index)
    live = live[~live["canon_name"].isin(dead)]
    return {
        # всё, что продавалось: где одна сторона не участвовала, ей стоит ноль
        "Все позиции с продажами": live,
        # только то, что вообще возится с фабрики: «не заказал» здесь — решение,
        # а не отсутствие канала поставки
        "Только то, что возят с фабрики": live[live["возится с фабрики"]],
        # ...и без строк, где товар продавался с остатка прошлого привоза:
        # заявки в этот день нет не потому, что человек ошибся, а потому что
        # позицию заказывают через день (замечание сотрудника 04.08)
        "Возят с фабрики, без продаж с остатка":
            live[live["возится с фабрики"] & ~live["продано с остатка"]],
        # оба назвали своё число — сравнение решений, а не участия
        "Оба заказывали (решение против решения)":
            live[(live["human"] > 0) & (live[FC] > 0)],
    }, dead


def summary_sheets(df: pd.DataFrame, day: date, avg_price: float) -> dict[str, pd.DataFrame]:
    sets, dead = datasets(df)
    rows = []
    for label, g in sets.items():
        rows.append({
            "Множество": label, "Строк": len(g),
            "Прогноз модели": round(g[FC].sum()),
            "Заявка человека": round(g["human"].sum()),
            "Продано": round(g["actual"].sum()),
            "Перекос модели, %": bias(g, FC),
            "Перекос человека, %": bias(g, "human"),
            "Ошибка модели, %": wape(g, FC),
            "Ошибка человека, %": wape(g, "human"),
            "Цена ошибки модели, ₽": money(g, FC, avg_price),
            "Цена ошибки человека, ₽": money(g, "human", avg_price),
            "Модель ближе, строк": int((g["ближе"] == "модель").sum()),
            "Человек ближе, строк": int((g["ближе"] == "человек").sum()),
        })
    total = pd.DataFrame(rows)

    notes = pd.DataFrame({"Пояснение": [
        f"Дата: {day:%d.%m.%Y}, {WD[day.weekday()]}.",
        "«Прогноз модели» — то, что реально ушло утром в теневом режиме, а не пересчёт "
        "задним числом.",
        "«Заявка человека» — заказ на перемещение с фабрики с датой поступления на эту "
        "дату (то есть привоз этого дня).",
        "«Продано» — продажи за день по данным 1С, за вычетом возвратов.",
        "",
        "ЧТО ЗНАЧАТ ТРИ МНОЖЕСТВА:",
        "1) Все позиции с продажами — общая картина. Там, где одна сторона не "
        "участвовала, ей засчитан ноль, поэтому цифра завышает разницу.",
        "2) Только то, что возят с фабрики — без позиций, которые приезжают вне "
        "заявки (на Точки 1 наборы разузловываются в чеке: заявки нет, "
        "продажи есть).",
        "3) Возят с фабрики, без продаж с остатка — дополнительно убраны строки, где "
        "заявки в этот день не было, но позицию регулярно возят: там товар продавался "
        "с прошлого привоза (нарезка лосося и другие позиции с длинным сроком "
        "заказываются через день). Записывать такое сотруднику в недозаказ нельзя.",
        "4) Оба заказывали — самое чистое: обе стороны назвали своё число. "
        "Основной вывод строится по нему.",
        "",
        "ГЛАВНАЯ ОГОВОРКА: продажи ограничены тем, что привёз сотрудник. Его недозаказ "
        "почти не виден (продали ровно сколько было), перезаказ виден целиком. "
        "Сравнение поэтому к нему благосклоннее, чем к модели.",
        "",
        f"Точки без документа продаж за день (исключены): {', '.join(dead) if dead else 'нет'}.",
        "«Распродано подчистую» — товар кончился без вечерней уценки: настоящий спрос "
        "был выше проданного, промах вниз там недооценён у обоих.",
        "",
        f"«Цена ошибки» считается по средней цене {avg_price:.0f} ₽: лишняя единица "
        "стоит списанной себестоимости (0,33 цены), недостающая — потерянной маржи "
        "(0,67 цены). Построчно, без взаимозачёта между позициями. Это оценка "
        "порядка величины, а не бухгалтерский расчёт: цена одна на все позиции.",
        "",
        "Лист «История 28 дней» — тот же счёт на окне 25.06–22.07, чтобы вывод не "
        "строился на одном дне. Там прогноз пересчитан задним числом на сегодняшнем "
        "коде (теневой режим идёт только с 03.08), а задним числом всегда выходит "
        "немного красивее, чем в реальном выпуске.",
    ]})
    return {"Кто точнее": total, "Как читать": notes}


def window_history(avg_price: float) -> pd.DataFrame | None:
    """Тот же счёт на 28 датах окна протокола — чтобы вывод не строился на одном дне.

    ⚠️ Это СУХОЙ ПРОГОН: прогноз пересчитан задним числом на текущем коде, а не
    выпущенные файлы (теневой режим идёт только с 03.08). Задним числом всегда
    получается чуть красивее.
    """
    src = OUTPUT / "dryrun_frozen.parquet"
    if not src.exists():
        return None
    fr = filter_frame(
        pd.read_parquet(src), "metric_scope",
        point_col="_point_key", sku_col="_sku_key", date_col="date")
    fr["ошибка модели"] = fr[FC] - fr["actual"]
    fr["ошибка человека"] = fr["human"] - fr["actual"]
    am, ah = fr["ошибка модели"].abs(), fr["ошибка человека"].abs()
    fr["ближе"] = "ничья"
    fr.loc[am < ah, "ближе"] = "модель"
    fr.loc[ah < am, "ближе"] = "человек"

    paired = fr[(fr["human"] > 0) & (fr[FC] > 0)]
    observed = paired[paired["u40"] >= 0.5]
    rows = []
    for label, g in (("все строки", fr), ("оба заказывали", paired),
                     ("оба + спрос наблюдаем (была уценка)", observed)):
        days = g["date"].nunique()
        rows.append({
            "Множество": label, "Строк": len(g), "Дней": days,
            "Прогноз модели": round(g[FC].sum()),
            "Заявка человека": round(g["human"].sum()),
            "Продано": round(g["actual"].sum()),
            "Перекос модели, %": bias(g, FC),
            "Перекос человека, %": bias(g, "human"),
            "Ошибка модели, %": wape(g, FC),
            "Ошибка человека, %": wape(g, "human"),
            "Цена ошибки модели, ₽/день": round(money(g, FC, avg_price) / days),
            "Цена ошибки человека, ₽/день": round(money(g, "human", avg_price) / days),
            "Модель ближе, строк": int((g["ближе"] == "модель").sum()),
            "Человек ближе, строк": int((g["ближе"] == "человек").sum()),
        })
    return pd.DataFrame(rows)


def to_excel(df: pd.DataFrame, day: date, path: Path, avg_price: float) -> None:
    cols = ["Позиция", "Группа", FC, "human", "actual", "ошибка модели",
            "ошибка человека", "ближе", "возится с фабрики", "дней с заявкой из 30",
            "продано с остатка", "распродано подчистую",
            "Продано за 28 дн", "Уценено за 28 дн, %"]
    ru = {FC: "Прогноз модели", "human": "Заявка человека", "actual": "Продано"}
    sets, _ = datasets(df)
    live = sets["Все позиции с продажами"]
    sheets = summary_sheets(df, day, avg_price)
    sheets["По точкам"] = by_point(sets["Возят с фабрики, без продаж с остатка"], avg_price)
    sheets["По точкам, оба заказывали"] = by_point(
        sets["Оба заказывали (решение против решения)"], avg_price)
    hist = window_history(avg_price)
    order = ["Кто точнее", "По точкам", "По точкам, оба заказывали"]
    if hist is not None:
        sheets["История 28 дней"] = hist
        order.append("История 28 дней")
    order.append("Как читать")
    sheets = {k: sheets[k] for k in order}

    with pd.ExcelWriter(path, engine="xlsxwriter") as xw:
        book = xw.book
        head = book.add_format({"bold": True, "bg_color": "#DDEBF7", "border": 1,
                                "text_wrap": True, "valign": "top"})
        win_m = book.add_format({"bg_color": "#E2EFDA"})   # модель ближе
        win_h = book.add_format({"bg_color": "#FCE4D6"})   # человек ближе
        num = book.add_format({"num_format": "0"})

        for name, s in sheets.items():
            s.to_excel(xw, sheet_name=name, index=False)
            ws = xw.sheets[name]
            for i, c in enumerate(s.columns):
                ws.set_column(i, i, max(14, min(52, int(s[c].astype(str).str.len().max() or 14) + 2)))
                ws.write(0, i, c, head)
            ws.freeze_panes(1, 1)

        for pt, g in live.groupby("canon_name"):
            g = g[cols].rename(columns=ru).sort_values(
                ["Группа", "Продано"], ascending=[True, False])
            sheet = pt[:31]
            g.to_excel(xw, sheet_name=sheet, index=False)
            ws = xw.sheets[sheet]
            for i, c in enumerate(g.columns):
                width = 40 if c == "Позиция" else (16 if g[c].dtype == object else 13)
                ws.set_column(i, i, width, num if c in ("Прогноз модели", "Заявка человека",
                                                        "Продано") else None)
                ws.write(0, i, c, head)
            ws.freeze_panes(1, 1)
            ws.autofilter(0, 0, len(g), len(g.columns) - 1)
            col_close = g.columns.get_loc("ближе")
            ws.conditional_format(1, 0, len(g), len(g.columns) - 1, {
                "type": "formula", "format": win_m,
                "criteria": f'=${chr(65+col_close)}2="модель"'})
            ws.conditional_format(1, 0, len(g), len(g.columns) - 1, {
                "type": "formula", "format": win_h,
                "criteria": f'=${chr(65+col_close)}2="человек"'})


def main() -> int:
    days = [date.fromisoformat(a) for a in sys.argv[1:] if not a.startswith("-")]
    if not days:
        raise SystemExit("укажите дату: py -m prognoz.report.compare_human 2026-08-03")
    con = connect_reader()
    for day in days:
        df = collect(day, con)
        # цена берётся за месяц до дня сверки — окно закрыто с обеих сторон,
        # иначе число поедет с каждым ночным ETL
        price = avg_price_gross(con, day - timedelta(days=30), day)
        path = OUTPUT / f"Модель_против_человека_{day:%Y-%m-%d}.xlsx"
        to_excel(df, day, path, price)
        print(f"\n=== {day:%d.%m.%Y}, {WD[day.weekday()]} · средняя цена {price:.0f} ₽ ===")
        print(summary_sheets(df, day, price)["Кто точнее"].to_string(index=False))
        sets, _ = datasets(df)
        print("\nПО ТОЧКАМ (возят с фабрики, без продаж с остатка):")
        print(by_point(sets["Возят с фабрики, без продаж с остатка"],
                       price).to_string(index=False))
        print(f"\n→ {path}")
    con.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
