# -*- coding: utf-8 -*-
"""Дуэль движков за прошедшие дни: базовая формула vs ML-ядро vs факт.

Зачем: клиенту (и нам) нужно видеть по датам, кто и на сколько промахнулся —
в штуках и в рублях. В журнале прогнозов обе цифры лежат рядом только с 14.08
(первый выпуск ядром), а разговор идёт про месяцы. Значит прошлые дни надо
пересчитать — честно, будто мы считали их тогда.

Честность (иначе таблица бессмысленна):
  * оба движка идут ПРОД-ПУТЁМ, через predict.daily.build — со своими
    предохранителями (у формулы дневная рамка reconcile_to_day, у ядра
    аварийный сторож). Брать `_mu_baseline` из ML-прогона нельзя: это формула
    ДО округления и без её собственной рамки, движка с таким поведением
    никогда не существовало;
  * модель ядра на день D обучается на данных <= D−3 и живёт в ПЕСОЧНИЦЕ
    (PROGNOZ_ML_MODEL_DIR): боевая модель обучена по свежему дню, и без
    песочницы она предсказывала бы июль, зная август. Проверка cutoff стоит
    на каждом дне: не сошлось — день не пишется, идёт в ALERT;
  * панель продаж и заявок режется `d < target` (внутри build), полка берётся
    готовым файлом shelf_rolling.parquet.

Задекларированное допущение (как в backtest.ab_shelf): часовой профиль полки
считался по всему окну, то есть оценка спроса дня D знает про дни после D.
На сам прогноз это не влияет, на оценку — единицы процентов.

Выход: analysis/output/duel/<дата>.parquet, один файл на день (как shadow/).
Веб читает их и считает деньги теми же долями 0.33/0.67, что и «Точность».

Запуск:
  разовый бэкфилл  PYTHONUTF8=1 PYTHONPATH=prognoz/src py -m prognoz.predict.duel --days=30 --db=snapshot
  свежий день      PYTHONUTF8=1 PYTHONPATH=prognoz/src py -m prognoz.predict.duel --days=1
  диапазон         ... --since=2026-07-01 --until=2026-08-11
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

SRC = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(SRC))
sys.path.insert(0, str(SRC.parents[0] / "analysis" / "etl"))
from _db import DB_DIR, OUTPUT, connect_reader              # noqa: E402
from prognoz.assortment import filter_frame                 # noqa: E402
from prognoz.predict import guard                           # noqa: E402
from prognoz.predict.daily import build, load               # noqa: E402
from prognoz.predict.demand import ROLLING_CACHE, BuildCfg, load_shelf  # noqa: E402

# Дуэль обязана мерить ТОТ ЖЕ движок, что выпускает боевую заявку, иначе вкладка
# «Сравнение» показывает клиенту не тот расчёт. Источник истины — как в daily.py.
ML_ENGINE = os.environ.get("PROGNOZ_ENGINE", "catboost").strip().lower()
if ML_ENGINE not in ("ml", "catboost"):
    ML_ENGINE = "catboost"

DUEL_DIR = OUTPUT / "duel"
SANDBOX_DIR = OUTPUT / "ml_model_duel"     # модели пересчёта, боевую не трогаем
LAG = 3                                    # горизонт ядра (ml_core.LAG)
FC = "Прогноз, шт"


# ------------------------------------------------------------------ данные дня

def shelf_slice(shelf: pd.DataFrame, day: date) -> pd.DataFrame:
    """Строки полки за день: недопродано, цена, флаги достоверности."""
    td = pd.to_datetime(shelf["trade_day"]).dt.date
    s = shelf[td == day]
    out = s[["point_key", "sku_key", "lost_units", "avg_price",
             "supply_gap", "wo_settled"]].copy()
    return out.rename(columns={"point_key": "_point_key", "sku_key": "_sku_key"})


def price_map(shelf: pd.DataFrame, day: date, window: int = 28) -> pd.Series:
    """Цена пары: медиана цены полки за 28 дней до дня. Как в web.accuracy."""
    td = pd.to_datetime(shelf["trade_day"]).dt.date
    w = shelf[(td <= day) & (td > day - timedelta(days=window))]
    if w.empty:
        return pd.Series(dtype=float)
    return w.groupby(["point_key", "sku_key"])["avg_price"].median()


def facts_from_panel(sales: pd.DataFrame, orders: pd.DataFrame, day: date
                     ) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Продажи и заявка человека за день — из уже загруженной панели.

    Панель собрана тем же SQL, что кормит расчёт: скоуп меню, склейка
    точек-двойников и карточек-призраков, склейка каналов заявки (zakaz →
    transfer). Отдельный запрос за фактом эти склейки терял бы.
    """
    s = sales[sales["d"] == day][["point_key", "sku_key", "qty", "u40"]].rename(
        columns={"point_key": "_point_key", "sku_key": "_sku_key",
                 "qty": "sold", "u40": "u40"})
    o = orders[orders["d"] == day][["point_key", "sku_key", "qty"]].rename(
        columns={"point_key": "_point_key", "sku_key": "_sku_key",
                 "qty": "human"})
    return s, o


