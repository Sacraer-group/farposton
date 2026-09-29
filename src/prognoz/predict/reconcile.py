# -*- coding: utf-8 -*-
"""Сверка ВЫПУЩЕННОГО прогноза с фактом дня — измерение теневого режима.

Отличие от dryrun.py: тот пересчитывает прогноз задним числом на текущем коде,
а этот берёт файл, который реально ушёл в теневой режим (forecast_log/<дата>.parquet).
Задним числом всегда получается красивее — здесь мерим то, что клиент бы увидел.

⚠️ Что означает «факт» в теневом режиме. Наш прогноз ни на что не влиял: возили
по заявке сотрудника, продажи ограничены его привозом. Поэтому:
  - промах ВВЕРХ (прогноз > продаж) может быть как ошибкой, так и правотой модели,
    которую нечем подтвердить — товара привезли меньше, спрос не увидели;
  - промах ВНИЗ (прогноз < продаж) — всегда настоящий промах: столько купили ФАКТИЧЕСКИ.
Строки, где товар распродан подчистую (нет уценки), помечены sellout — там факт
это нижняя граница спроса, а не спрос.

Накопитель: analysis/output/shadow/<дата>.parquet + сводка shadow_log.csv,
чтобы к концу тени была история точности по дням, а не единичный замер.

Запуск: PYTHONUTF8=1 PYTHONPATH=prognoz/src py -m prognoz.predict.reconcile 2026-08-03
        (без даты — все даты журнала, для которых уже есть факт)
"""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import pandas as pd

SRC = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(SRC))
sys.path.insert(0, str(SRC.parents[0] / "analysis" / "etl"))
from _db import OUTPUT, connect_reader                                  # noqa: E402
from prognoz.assortment import filter_frame                             # noqa: E402
from prognoz.scope import (GROUP_LIKE, GROUP_NOT_LIKE, GROUP_NOT_LIKE_BAR,  # noqa: E402
                           canon_cte)

FC = "Прогноз, шт"
LOG_DIR = OUTPUT / "forecast_log"
SHADOW = OUTPUT / "shadow"
WD = ("пн", "вт", "ср", "чт", "пт", "сб", "вс")


def facts_for_day(con, day: date) -> pd.DataFrame:
    """Продажи, уценка и заявка сотрудника за день — на ключах прогноза."""
    sales = con.execute(f"""
        WITH {canon_cte()}
        SELECT c.pt AS _point_key, f.sku_key AS _sku_key,
               sum(f.qty_sold - f.qty_returned) AS actual,
               sum(f.u40)                       AS u40
        FROM mart.fact_sales_daily f
        JOIN c ON c.point_key = f.point_key
        JOIN mart.dim_sku s ON s.sku_key = f.sku_key
        WHERE s.group_path LIKE ? AND s.group_path NOT LIKE ?
          AND s.group_path NOT LIKE ? AND f.sale_date = ?
        GROUP BY 1, 2
    """, [GROUP_LIKE, GROUP_NOT_LIKE, GROUP_NOT_LIKE_BAR, day]).fetchdf()
    human = con.execute(f"""
        WITH {canon_cte()}
        SELECT c.pt AS _point_key, f.sku_key AS _sku_key, sum(f.qty_ordered) AS human
        FROM mart.fact_orders_daily f
        JOIN c ON c.point_key = f.point_key
        JOIN mart.dim_sku s ON s.sku_key = f.sku_key
        WHERE s.group_path LIKE ? AND s.group_path NOT LIKE ?
          AND s.group_path NOT LIKE ? AND f.trade_date = ? AND f.channel = 'transfer'
        GROUP BY 1, 2
    """, [GROUP_LIKE, GROUP_NOT_LIKE, GROUP_NOT_LIKE_BAR, day]).fetchdf()
    facts = sales.merge(human, on=["_point_key", "_sku_key"], how="outer")
    facts["date"] = day
    facts = filter_frame(
        facts, "metric_scope", point_col="_point_key", sku_col="_sku_key",
        date_col="date")
    return facts.drop(columns=["date"])


def wape(g: pd.DataFrame, col: str = FC) -> float:
    a = g["actual"].sum()
    return 100 * (g[col] - g["actual"]).abs().sum() / a if a else float("nan")


def bias(g: pd.DataFrame, col: str = FC) -> float:
    a = g["actual"].sum()
    return 100 * (g[col] - g["actual"]).sum() / a if a else float("nan")


