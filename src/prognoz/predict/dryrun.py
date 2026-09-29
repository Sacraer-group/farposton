# -*- coding: utf-8 -*-
"""Сухой прогон прод-прогноза: считаем на прошедшие даты и сверяем с фактом.

Проверяет не «красиво ли выглядит файл», а три вещи, на которых прод обычно и падает:
- нет ли систематического занижения (bias) — самая частая болезнь усадки и клипов;
- сходится ли дневной объём по точке с фактом (уровень, где принимается решение о поставке);
- не выкидывает ли ассортиментная матрица позиции, которые реально продавались.

Здесь же считается WAPE по всем уровням агрегации — та самая лестница из протокола.

Запуск: PYTHONUTF8=1 py prognoz/src/prognoz/predict/dryrun.py 2026-07-22 2026-07-29
"""
import sys
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

SRC = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(SRC))
sys.path.insert(0, str(SRC.parents[0] / "analysis" / "etl"))
from _db import OUTPUT, connect_reader                 # noqa: E402
from prognoz.assortment import filter_frame            # noqa: E402
from prognoz.predict.daily import build                # noqa: E402
from prognoz.scope import (GROUP_LIKE, GROUP_NOT_LIKE, GROUP_NOT_LIKE_BAR,  # noqa: E402
                           canon_cte, sku_twins_cte)


def actuals(con, day: date) -> pd.DataFrame:
    df = con.execute(f"""
        WITH {canon_cte()}, {sku_twins_cte()}
        SELECT c.pt AS _point_key, coalesce(tw.live, f.sku_key) AS _sku_key,
               sum(f.qty_sold - f.qty_returned) AS actual,
               sum(f.u40) AS u40
        FROM mart.fact_sales_daily f
        JOIN c ON c.point_key = f.point_key
        LEFT JOIN tw ON tw.ghost = f.sku_key
        JOIN mart.dim_sku s ON s.sku_key = coalesce(tw.live, f.sku_key)
        WHERE s.group_path LIKE ? AND s.group_path NOT LIKE ? AND s.group_path NOT LIKE ? AND f.sale_date = ?
        GROUP BY 1, 2
    """, [GROUP_LIKE, GROUP_NOT_LIKE, GROUP_NOT_LIKE_BAR, day]).fetchdf()
    df["date"] = day
    return filter_frame(
        df, "metric_scope", point_col="_point_key", sku_col="_sku_key",
        date_col="date").drop(columns=["date"])


def human_orders(con, day: date) -> pd.DataFrame:
    df = con.execute(f"""
        WITH {canon_cte()}, {sku_twins_cte()}
        SELECT c.pt AS _point_key, coalesce(tw.live, f.sku_key) AS _sku_key,
               sum(f.qty_ordered) AS human
        FROM mart.fact_orders_daily f
        JOIN c ON c.point_key = f.point_key
        LEFT JOIN tw ON tw.ghost = f.sku_key
        JOIN mart.dim_sku s ON s.sku_key = coalesce(tw.live, f.sku_key)
        WHERE s.group_path LIKE ? AND s.group_path NOT LIKE ? AND s.group_path NOT LIKE ?
          AND f.trade_date = ? AND f.channel = 'transfer'
        GROUP BY 1, 2
    """, [GROUP_LIKE, GROUP_NOT_LIKE, GROUP_NOT_LIKE_BAR, day]).fetchdf()
    df["date"] = day
    return filter_frame(
        df, "metric_scope", point_col="_point_key", sku_col="_sku_key",
        date_col="date").drop(columns=["date"])


def wape(g, fc="Прогноз, шт"):
    a = g["actual"].sum()
    return 100 * (g[fc] - g["actual"]).abs().sum() / a if a else float("nan")


def bias(g, fc="Прогноз, шт"):
    a = g["actual"].sum()
    return 100 * (g[fc] - g["actual"]).sum() / a if a else float("nan")


