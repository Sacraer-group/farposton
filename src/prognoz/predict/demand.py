# -*- coding: utf-8 -*-
"""Восстановленный спрос: лечение самоподдерживающегося недозаказа.

Проблема (гипотеза сотрудника, подтверждена панелью проверки 08.08.2026): движок
учится на продажах, а продажи хронически дефицитных пар обрезаны тем, что
позицию вчера недовезли. История занижена на ~11,6% у 73 хронических пар —
движок воспроизводит недозаказ вечно и сам из ямы не выберется.

Лекарство: для пар с устойчивым дефицитом прогноз умножается на
uplift = (продано + недопродано) / продано за скользящие 28 дней, где
«недопродано» — оценка полки (shelf.core). Раскрутки вверх нет архитектурно:
лишняя штука вечером → уценка/списание видны следующему же расчёту →
classify() гасит lost за один цикл, а сверху три предохранителя ниже.

ТРИ ОГРАНИЧИТЕЛЯ (вердикт панели, менять только с пересмотром отчёта):
  1. Кап добавки +25% и включение только при ≥4 дефицитных днях за 28.
  2. Автоотзыв: доля уценки за 28 дней > 20% ИЛИ ≥2 дней со списанием за 14 —
     добавка выключается до следующего пересчёта (уценка/списания = завоз уже
     догнал спрос, добавка начала переливать).
  3. Недельный сторож (watchdog_report): тренд уценки и списаний по парам
     с добавкой — рост значит, что lost переоценён; смотреть руками.

Предусловие включения выполнено 09.08.2026: гейт уценки и карточки-призраки
починены (фиксы 1 и 3 панели) — иначе добавка недоливалась бы ровно там,
где нужнее всего.

Прод: флаг BuildCfg.demand_recovery в predict.daily, ПО УМОЛЧАНИЮ ВЫКЛЮЧЕН.
Включение — после бэктеста и отмашки сотрудника.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

SRC = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(SRC))
sys.path.insert(0, str(SRC.parents[0] / "analysis" / "etl"))
from _db import OUTPUT                                      # noqa: E402
from prognoz.assortment import filter_frame                 # noqa: E402
from prognoz.scope import canon_cte, sku_twins_cte          # noqa: E402

ROLLING_CACHE = OUTPUT / "shelf_rolling.parquet"

# Параметры правила — вердикт панели проверки (Отчёт_проверки_полки.md, п. 3)
UPLIFT_CAP = 1.25            # добавка не больше +25%
MIN_DEFICIT_DAYS = 4         # включение от ≥4 дефицитных дней за окно
WINDOW_DAYS = 28             # скользящее окно оценки
U40_REVOKE_SHARE = 0.20      # доля уценки за окно выше — отзыв
WO_REVOKE_DAYS = 2           # ≥2 дней со списанием за 14 дней — отзыв
WO_WINDOW_DAYS = 14


@dataclass(frozen=True)
class BuildCfg:
    """Конфигурация расчёта заявки. По умолчанию — ровно прежнее поведение."""
    demand_recovery: bool = False
    # Движок заявки: "baseline" — формула одноимённых дней со всеми рамками
    # (умолчание сохраняет тождество со старым продом байт-в-байт);
    # "ml" — GBM-ядро (predict.ml_core): сырой квантиль + календарь + floor
    # новинок + аварийный сторож, БЕЗ рамок к человеку (решение сотрудника 11.08:
    # ядро выпускается основным сразу). Прод включает ml в main() через
    # PROGNOZ_ENGINE; откат на старый движок — одна переменная окружения.
    engine: str = "baseline"
    # Граница доступной информации для ML: D+2/D+3/D+4 -> lag 3/4/5.
    # Не влияет на формулу базовой формулы.
    model_lag: int = 3
    # Готовая полка для бэктеста (годовой кэш). None = скользящее окно от asof.
    shelf_path: Path | None = None
    # ЭКСПЕРИМЕНТ (только бэктест): кормить движок восстановленным спросом
    # sold+lost вместо продаж по ВСЕМ парам. КВП при этом выключается — он был
    # прокси того же недозаказа («продали всё → спроса больше»), на спросе это
    # двойной счёт. Дневная рамка (reconcile_to_day) тоже выключается: её
    # ориентир — продажи, к спросу он занижен системно. В прод не включать
    # без отдельного решения.
    demand_feed: bool = False


def lost_lookup(shelf_df: pd.DataFrame) -> dict:
    """(point_key, sku_key, день) → недопродано шт. Для demand_feed."""
    s = shelf_df[shelf_df["lost_units"] > 0]
    days = pd.to_datetime(s["trade_day"]).dt.date
    return dict(zip(zip(s["point_key"], s["sku_key"], days), s["lost_units"]))


def load_shelf(con, asof: date, shelf_path: Path | None = None) -> pd.DataFrame:
    """Полка для оценки дефицита. Бэктест подаёт готовый файл, прод — скользящее
    окно (asof−365 … asof−1) с ОТДЕЛЬНЫМ кэшем: годовой shelf_daily.parquet
    заморожен за период ДО и принадлежит фактам/тестам, трогать его нельзя."""
    if shelf_path is not None:
        return filter_frame(
            pd.read_parquet(shelf_path), "metric_scope", date_col="trade_day")
    from prognoz.shelf.core import load_or_build            # noqa: PLC0415
    from prognoz.shelf.params import Params                 # noqa: PLC0415
    p = Params(since=asof - timedelta(days=365), until=asof - timedelta(days=1))
    return filter_frame(
        load_or_build(con, p, cache=ROLLING_CACHE),
        "metric_scope", date_col="trade_day")


def wo_recent_days(con, asof: date) -> pd.Series:
    """Дней со списанием за последние WO_WINDOW_DAYS по паре — из витрины
    напрямую (без settled-обрезки полки: свежие акты важны именно здесь)."""
    df = con.execute(f"""
        WITH {canon_cte()}, {sku_twins_cte()}
        SELECT c.pt AS point_key, coalesce(tw.live, w.sku_key) AS sku_key,
               count(DISTINCT w.wo_date) AS wo_days
        FROM mart.fact_writeoff_daily w
        JOIN c ON c.point_key = w.point_key
        LEFT JOIN tw ON tw.ghost = w.sku_key
        WHERE w.qty_writeoff > 0 AND w.wo_date < ? AND w.wo_date >= ?
        GROUP BY 1, 2
    """, [asof, asof - timedelta(days=WO_WINDOW_DAYS)]).fetchdf()
    df = filter_frame(df, "metric_scope")
    return df.set_index(["point_key", "sku_key"])["wo_days"]


def uplift_table(shelf_df: pd.DataFrame, asof: date,
                 wo_days14: pd.Series | None = None) -> pd.DataFrame:
    """Таблица добавок по парам на дату asof.

    Честность к бэктесту: используются ТОЛЬКО строки trade_day < asof —
    полка знает исход дня asof, но добавка его не видит. Дни без пришедших
    актов (wo_settled=False) не считаются: там «кончилось рано» может
    оказаться списанием, когда акт доедет.

    Колонки: uplift (1.0 если не активна/отозвана), active, revoked, reason,
    deficit_days28, sold28, lost28, u40_share28, wo_days14.
    """
    d0, d1 = asof - timedelta(days=WINDOW_DAYS), asof
    td = pd.to_datetime(shelf_df["trade_day"]).dt.date
    s = shelf_df[(td >= d0) & (td < d1) & shelf_df["wo_settled"]]
    if s.empty:
        return pd.DataFrame(columns=["point_key", "sku_key", "uplift", "active",
                                     "revoked", "reason", "deficit_days28"])
    g = s.groupby(["point_key", "sku_key"]).agg(
        sold28=("sold_units", "sum"),
        lost28=("lost_units", "sum"),
        u4028=("u40_units", "sum"),
        deficit_days28=("lost_units", lambda x: int((x > 0).sum()))).reset_index()
    g["u40_share28"] = g["u4028"] / g["sold28"].replace(0, np.nan)
    if wo_days14 is not None:
        g["wo_days14"] = (g.set_index(["point_key", "sku_key"]).index
                          .map(wo_days14).fillna(0).astype(int))
    else:
        g["wo_days14"] = 0

    g["active"] = (g["deficit_days28"] >= MIN_DEFICIT_DAYS) & (g["sold28"] > 0)
    raw = (g["sold28"] + g["lost28"]) / g["sold28"].replace(0, np.nan)
    g["uplift"] = np.minimum(UPLIFT_CAP, raw.fillna(1.0))

    revoke_u40 = g["u40_share28"].fillna(0) > U40_REVOKE_SHARE
    revoke_wo = g["wo_days14"] >= WO_REVOKE_DAYS
    g["revoked"] = g["active"] & (revoke_u40 | revoke_wo)
    g["reason"] = np.select(
        [g["revoked"] & revoke_u40 & revoke_wo,
         g["revoked"] & revoke_u40,
         g["revoked"] & revoke_wo],
        ["уценка и списания растут", "уценка выше 20% за 28 дн",
         "2+ дня со списанием за 14 дн"], default="")
    g.loc[~g["active"] | g["revoked"], "uplift"] = 1.0
    return g


def uplift_lookup(con, asof: date, cfg: BuildCfg) -> dict:
    """(point_key, sku_key) → строка добавки. Пустой словарь при выкл. флаге."""
    if not cfg.demand_recovery:
        return {}
    shelf_df = load_shelf(con, asof, cfg.shelf_path)
    tbl = uplift_table(shelf_df, asof, wo_recent_days(con, asof))
    return {(r.point_key, r.sku_key): r for r in tbl.itertuples()}


def watchdog_report(con, asof: date, cfg: BuildCfg | None = None) -> pd.DataFrame:
    """Ограничитель 3: тренд уценки/списаний по парам с активной добавкой.

    Сравнивает последние 14 дней с предыдущими 14. Рост уценки или списаний
    у пар с добавкой = lost переоценён, добавка переливает — смотреть руками.
    Пишется недельным прогоном в analysis/output/demand_watchdog.md.
    """
    cfg = cfg or BuildCfg(demand_recovery=True)
    shelf_df = load_shelf(con, asof, cfg.shelf_path)
    tbl = uplift_table(shelf_df, asof, wo_recent_days(con, asof))
    act = tbl[tbl["active"] & ~tbl["revoked"]]
    if act.empty:
        return pd.DataFrame()
    td = pd.to_datetime(shelf_df["trade_day"]).dt.date
    rows = []
    for _, r in act.iterrows():
        pair = shelf_df[(shelf_df["point_key"] == r["point_key"])
                        & (shelf_df["sku_key"] == r["sku_key"])]
        ptd = pd.to_datetime(pair["trade_day"]).dt.date
        recent = pair[(ptd >= asof - timedelta(days=14)) & (ptd < asof)]
        prev = pair[(ptd >= asof - timedelta(days=28))
                    & (ptd < asof - timedelta(days=14))]
        rows.append({
            "point_key": r["point_key"], "sku_key": r["sku_key"],
            "uplift": round(r["uplift"], 3),
            "deficit_days28": r["deficit_days28"],
            "u40_recent": recent["u40_units"].sum(),
            "u40_prev": prev["u40_units"].sum(),
            "wo_recent": recent["wo_units"].sum(),
            "wo_prev": prev["wo_units"].sum(),
        })
    out = pd.DataFrame(rows)
    out["u40_growing"] = out["u40_recent"] > out["u40_prev"] * 1.5 + 1
    out["wo_growing"] = out["wo_recent"] > out["wo_prev"] * 1.5 + 1
    return out.sort_values("uplift", ascending=False)


def main() -> int:
    """Недельный сторож. Вызывается run-weekly.ps1.

    Правило включается переменной окружения PROGNOZ_DEMAND_RECOVERY=1 (та же,
    что читает predict.daily). Пока правило выключено, сторожить нечего —
    выходим сразу, не строя скользящую полку.
    """
    import os                                               # noqa: PLC0415
    from datetime import date as _date                      # noqa: PLC0415
    from _db import connect_reader                          # noqa: PLC0415
    if os.environ.get("PROGNOZ_DEMAND_RECOVERY") != "1":
        print("восстановленный спрос выключен (PROGNOZ_DEMAND_RECOVERY≠1) — "
              "сторожить нечего")
        return 0
    con = connect_reader()
    asof = _date.today()
    rep = watchdog_report(con, asof)
    con.close()
    path = OUTPUT / "demand_watchdog.md"
    if rep.empty:
        path.write_text(f"# Сторож восстановленного спроса — {asof}\n\n"
                        "Активных добавок нет.\n", encoding="utf-8")
        print("активных добавок нет")
        return 0
    bad = rep[rep["u40_growing"] | rep["wo_growing"]]
    lines = [f"# Сторож восстановленного спроса — {asof}", "",
             f"Пар с активной добавкой: {len(rep)}; "
             f"с растущей уценкой/списаниями: {len(bad)}", ""]
    if len(bad):
        lines.append("⚠ У этих пар после добавки растут уценка или списания — "
                     "lost переоценён, смотреть руками:")
        lines.append(bad.to_string(index=False))
    lines.append("")
    lines.append(rep.to_string(index=False))
    path.write_text("\n".join(lines), encoding="utf-8")
    print(f"пар с добавкой {len(rep)}, тревожных {len(bad)} → {path}")
    return 1 if len(bad) else 0


if __name__ == "__main__":
    sys.exit(main())