# ------------------------------------------------------------------- один день

def one_day(day: date, con, panel, shelf: pd.DataFrame, sku_names: dict,
            point_names: dict, cheki_upto: date | None) -> pd.DataFrame | None:
    """Пересчёт дня обоими движками + факт. None — день забракован."""
    cfg_krap = BuildCfg(engine="baseline", shelf_path=ROLLING_CACHE)
    cfg_ml = BuildCfg(engine=ML_ENGINE, shelf_path=ROLLING_CACHE)

    krap = build(day, con, panel=panel, cfg=cfg_krap)[
        ["_point_key", "_sku_key", "Группа", FC]].rename(columns={FC: "baseline"})
    ml_df = build(day, con, panel=panel, cfg=cfg_ml)

    # --- проверка честности ML-прогона -------------------------------------
    engines = set(ml_df.get("engine", pd.Series(dtype=str)).astype(str))
    if engines != {ML_ENGINE}:
        guard.alert([f"ДУЭЛЬ {day}: ядро не отработало (движок {engines or '—'}) "
                     "— день пропущен"])
        return None
    cutoffs = set(ml_df.get("model_cutoff", pd.Series(dtype=str)).astype(str))
    limit = (day - timedelta(days=LAG)).isoformat()
    if not cutoffs or max(cutoffs) > limit:
        guard.alert([f"ДУЭЛЬ {day}: модель обучена по {max(cutoffs) if cutoffs else '—'}"
                     f", допустимо не позже {limit} — день пропущен "
                     "(заглядывание в будущее)"])
        return None

    ml = ml_df[["_point_key", "_sku_key", "Группа", FC, "model_cutoff"]].rename(
        columns={FC: "ml"})

    sold, human = facts_from_panel(panel[0], panel[1], day)
    shelf_d = shelf_slice(shelf, day)

    m = (krap.merge(ml, on=["_point_key", "_sku_key", "Группа"], how="outer")
              .merge(sold, on=["_point_key", "_sku_key"], how="outer")
              .merge(human, on=["_point_key", "_sku_key"], how="outer")
              .merge(shelf_d, on=["_point_key", "_sku_key"], how="outer"))

    for c in ("baseline", "ml", "sold", "u40", "human", "lost_units"):
        m[c] = pd.to_numeric(m.get(c), errors="coerce").fillna(0.0)
    m["demand"] = m["sold"] + m["lost_units"]
    m["supply_gap"] = m["supply_gap"].fillna(False).astype(bool)   # noqa: PD003
    m["wo_settled"] = m["wo_settled"].fillna(False).astype(bool)   # noqa: PD003

    # Цена зашивается в строку: иначе веб и пересчёт со временем разъедутся.
    pm = price_map(shelf, day)
    keys = list(zip(m["_point_key"], m["_sku_key"]))
    fallback = float(pm.median()) if len(pm) else 0.0
    m["price"] = [float(pm.get(k, fallback)) for k in keys]

    m["date"] = day.isoformat()
    m["canon_name"] = m["_point_key"].map(point_names)
    m["sku_name"] = m["_sku_key"].map(sku_names)
    m["model_cutoff"] = m["model_cutoff"].ffill().bfill()
    m["has_order"] = m["human"] > 0
    m["sellout"] = (m["u40"] < 0.5) & (m["sold"] > 0)
    # Чеки отстают от документа продаж — без них полка не видит времени
    # последней продажи, и «недопродано» на таких днях занижено (не ноль по
    # факту, а ноль по незнанию). Помечаем, а не молчим.
    m["chek_settled"] = bool(cheki_upto and day <= cheki_upto)

    # Точко-день без единой продажи = точка не сдала отчёт. Любой прогноз там
    # выглядит стопроцентным промахом — это дыра данных, а не ошибка движка.
    by_pt = m.groupby("canon_name")["sold"].transform("sum")
    m["no_report"] = by_pt <= 0
    return m


