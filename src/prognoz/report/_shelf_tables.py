# -*- coding: utf-8 -*-
"""Сводные таблицы полки, общие для всех Excel-отчётов.

Единственное место, где живут формулы сводок и вердикты «что делать». Пока они
были внутри shelf_calendar, второй отчёт по точкам неизбежно завёл бы вторую
реализацию тех же колонок — и два файла клиенту с разными числами.
"""
from __future__ import annotations

import pandas as pd

# Пороги вердикта. Здесь, а не в params.py: это правило ЧТЕНИЯ отчёта человеком,
# а не параметр методики — на публикуемые числа не влияет.
#
# Откалиброваны по распределению зрелых пар за год (563 пары): дефицит — медиана
# 7% дней, 75-й перцентиль 14%; списания — медиана 26% дней, 75-й перцентиль 37%.
# Отсюда «высокий дефицит» = 12% дней (верхняя треть), «высокие списания» = 30%.
MIN_DAYS_VERDICT = 30
DEFICIT_HIGH = 12.0     # % дней с дефицитом — верхняя треть пар
DEFICIT_LOW = 5.0
WRITEOFF_HIGH = 30.0    # % дней со списанием — верхняя треть пар
WRITEOFF_LOW = 15.0


def net_summary(df: pd.DataFrame) -> pd.DataFrame:
    """Сеть по месяцам + строка ИТОГО."""
    s = df[df["wo_settled"]].copy()
    s["mon"] = pd.to_datetime(s["trade_day"]).dt.strftime("%Y-%m")
    g = s.groupby("mon").agg(**{
        "Продано, шт": ("sold_units", "sum"),
        "Недопродано, шт": ("lost_units", "sum"),
        "Недопродано, тыс ₽": ("lost_rub_margin", lambda x: x.sum() / 1e3),
        "Списано, шт": ("wo_units", "sum"),
        "Списано, тыс ₽": ("wo_rub_cost", lambda x: x.sum() / 1e3),
        "Уценено, шт (справочно)": ("u40_units", "sum"),
        "Дней дефицита": ("lost_units", lambda x: (x > 0).sum()),
    }).round(0).reset_index().rename(columns={"mon": "Месяц"})
    g["Цена промаха, тыс ₽"] = g["Недопродано, тыс ₽"] + g["Списано, тыс ₽"]
    itog = g.drop(columns="Месяц").sum().to_frame().T
    itog.insert(0, "Месяц", "ИТОГО")
    return pd.concat([g, itog], ignore_index=True)


def point_summary(df: pd.DataFrame) -> pd.DataFrame:
    """По точкам: обе потери, доля дефицитных дней, недозаказ в % спроса."""
    s = df[df["wo_settled"]]
    g = s.groupby("canon_name").agg(**{
        "Продано, шт": ("sold_units", "sum"),
        "Недопродано, шт": ("lost_units", "sum"),
        "Недопродано, млн ₽": ("lost_rub_margin", lambda x: round(x.sum() / 1e6, 2)),
        "Списано, шт": ("wo_units", "sum"),
        "Списано, млн ₽": ("wo_rub_cost", lambda x: round(x.sum() / 1e6, 2)),
        "Дней дефицита": ("lost_units", lambda x: (x > 0).sum()),
        "Дней всего": ("trade_day", "count"),
    }).reset_index().rename(columns={"canon_name": "Точка"})
    g["Цена промаха, млн ₽"] = (g["Недопродано, млн ₽"] + g["Списано, млн ₽"]).round(2)
    g["Дефицит, % дней"] = (100 * g["Дней дефицита"] / g["Дней всего"]).round(1)
    g["Недозаказ, % спроса"] = (
        100 * g["Недопродано, шт"] / (g["Продано, шт"] + g["Недопродано, шт"])
    ).round(1)
    return g.sort_values("Цена промаха, млн ₽", ascending=False)


