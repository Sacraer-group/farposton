# -*- coding: utf-8 -*-
"""Пересчёт календарных индексов из витрины — сверка с константами прод-модели.

Константы в external/calendar_effects.py (1 января ×0,19, 8 марта ×1,56, …)
были получены разовым анализом, скрипт которого не сохранился. Этот блок
воспроизводит методику и еженедельно сверяет пересчёт с константами:
расхождение больше 0,05 — сигнал, что данные уехали от таблицы.

Методика (та же, что в исходном анализе): дневной объём по сопоставимым точкам
делится на центрированную скользящую медиану за 29 дней (снимает тренд и сезон),
затем на медианную норму своего дня недели (по обычным дням). Остаток — чистый
эффект даты.
"""
from __future__ import annotations

import pandas as pd

from prognoz.assortment import sql_exclusion_predicate
from prognoz.external.calendar_effects import DISPUTED, FIXED_DATES
from prognoz.external.calendar_ru import load as cal_load
from prognoz.facts.registry import block
from prognoz.scope import (COMPARABLE_FULL_PERIOD, GROUP_LIKE, GROUP_NOT_LIKE,
                           GROUP_NOT_LIKE_BAR, canon_cte)

SRC = "facts.blocks.calendar_check:compute"
TOLERANCE = 0.05


def daily_index(con, window: int = 29) -> pd.DataFrame:
    pts = ", ".join(f"'{p}'" for p in COMPARABLE_FULL_PERIOD)
    d = con.execute(f"""
        WITH {canon_cte()}
        SELECT x.sale_date AS date, sum(x.qty_sold - x.qty_returned) AS qty
        FROM mart.fact_sales_daily x
        JOIN c ON c.point_key = x.point_key
        JOIN mart.dim_point nm ON nm.point_key = c.pt
        JOIN mart.dim_sku s ON s.sku_key = x.sku_key
        WHERE s.group_path LIKE ? AND s.group_path NOT LIKE ?
          AND s.group_path NOT LIKE ? AND nm.canon_name IN ({pts})
          AND {sql_exclusion_predicate(
              "metric_scope", point_expr="c.pt", sku_expr="x.sku_key",
              date_expr="x.sale_date")}
        GROUP BY 1 ORDER BY 1
    """, [GROUP_LIKE, GROUP_NOT_LIKE, GROUP_NOT_LIKE_BAR]).fetchdf()
    d["date"] = pd.to_datetime(d["date"])
    d = d.set_index("date").asfreq("D")
    d.loc[d["qty"] == 0, "qty"] = pd.NA           # день без отчёта — дыра, не ноль
    d["qty"] = d["qty"].astype(float)
    d["base"] = d["qty"].rolling(window, center=True, min_periods=13).median()
    d["idx"] = d["qty"] / d["base"]
    d["wd"] = d.index.weekday

    cal = cal_load().copy()
    cal["date"] = pd.to_datetime(cal["date"])
    d = d.join(cal.set_index("date")[["is_dayoff", "is_preholiday"]])
    d = d[d["idx"].notna()].copy()

    # праздник = нерабочий день в пн–пт; сб/вс из нормы не выбрасывать
    is_hol = d["is_dayoff"].fillna(False) & (d["wd"] < 5)
    ordinary = d[~is_hol & ~d["is_preholiday"].fillna(False)]
    wd_norm = ordinary.groupby("wd")["idx"].median()
    d["clean"] = d["idx"] / d["wd"].map(wd_norm)
    return d


def special_dates(idx: pd.DataFrame) -> pd.DataFrame:
    out = []
    for (m, day), (const, _why) in FIXED_DATES.items():
        sub = idx[(idx.index.month == m) & (idx.index.day == day)]
        vals = [round(v, 2) for v in sub["clean"].dropna()]
        out.append({"month": m, "day": day, "const": const,
                    "recomputed": round(float(pd.Series(vals).mean()), 2) if vals else None,
                    "n_years": len(vals), "observations": " / ".join(map(str, vals)),
                    "disputed": False})
    for (m, day), why in DISPUTED.items():
        sub = idx[(idx.index.month == m) & (idx.index.day == day)]
        vals = [round(v, 2) for v in sub["clean"].dropna()]
        out.append({"month": m, "day": day, "const": 1.0,
                    "recomputed": round(float(pd.Series(vals).mean()), 2) if vals else None,
                    "n_years": len(vals), "observations": " / ".join(map(str, vals)),
                    "disputed": True})
    return pd.DataFrame(out)


@block("calendar")
def compute(ctx, reg) -> None:
    idx = daily_index(ctx.con)
    sp = special_dates(idx)
    # Сторож обязан уметь проваливаться: колонка exceeds и счётчик нарушений.
    # Без них панель писала бы «константы воспроизводимы» при любом расхождении.
    sp["exceeds"] = (~sp["disputed"] & sp["recomputed"].notna()
                     & ((sp["recomputed"] - sp["const"]).abs() > TOLERANCE))
    reg.table("cal.special_dates", sp, title="Календарные индексы: константа против пересчёта")

    fixed = sp[~sp["disputed"] & sp["recomputed"].notna()]
    dev = (fixed["recomputed"] - fixed["const"]).abs()
    worst = fixed.loc[dev.idxmax()] if len(fixed) else None
    facts = [
        {"id": "cal.max_dev_from_constants",
         "value": round(float(dev.max()), 3) if len(fixed) else None,
         "title": "максимальное расхождение пересчёта с константами модели",
         "method": f"допуск {TOLERANCE}; методика: объём/медиана 29 дн/норма дня недели",
         "period": f"{idx.index.min():%d.%m.%Y}–{idx.index.max():%d.%m.%Y}"},
        {"id": "cal.n_dates_checked", "value": int(len(fixed)), "unit": "дат",
         "title": "особых дат сверено"},
        {"id": "cal.n_dates_over_tolerance", "value": int(sp["exceeds"].sum()),
         "unit": "дат", "title": f"дат с расхождением больше допуска {TOLERANCE}",
         "method": "0 = константы воспроизводимы; >0 = данные уехали от таблицы, "
                   "смотреть cal.special_dates и решать про calendar_effects.FIXED_DATES"},
    ]
    if worst is not None:
        facts.append({"id": "cal.worst_date",
                      "value": f"{int(worst['day']):02d}.{int(worst['month']):02d}: "
                               f"константа {worst['const']}, пересчёт {worst['recomputed']}",
                      "title": "самая разошедшаяся дата"})
    reg.add_many("calendar", SRC, facts)
