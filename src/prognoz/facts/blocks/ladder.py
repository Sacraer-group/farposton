# -*- coding: utf-8 -*-
"""Лестница точности — единственная реализация в проекте.

Функции wape/bias/уровни раньше жили в predict/dryrun.py и печатались в stdout,
а числа переносились в документы руками — так лестница разъехалась на три
несовместимые версии. Теперь dryrun импортирует расчёт отсюда, а публикуемые
числа рождаются здесь же как факты.

Два семейства:
  ladder.*         — каноническое окно протокола (dryrun_frozen.parquet), на него
                     заякорены документы;
  ladder.rolling.* — последний скользящий прогон (dryrun_rows.parquet), живёт
                     на панели показателей.
"""
from __future__ import annotations

import pandas as pd

from prognoz.facts.registry import block

FLOOR_COEF = 79.8      # E|X−Λ|/Λ ≈ 79,8/√Λ % для пуассоновского спроса

LEVELS = {
    "sku_day": ("точка × позиция × день", ["date", "_point_key", "_sku_key"]),
    "group_day": ("точка × группа × день", ["date", "_point_key", "cat"]),
    "point_day": ("точка × день", ["date", "_point_key"]),
    "net_day": ("сеть × день", ["date"]),
}

SRC = "facts.blocks.ladder:compute"


def wape(g: pd.DataFrame, col: str = "Прогноз, шт") -> float:
    a = g["actual"].sum()
    return 100 * (g[col] - g["actual"]).abs().sum() / a if a else float("nan")


def bias(g: pd.DataFrame, col: str = "Прогноз, шт") -> float:
    a = g["actual"].sum()
    return 100 * (g[col] - g["actual"]).sum() / a if a else float("nan")


def drop_dead_days(rows: pd.DataFrame) -> tuple[pd.DataFrame, int]:
    """Дни-точки без единой продажи — дыра в данных (нет отчёта), а не ошибка модели."""
    day_pt = rows.groupby(["date", "canon_name"])["actual"].sum()
    dead = day_pt[day_pt == 0]
    if not len(dead):
        return rows, 0
    keep = day_pt[day_pt > 0].index
    out = rows.set_index(["date", "canon_name"]).loc[keep].reset_index()
    return out, len(dead)


def paired(rows: pd.DataFrame) -> pd.DataFrame:
    """Честное множество сравнения: только строки, которые сотрудник заказывал.
    Там, где он не заказывал, ему проставился бы ноль и засчитался промах —
    ровно то подтасовывание, из-за которого вышло «модель вдвое точнее»."""
    return rows[rows["human"] > 0]


def ladder_table(rows: pd.DataFrame) -> pd.DataFrame:
    if "cat" not in rows.columns:
        rows = rows.assign(cat=rows.get("Группа", pd.Series(dtype=str)).fillna("?"))
    out = []
    for key, (title, keys) in LEVELS.items():
        g = rows.groupby(keys, dropna=False)[["Прогноз, шт", "actual", "human"]] \
                .sum().reset_index()
        lam = g["actual"].mean()
        out.append({
            "level": key, "уровень": title,
            "floor": round(FLOOR_COEF / lam ** 0.5, 1) if lam > 0 else None,
            "model_wape": round(wape(g), 1), "model_bias": round(bias(g), 1),
            "human_wape": round(wape(g, "human"), 1),
            "human_bias": round(bias(g, "human"), 1),
        })
    return pd.DataFrame(out)


def coverage(rows: pd.DataFrame) -> pd.DataFrame:
    tot = rows["actual"].sum()
    parts = {
        "оба заказали": rows[(rows["human"] > 0) & (rows["Прогноз, шт"] > 0)],
        "только модель": rows[(rows["human"] == 0) & (rows["Прогноз, шт"] > 0)],
        "только человек": rows[(rows["human"] > 0) & (rows["Прогноз, шт"] == 0)],
    }
    return pd.DataFrame([
        {"кто": k, "строк": len(v), "факт_шт": round(v["actual"].sum()),
         "доля_факта_%": round(100 * v["actual"].sum() / tot, 1) if tot else 0}
        for k, v in parts.items()])