# --------------------------------------------------------------------- прогон

def run(days: list[date], con, *, verbose: bool = True) -> list[Path]:
    if not days:
        return []
    os.environ["PROGNOZ_ML_MODEL_DIR"] = str(SANDBOX_DIR)   # песочница моделей
    DUEL_DIR.mkdir(parents=True, exist_ok=True)

    # load_shelf, а не прямое чтение parquet: кэш мог протухнуть, если ночной
    # расчёт шёл движком, который полку не строит (откат на базовой формулы);
    # фильтр ассортиментной политики (metric_scope) load_shelf применяет сам
    shelf = load_shelf(con, max(days) + timedelta(days=1))
    panel = load(con, max(days) + timedelta(days=1))
    sales, orders, dims, sku = panel
    point_names = dict(zip(dims["point_key"], dims["canon_name"]))
    sku_names = dict(zip(sku["sku_key"], sku["name"]))
    try:
        cheki_upto = con.execute(
            "SELECT max(trade_day) FROM mart.cheki_sales_net").fetchone()[0]
        cheki_upto = pd.Timestamp(cheki_upto).date() if cheki_upto else None
    except Exception:                                       # noqa: BLE001
        cheki_upto = None

    # Самопроверка панельного пути: расчёт первой даты двумя способами должен
    # совпасть, иначе весь пересчёт недостоверен (приём из backtest.ab_shelf).
    probe = min(days)
    via_panel = build(probe, con, panel=panel).set_index(
        ["_point_key", "_sku_key"])["_mu"].sort_index()
    via_sql = build(probe, con).set_index(
        ["_point_key", "_sku_key"])["_mu"].sort_index()
    if not via_panel.equals(via_sql):
        raise SystemExit("панельный путь разошёлся с прямым — дуэль недостоверна")

    written = []
    for i, day in enumerate(sorted(days), 1):
        t0 = time.time()
        m = one_day(day, con, panel, shelf, sku_names, point_names, cheki_upto)
        if m is None:
            continue
        out = DUEL_DIR / f"{day.isoformat()}.parquet"
        m.to_parquet(out, index=False)
        written.append(out)
        if verbose:
            print(f"  [{i}/{len(days)}] {day}: строк {len(m):,}, "
                  f"формула {m['baseline'].sum():.0f} / ядро {m['ml'].sum():.0f} / "
                  f"человек {m['human'].sum():.0f} / продано {m['sold'].sum():.0f} "
                  f"({time.time() - t0:.0f} c)", flush=True)
    return written


def target_days(con, n_days: int | None, since: date | None, until: date | None,
                redo: bool) -> list[date]:
    """Какие дни считать: по умолчанию последние n_days с фактом продаж."""
    last_fact = con.execute(
        "SELECT max(sale_date) FROM mart.fact_sales_daily").fetchone()[0]
    last_fact = pd.Timestamp(last_fact).date()
    until = min(until, last_fact) if until else last_fact
    if since is None:
        since = until - timedelta(days=(n_days or 30) - 1)
    days = [since + timedelta(days=k) for k in range((until - since).days + 1)]
    if not redo:
        days = [d for d in days if not (DUEL_DIR / f"{d.isoformat()}.parquet").exists()]
    return days


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=None, help="сколько последних дней")
    ap.add_argument("--since", type=str, default=None)
    ap.add_argument("--until", type=str, default=None)
    ap.add_argument("--redo", action="store_true", help="пересчитать уже готовые дни")
    ap.add_argument("--db", choices=("live", "snapshot"), default="live",
                    help="snapshot — читать копию для веба, не мешая ночному прогону")
    a = ap.parse_args()

    if a.db == "snapshot":
        import duckdb                                        # noqa: PLC0415
        con = duckdb.connect(str(DB_DIR / "prognoz_web.duckdb"), read_only=True)
        con.execute("SET memory_limit='1200MB'")
        con.execute("SET threads=1")
    else:
        con = connect_reader()
    try:
        days = target_days(con, a.days,
                           date.fromisoformat(a.since) if a.since else None,
                           date.fromisoformat(a.until) if a.until else None,
                           a.redo)
        if not days:
            print("дуэль: новых дней нет")
            return 0
        print(f"дуэль: {len(days)} дн. ({days[0]} … {days[-1]}), "
              f"песочница моделей {SANDBOX_DIR.name}")
        written = run(days, con)
    finally:
        con.close()
    print(f"дуэль: записано {len(written)} дн. → {DUEL_DIR}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