def main():
    days = [date.fromisoformat(a) for a in sys.argv[1:] if not a.startswith("-")]
    if not days:
        days = [date(2026, 7, 22), date(2026, 7, 29)]
    con = connect_reader()
    frames = []
    for day in days:
        df = build(day, con)
        act = actuals(con, day)
        hum = human_orders(con, day)
        m = df.merge(act, on=["_point_key", "_sku_key"], how="outer") \
              .merge(hum, on=["_point_key", "_sku_key"], how="left")
        m["Прогноз, шт"] = m["Прогноз, шт"].fillna(0)
        m["actual"] = m["actual"].fillna(0)
        m["u40"] = m["u40"].fillna(0)
        m["date"] = day
        names = con.execute("SELECT point_key AS _point_key, canon_name FROM mart.dim_point "
                            "WHERE canon_name IS NOT NULL").fetchdf()
        m = m.drop(columns=["Точка"], errors="ignore").merge(names, on="_point_key", how="left")
        frames.append(m)
        print(f"\n=== {day:%d.%m.%Y} ({['пн','вт','ср','чт','пт','сб','вс'][day.weekday()]}) ===")
        print(f"позиций в расчёте {len(df)}, факт продаж по {len(act)} позициям, "
              f"пропущено моделью {int((m['Прогноз, шт'] == 0).sum())} позиций с фактом "
              f"{m.loc[m['Прогноз, шт'] == 0, 'actual'].sum():.0f} шт")
        print(m.groupby("canon_name").apply(
            lambda g: pd.Series({
                "прогноз": g["Прогноз, шт"].sum(), "факт": g["actual"].sum(),
                "человек": g["human"].sum(), "WAPE %": round(wape(g), 1),
                "bias %": round(bias(g), 1)}), include_groups=False).to_string())
    con.close()

    all_df = pd.concat(frames, ignore_index=True)
    all_df["cat"] = all_df["Группа"].fillna("?")

    # Дни, за которые точка не сдала отчёт о продажах, метрикой быть не могут: факт там
    # ноль по всем позициям, и любой прогноз выглядит стопроцентным промахом. Это дыра
    # в данных, а не ошибка модели — выбрасываем и говорим сколько.
    day_pt = all_df.groupby(["date", "canon_name"])["actual"].sum()
    dead = day_pt[day_pt == 0]
    if len(dead):
        print(f"\n⚠ исключено {len(dead)} дней-точек без единой продажи (нет отчёта за день): "
              + ", ".join(f"{d:%d.%m} {p}" for (d, p) in dead.index))
        keep = day_pt[day_pt > 0].index
        all_df = all_df.set_index(["date", "canon_name"]).loc[keep].reset_index()
    levels = {
        "точка × позиция × день": ["date", "_point_key", "_sku_key"],
        "точка × группа × день": ["date", "_point_key", "cat"],
        "точка × день": ["date", "_point_key"],
        "сеть × день": ["date"],
    }

    def ladder(f):
        rows = []
        for name, keys in levels.items():
            g = f.groupby(keys, dropna=False)[["Прогноз, шт", "actual", "human"]].sum().reset_index()
            lam = g["actual"].mean()
            rows.append({
                "уровень": name,
                "пол Пуассона %": round(79.8 / (lam ** 0.5), 1) if lam > 0 else None,
                "модель WAPE %": round(wape(g), 1),
                "модель bias %": round(bias(g), 1),
                "человек WAPE %": round(wape(g, "human"), 1),
                "человек bias %": round(bias(g, "human"), 1),
            })
        return pd.DataFrame(rows)

    all_df["human"] = all_df["human"].fillna(0)

    # ЧЕСТНОЕ сравнение — только на позициях, которые сотрудник в этот день заказывал.
    # Иначе там, где он не заказывал, ему проставляется ноль и засчитывается промах:
    # ровно то подтасовывание множества, из-за которого раньше вышло «модель вдвое точнее».
    paired = all_df[all_df["human"] > 0]
    print(f"\n=== Лестница: множество решений сотрудника ({len(paired):,} строк из {len(all_df):,}) ===")
    print(ladder(paired).to_string(index=False))

    print("\n=== Справочно: все строки расчёта (сотруднику проставлен 0, где он не заказывал) ===")
    print(ladder(all_df).to_string(index=False))

    print("\n=== Покрытие ассортимента ===")
    tot = all_df["actual"].sum()
    for lbl, x in (("оба заказали", all_df[(all_df.human > 0) & (all_df["Прогноз, шт"] > 0)]),
                   ("только модель", all_df[(all_df.human == 0) & (all_df["Прогноз, шт"] > 0)]),
                   ("только человек", all_df[(all_df.human > 0) & (all_df["Прогноз, шт"] == 0)])):
        print(f"  {lbl:<16} строк {len(x):>6}  факт продаж {x['actual'].sum():>9.0f} шт "
              f"({100 * x['actual'].sum() / tot:>5.1f}%)")

    print("\n=== Только строки, где спрос наблюдаем (была уценка → товара хватало) ===")
    obs = paired[paired["u40"] >= 0.5]
    g = obs.groupby(["date", "_point_key", "_sku_key"])[["Прогноз, шт", "actual", "human"]].sum().reset_index()
    print(f"строк {len(g)} из {len(paired)}; модель WAPE {wape(g):.1f}% (bias {bias(g):+.1f}%), "
          f"человек WAPE {wape(g, 'human'):.1f}% (bias {bias(g, 'human'):+.1f}%)")

    out = OUTPUT / "dryrun_rows.parquet"
    all_df.to_parquet(out, index=False)
    print(f"\n→ {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
