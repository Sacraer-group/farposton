# -*- coding: utf-8 -*-
"""Бэктест «старый движок vs новый vs человек» против ВОССТАНОВЛЕННОГО спроса.

Отвечает на главный вопрос заказчика: будет ли ПО экономить деньги сети.

Ключевое отличие от прежних сверок: эталон дня — не продажи, а восстановленный
спрос из полки (продано + недопродано). Продажи — не спрос: если вчера привезли
мало, продажи обрезаны привозом, и «точный прогноз продаж» просто повторяет
чужой недозаказ. Оценивать заявку задним числом по исходу дня — законно:
мы ОЦЕНИВАЕМ уже сделанную заявку, а не прогнозируем; сами прогнозы построены
строго на данных ДО целевой даты (см. тест ниже и predict.demand — только
trade_day < asof).

Задекларированное допущение: часовой профиль полки посчитан по всему году,
то есть оценка спроса дня D знает будущее ПОСЛЕ D. На прогнозы это не влияет
(они полку дня D не видят), на оценку влияет <±5% (атаки панели проверки).

Метрика симметрична для всех трёх заявок (X ∈ {старый, новый, человек}):
    over  = Σ (X − спрос)⁺ × цена × 0,33   — себестоимость лишнего
    under = Σ (спрос − X)⁺ × цена × 0,67   — маржа недовезённого
    miss  = over + under                    — цена промаха заявки

Множество — строки, где человек в этот день заказывал (его решения против
наших). Исключаются: точко-дни без отчёта продаж, дни срывов поставки
(supply_gap — спрос неизвестен), дни без пришедших актов (wo_settled=False).

Запуск: PYTHONUTF8=1 PYTHONPATH=prognoz/src py -m prognoz.backtest.ab_shelf
        [--since=2026-06-01] [--until=2026-07-31] [--freeze]
--freeze дополнительно пишет ab_shelf_frozen.parquet — канонический прогон
для блока фактов compare (обычный прогон его не трогает).
"""
from __future__ import annotations

import sys
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

SRC = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(SRC))
sys.path.insert(0, str(SRC.parents[0] / "analysis" / "etl"))
from _db import OUTPUT, connect_reader                     # noqa: E402
from prognoz.predict.daily import build, load              # noqa: E402
from prognoz.predict.demand import BuildCfg                # noqa: E402
from prognoz.predict.dryrun import human_orders            # noqa: E402
from prognoz.assortment import filter_frame                # noqa: E402

SHELF_YEAR = OUTPUT / "shelf_daily.parquet"
OUT_ROWS = OUTPUT / "ab_shelf_rows.parquet"
OUT_FROZEN = OUTPUT / "ab_shelf_frozen.parquet"
CO, CU = 0.33, 0.67


def shelf_day(shelf: pd.DataFrame, day: date) -> pd.DataFrame:
    """Восстановленный спрос дня: продано + недопродано, цена, флаги."""
    td = pd.to_datetime(shelf["trade_day"]).dt.date
    s = shelf[td == day]
    out = s[["point_key", "sku_key", "sold_units", "lost_units", "avg_price",
             "supply_gap", "wo_settled"]].copy()
    out["demand"] = out["sold_units"] + out["lost_units"]
    return out.rename(columns={"point_key": "_point_key", "sku_key": "_sku_key"})


def run(days: list[date], con) -> pd.DataFrame:
    shelf = filter_frame(
        pd.read_parquet(SHELF_YEAR), "metric_scope", date_col="trade_day")
    upto_max = max(days) + timedelta(days=1)
    panel = load(con, upto_max)

    # Самопроверка панельного пути: первая дата двумя способами, байт-в-байт.
    probe = days[0]
    via_panel = build(probe, con, panel=panel).set_index(
        ["_point_key", "_sku_key"])["_mu"].sort_index()
    via_sql = build(probe, con).set_index(
        ["_point_key", "_sku_key"])["_mu"].sort_index()
    if not via_panel.equals(via_sql):
        raise SystemExit("панельный путь разошёлся с прямым — бэктест недостоверен")

    cfg_new = BuildCfg(demand_recovery=True, shelf_path=SHELF_YEAR)
    cfg_feed = BuildCfg(demand_feed=True, shelf_path=SHELF_YEAR)
    frames = []
    for i, day in enumerate(days, 1):
        old = build(day, con, panel=panel)[
            ["_point_key", "_sku_key", "Прогноз, шт"]].rename(
            columns={"Прогноз, шт": "old"})
        new = build(day, con, panel=panel, cfg=cfg_new)[
            ["_point_key", "_sku_key", "Прогноз, шт"]].rename(
            columns={"Прогноз, шт": "new"})
        feed = build(day, con, panel=panel, cfg=cfg_feed)[
            ["_point_key", "_sku_key", "Прогноз, шт"]].rename(
            columns={"Прогноз, шт": "feed"})
        hum = human_orders(con, day)
        dem = shelf_day(shelf, day)

        m = (dem.merge(old, on=["_point_key", "_sku_key"], how="outer")
                .merge(new, on=["_point_key", "_sku_key"], how="outer")
                .merge(feed, on=["_point_key", "_sku_key"], how="outer")
                .merge(hum, on=["_point_key", "_sku_key"], how="outer"))
        for c in ("old", "new", "feed", "human", "demand", "sold_units",
                  "lost_units"):
            m[c] = m[c].fillna(0.0)
        m["supply_gap"] = m["supply_gap"].fillna(False).astype(bool)
        m["wo_settled"] = m["wo_settled"].fillna(False).astype(bool)
        m["date"] = day
        frames.append(m)
        if i % 10 == 0:
            print(f"  {i}/{len(days)} дат…", flush=True)

    df = pd.concat(frames, ignore_index=True)
    names = con.execute("SELECT point_key AS _point_key, canon_name "
                        "FROM mart.dim_point WHERE canon_name IS NOT NULL").fetchdf()
    df = df.merge(names, on="_point_key", how="left")

    # Точко-дни без отчёта продаж: факт нулевой по всем позициям — дыра данных
    day_pt = df.groupby(["date", "canon_name"])["sold_units"].sum()
    dead = set(day_pt[day_pt == 0].index)
    df["no_report"] = df.set_index(["date", "canon_name"]).index.isin(dead)
    return filter_frame(
        df, "metric_scope", point_col="_point_key", sku_col="_sku_key",
        date_col="date")


