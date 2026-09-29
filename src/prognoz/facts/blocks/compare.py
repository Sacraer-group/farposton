# -*- coding: utf-8 -*-
"""Блок «compare»: старый движок vs новый vs человек против восстановленного
спроса — ответ на вопрос заказчика «будет ли ПО экономить деньги».

Источник — ЗАМОРОЖЕННЫЙ прогон бэктеста ab_shelf_frozen.parquet (паттерн
ladder ← dryrun_frozen): обычные прогоны пишут ab_shelf_rows и якорей не рвут;
канон пересоздаётся только `py -m prognoz.backtest.ab_shelf --freeze` —
осознанным действием, после которого пересчитываются факты и якоря.
"""
from __future__ import annotations

import pandas as pd

from prognoz.assortment import filter_frame
from prognoz.facts.registry import block

SRC = "facts.blocks.compare:compute"

METHOD = (
    "эталон дня — восстановленный спрос полки (продано + недопродано), а не "
    "продажи: продажи обрезаны привозом и повторяют чужой недозаказ; метрика "
    "симметрична для всех трёх заявок: лишнее ×0,33 цены + недовезённое ×0,67; "
    "множество — строки, где сотрудник заказывал; исключены точко-дни без "
    "отчёта, срывы поставки и дни без актов списания; прогнозы построены "
    "строго на данных до целевой даты, оценка дня — задним числом (законно: "
    "оцениваем заявку, а не прогнозируем)"
)


def _clean(df: pd.DataFrame) -> pd.DataFrame:
    return df[(df["human"] > 0) & ~df["supply_gap"] & df["wo_settled"]
              & ~df["no_report"]]


@block("compare")
def compute(ctx, reg) -> None:
    # Лениво: ab_shelf тянет prod-движок, на уровне модуля это петля импортов
    from prognoz.backtest.ab_shelf import OUT_FROZEN, money   # noqa: PLC0415
    if not OUT_FROZEN.exists():
        print("  compare: ab_shelf_frozen.parquet нет — блок пропущен "
              "(создать: py -m prognoz.backtest.ab_shelf --freeze)")
        return
    df = filter_frame(
        pd.read_parquet(OUT_FROZEN), "metric_scope",
        point_col="_point_key", sku_col="_sku_key", date_col="date")
    ok = _clean(df)
    if ok.empty:
        print("  compare: после ассортиментного фильтра нет строк для оценки")
        return
    n_days = ok["date"].nunique()
    period = (f"{pd.to_datetime(ok['date']).min():%d.%m}–"
              f"{pd.to_datetime(ok['date']).max():%d.%m.%Y}")

    variants = [("human", "человек"), ("old", "движок как есть"),
                ("new", "движок + восст. спрос")]
    if "feed" in ok.columns:
        variants.append(("feed", "движок на спросе (эксперимент)"))
    vals = {}
    for name, _ru in variants:
        over, under = money(ok, name)
        vals[name] = (over, under)

    saving_k = (sum(vals["human"]) - sum(vals["new"])) / 1e3
    facts = [
        {"id": "compare.window.n_days", "value": int(n_days), "unit": "дн.",
         "title": "окно бэктеста против восстановленного спроса",
         "period": period, "method": METHOD},
        {"id": "compare.window.n_rows", "value": int(len(ok)), "unit": "строк",
         "title": "строк решений сотрудника в оценке", "period": period},
        {"id": "compare.window.saving_new_vs_human_k",
         "value": round(saving_k), "unit": "тыс ₽",
         "title": "экономия нового движка против человека за окно "
                  "(минус = человек дешевле)", "period": period,
         "method": METHOD},
        {"id": "compare.year.saving_linear_m",
         "value": round(saving_k * 365 / n_days / 1e3, 1), "unit": "млн ₽/год",
         "title": "линейная экстраполяция экономии на год (ОЦЕНКА)",
         "period": period,
         "method": "×365/n_days; оговорки: летнее окно, состав точек года "
                   "иной, спрос — нижняя граница полки"},
    ]
    if "feed" in vals:
        saving_feed_k = (sum(vals["human"]) - sum(vals["feed"])) / 1e3
        facts.append(
            {"id": "compare.window.saving_feed_vs_human_k",
             "value": round(saving_feed_k), "unit": "тыс ₽",
             "title": "экономия «движка на спросе» против человека за окно "
                      "(эксперимент, в прод не включён)", "period": period,
             "method": METHOD})
    for name, ru in variants:
        over, under = vals[name]
        facts += [
            {"id": f"compare.window.{name}_over_k", "value": round(over / 1e3),
             "unit": "тыс ₽", "title": f"{ru}: себестоимость лишнего (×0,33)",
             "period": period},
            {"id": f"compare.window.{name}_under_k", "value": round(under / 1e3),
             "unit": "тыс ₽", "title": f"{ru}: маржа недовезённого (×0,67)",
             "period": period},
            {"id": f"compare.window.{name}_miss_k",
             "value": round((over + under) / 1e3), "unit": "тыс ₽",
             "title": f"{ru}: цена промаха за окно", "period": period,
             "method": METHOD},
        ]

    cols = [name for name, _ru in variants]
    by_point = []
    for pt, g in ok.groupby("canon_name"):
        row = {"point": pt, "rows": len(g)}
        for name in cols:
            o, u = money(g, name)
            row[f"{name}_miss_k"] = round((o + u) / 1e3, 1)
        by_point.append(row)
    reg.table("compare.by_point", pd.DataFrame(by_point),
              title="Цена промаха по точкам за окно, тыс ₽")

    ok2 = ok.copy()
    ok2["week"] = pd.to_datetime(ok2["date"]).dt.strftime("%V")
    by_week = []
    for w, g in ok2.groupby("week"):
        row = {"week": w, "days": g["date"].nunique()}
        for name in cols:
            o, u = money(g, name)
            row[f"{name}_miss_k"] = round((o + u) / 1e3, 1)
        by_week.append(row)
    reg.table("compare.by_week", pd.DataFrame(by_week),
              title="Цена промаха по неделям, тыс ₽")

    reg.add_many("compare", SRC, facts)