def scope_skus(con) -> set[str]:
    """Позиции ДЕЙСТВУЮЩЕГО скоупа. Старые журналы прогнозов считались с баром;
    факт мы считаем без него, и без приведения к общему множеству весь бар
    превращается в фиктивный перезаказ (03.08: 97 шт из 103)."""
    return set(con.execute("""
        SELECT sku_key FROM mart.dim_sku
        WHERE group_path LIKE ? AND group_path NOT LIKE ? AND group_path NOT LIKE ?
    """, [GROUP_LIKE, GROUP_NOT_LIKE, GROUP_NOT_LIKE_BAR]).fetchdf()["sku_key"])


def reconcile(day: date, con) -> pd.DataFrame | None:
    src = LOG_DIR / f"{day:%Y-%m-%d}.parquet"
    if not src.exists():
        print(f"⚠ нет журнала прогноза за {day:%d.%m.%Y} — пропускаю")
        return None
    fc = pd.read_parquet(src)
    keep = scope_skus(con)
    out_of_scope = fc[~fc["_sku_key"].isin(keep)]
    if len(out_of_scope):
        print(f"⚠ из журнала за {day:%d.%m.%Y} исключено {len(out_of_scope)} строк вне текущего "
              f"скоупа (бар/перепродажа) на {out_of_scope[FC].sum():.0f} шт прогноза — "
              f"файл выпускался до вывода бара из расчёта")
        fc = fc[fc["_sku_key"].isin(keep)]
    fc = fc.copy()
    fc["date"] = day
    fc = filter_frame(
        fc, "metric_scope", point_col="_point_key", sku_col="_sku_key",
        date_col="date")
    m = fc.merge(facts_for_day(con, day), on=["_point_key", "_sku_key"], how="outer")
    for c in (FC, "actual", "u40", "human"):
        m[c] = m[c].fillna(0)
    m["date"] = day
    names = con.execute("SELECT point_key AS _point_key, canon_name FROM mart.dim_point "
                        "WHERE canon_name IS NOT NULL").fetchdf()
    m = m.drop(columns=["Точка"], errors="ignore").merge(names, on="_point_key", how="left")
    m["Группа"] = m["Группа"].fillna("вне расчёта")
    m["err"] = m[FC] - m["actual"]
    # распродано без уценки → факт это нижняя граница спроса, а не спрос
    m["sellout"] = (m["u40"] < 0.5) & (m["actual"] > 0)
    return m


def report(m: pd.DataFrame) -> None:
    day = m["date"].iloc[0]
    print(f"\n{'='*72}\n СВЕРКА {day:%d.%m.%Y} ({WD[day.weekday()]}) — "
          f"прогноз выпущен {pd.to_datetime(m['run_at'].dropna().iloc[0]):%d.%m %H:%M}, "
          f"на данных по {m['max_sale_date'].dropna().iloc[0]}\n{'='*72}")

    live = m[m["canon_name"].notna()]
    dead = live.groupby("canon_name")["actual"].sum()
    dead = dead[dead == 0]
    if len(dead):
        print(f"⚠ точки без единой продажи (нет отчёта за день): {', '.join(dead.index)} — исключены")
        live = live[~live["canon_name"].isin(dead.index)]

    if live.empty:
        # день «с фактом» только формально: пара строк вне скоупа (документ, проведённый
        # среди дня) делает дату видимой, а все точки при этом без отчёта — 07.08 так
        # уронило ночную сверку. Такой день просто ещё не наступил для сверки.
        print("день без единого отчёта продаж по точкам — сверять нечего, пропускаю")
        return

    net = live.groupby("date")[[FC, "actual", "human"]].sum()
    print(f"\nСЕТЬ ЗА ДЕНЬ: прогноз {net[FC].iloc[0]:.0f} шт, продано {net['actual'].iloc[0]:.0f} шт, "
          f"промах {net[FC].iloc[0] - net['actual'].iloc[0]:+.0f} шт "
          f"({100*(net[FC].iloc[0]-net['actual'].iloc[0])/net['actual'].iloc[0]:+.1f}%)")

    print("\nПО ТОЧКАМ:")
    pt = live.groupby("canon_name").apply(lambda g: pd.Series({
        "прогноз": g[FC].sum(), "продано": g["actual"].sum(),
        "заявка чел.": g["human"].sum(),
        "промах шт": g[FC].sum() - g["actual"].sum(),
        "промах %": round(100 * (g[FC].sum() - g["actual"].sum()) / g["actual"].sum(), 1)
        if g["actual"].sum() else None,
        "WAPE позиций %": round(wape(g), 1),
    }), include_groups=False)
    print(pt.to_string())

    lvl = {"позиция × точка": ["_point_key", "_sku_key"],
           "группа × точка": ["_point_key", "Группа"],
           "точка": ["_point_key"],
           "сеть": ["date"]}
    print("\nЛЕСТНИЦА ЗА ДЕНЬ (модель / сотрудник, только строки с его заявкой):")
    paired = live[live["human"] > 0]
    for name, keys in lvl.items():
        g = live.groupby(keys, dropna=False)[[FC, "actual", "human"]].sum().reset_index()
        gp = paired.groupby(keys, dropna=False)[[FC, "actual", "human"]].sum().reset_index()
        print(f"  {name:<16} модель WAPE {wape(g):5.1f}% (перекос {bias(g):+5.1f}%)"
              f"   |  на парных: модель {wape(gp):5.1f}% против человека {wape(gp,'human'):5.1f}%")

    print(f"\nПРОМАХ ВНИЗ — недодали (это настоящие потери продаж): "
          f"{-live.loc[live.err < 0, 'err'].sum():.0f} шт на {int((live.err < 0).sum())} строках")
    print(f"ПРОМАХ ВВЕРХ — лишнее (часть могла бы продаться, товара просто не было): "
          f"{live.loc[live.err > 0, 'err'].sum():.0f} шт на {int((live.err > 0).sum())} строках")
    print(f"Распродано подчистую (sellout, спрос не наблюдаем): "
          f"{int(live['sellout'].sum())} строк — там факт это нижняя граница")

    def top(df: pd.DataFrame, mark_sellout: bool = False) -> None:
        for _, r in df.iterrows():
            print(f"  {str(r['canon_name'])[:20]:20s} {str(r['Позиция'])[:34]:34s} "
                  f"прогноз {r[FC]:3.0f} → продано {r['actual']:3.0f} ({r['err']:+.0f})"
                  f"{'  [распродано]' if mark_sellout and r['sellout'] else ''}")

    print("\nТОП-12 промахов ВНИЗ (недооценили спрос):")
    top(live.nsmallest(12, "err"), mark_sellout=True)
    print("\nТОП-12 промахов ВВЕРХ (перезаказ):")
    top(live.nlargest(12, "err"))

    print("\nПО ГРУППАМ (где промах крупнее всего):")
    gr = live.groupby("Группа").agg(прогноз=(FC, "sum"), продано=("actual", "sum"))
    gr["промах"] = gr.прогноз - gr.продано
    gr["промах %"] = (100 * gr.промах / gr.продано).round(1)
    print(gr.reindex(gr.промах.abs().sort_values(ascending=False).index).head(10).to_string())