def observed_only(rows: pd.DataFrame) -> pd.DataFrame:
    """Строки, где спрос наблюдаем точно: была уценка → товар дожил до вечера."""
    return rows[rows["u40"] >= 0.5]


def _emit(reg, rows: pd.DataFrame, prefix: str, period: str) -> None:
    rows, n_dead = drop_dead_days(rows)
    p = paired(rows)
    lt = ladder_table(p)
    reg.table(f"{prefix}.table", lt, title=f"Лестница точности ({period})")
    facts = []
    for r in lt.itertuples():
        for name, val in (("floor", r.floor), ("model_wape", r.model_wape),
                          ("model_bias", r.model_bias), ("human_wape", r.human_wape),
                          ("human_bias", r.human_bias)):
            facts.append({"id": f"{prefix}.{r.level}.{name}", "value": val,
                          "unit": "%", "title": f"{r.уровень}: {name}",
                          "period": period})
    exact = float((p["human"] == p["actual"]).mean())
    facts += [
        {"id": f"{prefix}.meta.n_dates", "value": int(p["date"].nunique()),
         "unit": "дат", "title": "дат в прогоне", "period": period},
        {"id": f"{prefix}.meta.n_rows_paired", "value": len(p),
         "unit": "строк", "title": "парных строк (сотрудник заказывал)",
         "period": period},
        {"id": f"{prefix}.meta.n_dead_days", "value": n_dead, "unit": "дн.-точек",
         "title": "исключено дней-точек без отчёта о продажах", "period": period},
        # Ключевая оговорка ко ВСЕЙ лестнице: продажи = min(спрос, привоз), привоз —
        # по заявке сотрудника. Там, где полка вычищена, его ошибка обнуляется
        # механически. При пуассоновском спросе λ≈4 точных совпадений было бы ~20%.
        {"id": f"{prefix}.meta.exact_match_share", "value": round(100 * exact, 1),
         "unit": "%", "title": "доля строк, где заявка сотрудника точно равна продажам",
         "method": "продажи обрезаны его же привозом → сравнение с человеком смещено "
                   "в его пользу; честное сравнение возможно только по исходам пилота",
         "period": period},
    ]
    cov = coverage(rows)
    reg.table(f"{prefix}.coverage", cov, title=f"Покрытие ассортимента ({period})")
    mo = cov[cov["кто"] == "только модель"].iloc[0]
    facts.append({"id": f"{prefix}.coverage.model_only_share",
                  "value": float(mo["доля_факта_%"]), "unit": "%",
                  "title": "доля продаж по позициям, которые человек не заказывал",
                  "period": period})
    obs = observed_only(p)
    if len(obs):
        g = obs.groupby(["date", "_point_key", "_sku_key"])[
            ["Прогноз, шт", "actual", "human"]].sum().reset_index()
        facts += [
            {"id": f"{prefix}.observed.model_wape", "value": round(wape(g), 1),
             "unit": "%", "title": "WAPE модели на строках с наблюдаемым спросом",
             "period": period},
            {"id": f"{prefix}.observed.human_wape", "value": round(wape(g, "human"), 1),
             "unit": "%", "title": "WAPE человека на строках с наблюдаемым спросом",
             "period": period},
        ]
    reg.add_many("ladder", SRC, [
        {**f, "method": f.get("method",
            "WAPE=Σ|прогноз−факт|/Σфакт; bias со знаком; пол 79,8/√Λ; только строки "
            "с заявкой сотрудника. ⚠ ЦЕНЗУРА: продажи обрезаны привозом по его же "
            "заявке — сравнение смещено в пользу человека, см. meta.exact_match_share")}
        for f in facts])


@block("ladder")
def compute(ctx, reg) -> None:
    if ctx.dryrun_frozen is not None:
        period = ctx.data_stamp.get("frozen_period", "")
        _emit(reg, ctx.dryrun_frozen, "ladder", f"окно протокола {period}")
    if ctx.dryrun is not None:
        period = ctx.data_stamp.get("dryrun_period", "")
        _emit(reg, ctx.dryrun, "ladder.rolling", f"скользящее {period}")