def verdict(r: pd.Series) -> str:
    """Что делать с позицией. Читается человеком, не движком.

    Три разные болезни, и лечатся они по-разному:
      · «не хватает» — дефицит есть, списаний нет: поднять уровень заказа;
      · «лишнее» — списания есть, дефицита нет: снизить уровень;
      · «скачет» — И дефицит, И списания высокие: уровень в среднем верный, а
        промахи по дням в обе стороны. Поднимать уровень тут вредно — вырастут
        списания. Это работа для прогноза по дням, и именно на таких позициях
        сосредоточены самые большие деньги промаха (наборы, топ-шаурму).
    """
    if r["Дней в обороте"] < MIN_DAYS_VERDICT:
        return "мало данных"
    if r["Продано, шт"] == 0:
        return "не продаётся — вывести?"
    deficit, wo = r["Дефицит, % дней"], r["Списания, % дней"]
    if deficit >= DEFICIT_HIGH and wo >= WRITEOFF_LOW:
        return "скачет: то пусто, то густо — точнее по дням"
    if deficit >= DEFICIT_HIGH:
        return "систематически не хватает — поднять"
    if wo >= WRITEOFF_HIGH and deficit < DEFICIT_LOW:
        return "стабильно лишнее — снизить"
    return "в норме"


def pair_summary(df: pd.DataFrame) -> pd.DataFrame:
    """Строка = точка × позиция за весь период, с вердиктом."""
    s = df[df["wo_settled"]]
    g = s.groupby(["canon_name", "sku_name", "grp"]).agg(**{
        "Продано, шт": ("sold_units", "sum"),
        "Дней в обороте": ("trade_day", "nunique"),
        "Дней дефицита": ("lost_units", lambda x: (x > 0).sum()),
        "Недопродано, шт": ("lost_units", "sum"),
        "Недопродано, тыс ₽": ("lost_rub_margin", lambda x: x.sum() / 1e3),
        "Дней со списанием": ("had_writeoff", "sum"),
        "Списано, шт": ("wo_units", "sum"),
        "Списано, тыс ₽": ("wo_rub_cost", lambda x: x.sum() / 1e3),
        "Средний час затишья": ("t_last", "mean"),
    }).round(1).reset_index().rename(columns={
        "canon_name": "Точка", "sku_name": "Позиция", "grp": "Группа"})
    g["Цена промаха, тыс ₽"] = (g["Недопродано, тыс ₽"] + g["Списано, тыс ₽"]).round(1)
    g["Дефицит, % дней"] = (100 * g["Дней дефицита"] / g["Дней в обороте"]).round(0)
    g["Списания, % дней"] = (100 * g["Дней со списанием"] / g["Дней в обороте"]).round(0)
    g["Недозаказ, % спроса"] = (
        100 * g["Недопродано, шт"] / (g["Продано, шт"] + g["Недопродано, шт"])
    ).round(1)
    g["Что делать"] = g.apply(verdict, axis=1)
    return g.sort_values("Цена промаха, тыс ₽", ascending=False)


def deficit_history(df: pd.DataFrame) -> pd.DataFrame:
    """Помесячная история дефицитных дней и последние даты — для отчёта по точке."""
    s = df[df["wo_settled"] & (df["lost_units"] > 0)].copy()
    if s.empty:
        return pd.DataFrame(columns=["Точка", "Позиция"])
    s["mon"] = pd.to_datetime(s["trade_day"]).dt.strftime("%m.%y")
    piv = (s.pivot_table(index=["canon_name", "sku_name"], columns="mon",
                         values="lost_units", aggfunc=lambda x: (x > 0).sum(),
                         fill_value=0)
            .astype(int))
    piv = piv[sorted(piv.columns, key=lambda c: (c[3:], c[:2]))]
    last = (s.sort_values("trade_day")
             .groupby(["canon_name", "sku_name"])["trade_day"]
             .apply(lambda x: ", ".join(pd.to_datetime(d).strftime("%d.%m")
                                        for d in list(x)[-5:]))
             .rename("Последние дни дефицита"))
    out = piv.join(last).reset_index().rename(columns={
        "canon_name": "Точка", "sku_name": "Позиция"})
    return out