def money(df: pd.DataFrame, col: str) -> tuple[float, float]:
    px = df["avg_price"].fillna(df["avg_price"].median())
    over = ((df[col] - df["demand"]).clip(lower=0) * px * CO).sum()
    under = ((df["demand"] - df[col]).clip(lower=0) * px * CU).sum()
    return over, under


def report(df: pd.DataFrame) -> pd.DataFrame:
    """Основная таблица: только строки решений человека, чистые дни."""
    ok = df[(df["human"] > 0) & ~df["supply_gap"] & df["wo_settled"]
            & ~df["no_report"]]
    n_days = ok["date"].nunique()
    print(f"\n=== ОЦЕНКА ПРОТИВ ВОССТАНОВЛЕННОГО СПРОСА ===")
    print(f"окно {ok['date'].min()} … {ok['date'].max()} ({n_days} дн., "
          f"{len(ok):,} строк решений человека; исключено: "
          f"{int(df['supply_gap'].sum()):,} строк срывов, "
          f"{int((~df['wo_settled']).sum()):,} без актов, "
          f"{int(df['no_report'].sum()):,} без отчёта)")

    variants = (("человек", "human"), ("движок как есть", "old"),
                ("движок + восст. спрос", "new"),
                ("движок на спросе (эксп.)", "feed"))
    rows = []
    for name, col in variants:
        over, under = money(ok, col)
        rows.append({"заявка": name,
                     "лишнего, тыс ₽ (×0,33)": round(over / 1e3),
                     "недовезли, тыс ₽ (×0,67)": round(under / 1e3),
                     "промах, тыс ₽": round((over + under) / 1e3),
                     "шт/день": round(ok.groupby("date")[col].sum().mean())})
    res = pd.DataFrame(rows)
    print(res.to_string(index=False))

    print("\n=== по точкам (промах, тыс ₽) ===")
    pts = []
    for pt, g in ok.groupby("canon_name"):
        r = {"точка": pt, "строк": len(g)}
        for name, col in variants:
            o, u = money(g, col)
            r[name.split(" ")[0] if col != "feed" else "на спросе"] = \
                round((o + u) / 1e3)
        pts.append(r)
    print(pd.DataFrame(pts).to_string(index=False))

    print("\n=== по неделям (промах, тыс ₽) ===")
    ok2 = ok.copy()
    ok2["week"] = pd.to_datetime(ok2["date"]).dt.strftime("%V")
    wk = []
    for w, g in ok2.groupby("week"):
        r = {"нед.": w, "дней": g["date"].nunique()}
        for name, col in variants:
            o, u = money(g, col)
            r[name.split(" ")[0] if col != "feed" else "на спросе"] = \
                round((o + u) / 1e3)
        wk.append(r)
    print(pd.DataFrame(wk).to_string(index=False))

    # Экстраполяция года — двумя способами, оба с оговорками
    h_over, h_under = money(ok, "human")
    n_over, n_under = money(ok, "new")
    saving_window = (h_over + h_under) - (n_over + n_under)
    per_year_linear = saving_window * 365 / n_days
    print(f"\nэкономия нового движка против человека за окно: "
          f"{saving_window / 1e3:,.0f} тыс ₽")
    print(f"экстраполяция на год (линейная, ×365/{n_days}): "
          f"{per_year_linear / 1e6:,.1f} млн ₽/год")
    print("оговорки: летнее окно, 5 точек против 2 в начале года ДО, спрос — "
          "нижняя граница полки")
    return res


def main() -> int:
    since, until, freeze = date(2026, 6, 1), date(2026, 7, 31), False
    for a in sys.argv[1:]:
        if a.startswith("--since="):
            since = date.fromisoformat(a.split("=", 1)[1])
        elif a.startswith("--until="):
            until = date.fromisoformat(a.split("=", 1)[1])
        elif a == "--freeze":
            freeze = True
    days = [since + timedelta(days=i) for i in range((until - since).days + 1)]
    con = connect_reader()
    df = run(days, con)
    con.close()
    df.to_parquet(OUT_ROWS, index=False)
    report(df)
    print(f"\n→ {OUT_ROWS}")
    if freeze:
        df.to_parquet(OUT_FROZEN, index=False)
        print(f"→ {OUT_FROZEN} (канонический прогон для блока compare)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