def main() -> int:
    days = [date.fromisoformat(a) for a in sys.argv[1:] if not a.startswith("-")]
    con = connect_reader()
    max_sale = con.execute("SELECT max(sale_date) FROM mart.fact_sales_daily").fetchone()[0]
    if not days:
        days = sorted(date.fromisoformat(p.stem) for p in LOG_DIR.glob("*.parquet")
                      if date.fromisoformat(p.stem) <= max_sale)
    SHADOW.mkdir(parents=True, exist_ok=True)
    summary = []
    for day in days:
        if day > max_sale:
            print(f"⚠ {day:%d.%m.%Y}: фактa ещё нет (продажи по {max_sale:%d.%m.%Y}) — пропускаю")
            continue
        m = reconcile(day, con)
        if m is None:
            continue
        report(m)
        m.to_parquet(SHADOW / f"{day:%Y-%m-%d}.parquet", index=False)
        live = m[m["canon_name"].notna()]
        alive = live.groupby("canon_name")["actual"].sum()
        live = live[live["canon_name"].isin(alive[alive > 0].index)]
        g = live.groupby(["_point_key", "_sku_key"])[[FC, "actual", "human"]].sum().reset_index()
        gp = live[live["human"] > 0].groupby(["_point_key", "_sku_key"])[
            [FC, "actual", "human"]].sum().reset_index()
        summary.append({
            "дата": f"{day:%Y-%m-%d}", "день": WD[day.weekday()],
            "прогноз": round(live[FC].sum()), "продано": round(live["actual"].sum()),
            "перекос %": round(bias(live.groupby("date")[[FC, "actual"]].sum().reset_index()), 1),
            "WAPE сеть %": round(abs(bias(live.groupby("date")[[FC, "actual"]].sum().reset_index())), 1),
            "WAPE позиция %": round(wape(g), 1),
            "WAPE позиция парн. %": round(wape(gp), 1),
            "человек позиция парн. %": round(wape(gp, "human"), 1),
        })
    con.close()

    if summary:
        s = pd.DataFrame(summary)
        path = OUTPUT / "shadow_log.csv"
        if path.exists():
            old = pd.read_csv(path)
            s = pd.concat([old[~old["дата"].isin(s["дата"])], s], ignore_index=True)
        s = s.sort_values("дата")
        s.to_csv(path, index=False, encoding="utf-8-sig")
        print(f"\n{'='*72}\nЖУРНАЛ ТЕНЕВОГО РЕЖИМА (накопительно):")
        print(s.to_string(index=False))
        print(f"→ {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
