# -*- coding: utf-8 -*-
"""Свод точности нового движка для вкладки «Точность» (редизайн 12.08).

Точка отсчёта — ENGINE_EPOCH = 11.08.2026: с этого дня ML-ядро основное,
вся статистика страницы начинается с него. Файлы сверки за более ранние даты
(старый движок) в выборки не попадают и даже не читаются.

Тройное сравнение по строкам shadow/<дата>.parquet:
- forecast   — что предложило ПО («Прогноз, шт»);
- order_fact — факт заявки: перенос-заявка 1С (human), если она в этот день
  была (сотрудники пока заказывают мимо ПО); иначе прогноз ПО с учётом правок
  сотрудника на странице «Заявка» (журнал web_edits);
- sold       — фактические продажи (actual).

Метрики обоих рядов считаются к продажам построчно (позиция × день):
WAPE = Σ|ряд − продажи| / Σпродажи, перекос = Σ(ряд − продажи) / Σпродажи.

Деньги промаха — методика evaluate: over = (заказ−факт)⁺ × цена × 0.33,
under = (факт−заказ)⁺ × цена × 0.67, построчно БЕЗ сальдирования; цены —
avg_price полки по (point, sku), fallback — общая медиана.

Списания за период — снимок DuckDB (mart.fact_writeoff_daily) по скоупу
прогноза; в снимке они отстают от продаж, поэтому отдаём и last_wo_date.
"""
from __future__ import annotations

from . import paths  # noqa: F401  — кладёт prognoz/src в sys.path ДО импорта ядра

import json
import os
import os
from datetime import date, datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
from loguru import logger

from . import data
from . import edits as edits_store
from . import order as order_store
from .config import ROOT
from .export import make_export_tempfile

from prognoz.assortment import filter_frame, sql_exclusion_predicate
from prognoz.scope import GROUP_LIKE, GROUP_NOT_LIKE, GROUP_NOT_LIKE_BAR, canon_cte

FC = "Прогноз, шт"
SHADOW_DIR = data.OUTPUT / "shadow"
CONFIG_DIR = Path(os.getenv("PROGNOZ_CONFIG_DIR") or ROOT / "analysis" / "config")

# День, с которого ML-ядро стало основным, — определяется ПО ФАКТУ, а не константой:
# первая дата в журнале прогнозов с любым штатным ML-движком. Константа тут врала:
# 11.08, а первый выпуск ядром — заявка на 14.08 (12.08 вечером), и страница выдавала
# три дня старого движка за новый. Фолбэк на случай, если колонки engine нигде нет.
ENGINE_EPOCH_FALLBACK = date(2026, 8, 14)
ML_ENGINES = frozenset({"ml", "catboost", "stacked_guard"})

OVER_COST_SHARE = 0.33     # перезаказ: теряем себестоимость ≈ треть цены
UNDER_MARGIN_SHARE = 0.67  # недозаказ: теряем маржу ≈ две трети цены

PERIOD_DAYS = {"last": 1, "7d": 7, "30d": 30, "all": None}
LEVELS = ("network", "point")

_ZERO_MONEY = {"over_k": 0.0, "under_k": 0.0, "miss_k": 0.0,
               "priced_rows": 0, "fallback_rows": 0}


# --- загрузка -----------------------------------------------------------------

def excluded_days() -> list[str]:
    """Даты, исключённые из оценки (analysis/config/excluded_days.json)."""
    p = CONFIG_DIR / "excluded_days.json"
    if not p.exists():
        return []
    try:
        doc = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return [d.get("date") for d in doc.get("days", []) if d.get("date")]


@data.ttl_cache(seconds=600)
def engine_epoch() -> date:
    """Первый день, заявку на который выпустило ML-ядро (по журналу прогнозов).

    Дата нужна честная: до неё заявки считал базовый движок, и мешать их в одну
    статистику «нового движка» нельзя. Старые файлы журнала колонки engine не имеют
    вовсе — они по построению относятся к старому движку и пропускаются.
    """
    dates = []
    for p in sorted(data.FORECAST_LOG.glob("*.parquet")):
        try:
            eng = pd.read_parquet(p, columns=["engine"])["engine"]
        except (OSError, ValueError, KeyError):
            continue                      # нет колонки — старый движок
        normalized = eng.astype(str).str.strip().str.lower()
        if normalized.isin(ML_ENGINES).any():
            dates.append(p.stem)
    return date.fromisoformat(min(dates)) if dates else ENGINE_EPOCH_FALLBACK


@data.ttl_cache(seconds=600)
def _sku_names() -> dict:
    """sku_key → имя из снимка БД: подписи строкам, пришедшим только из факта
    (в журнале прогноза их не было, «Позиция» там пустая)."""
    try:
        with data.connect_snapshot() as con:
            rows = con.execute("SELECT sku_key, name FROM mart.dim_sku").fetchall()
        return {k: n for k, n in rows}
    except Exception:
        return {}   # снимка может не быть — подписи заменит sku_key


def _edit_map(days: list[str]) -> dict[tuple[str, str, str], float]:
    """(date, point_key, sku_key) → действующая правка сотрудника из web_edits."""
    out: dict[tuple[str, str, str], float] = {}
    for day in days:
        try:
            release_id = order_store.release_fingerprint(day)
            eff = edits_store.get_effective(day, release_id)
        except Exception:
            logger.exception("accuracy: журнал правок не прочитался (дата {d})", d=day)
            continue
        for (pk, sk), q in eff.items():
            out[(day, pk, sk)] = float(q)
    return out


@data.ttl_cache(seconds=300)
def _shadow_all() -> pd.DataFrame:
    """Дни сверки нового движка (с ENGINE_EPOCH) одним DataFrame.

    Чистка как в reconcile.report: только строки с известной точкой, минус
    исключённые дни, минус точки без единой продажи за день. Плюс колонки
    тройного сравнения: edit_qty (правка сотрудника), order_fact (факт заявки),
    err_order = order_fact − actual.

    Производительность: файлы с датой раньше вчерашней прогон не переписывает —
    читаем обычным pd.read_parquet; «стабильное» чтение с паузами — только
    1–2 свежим файлам, которые могут писаться прямо сейчас.
    """
    epoch_s = engine_epoch().isoformat()
    files = sorted(p for p in SHADOW_DIR.glob("*.parquet") if p.stem >= epoch_s)
    if not files:
        return pd.DataFrame()
    fresh_from = (date.today() - timedelta(days=1)).isoformat()
    frames = [data.read_parquet_stable(p) if p.stem >= fresh_from else pd.read_parquet(p)
              for p in files]
    df = pd.concat(frames, ignore_index=True)

    for c in (FC, "actual", "human", "u40"):
        df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0.0) if c in df.columns else 0.0
    df["err"] = df[FC] - df["actual"]
    if "sellout" in df.columns:
        df["sellout"] = df["sellout"].fillna(False).astype(bool)
    else:   # файл без колонки: распродано подчистую = продажи есть, уценки нет
        df["sellout"] = (df["u40"] < 0.5) & (df["actual"] > 0)
    df["date"] = df["date"].astype(str).str[:10]

    df = df[df["canon_name"].notna()]
    df = filter_frame(
        df, "metric_scope", point_col="_point_key", sku_col="_sku_key",
        date_col="date")
    excl = set(excluded_days())
    if excl:
        df = df[~df["date"].isin(excl)]
    day_point_total = df.groupby(["date", "canon_name"])["actual"].transform("sum")
    df = df[day_point_total > 0]

    df = df.copy()
    names = _sku_names()
    if "Позиция" in df.columns:
        blank = df["Позиция"].isna()
        df.loc[blank, "Позиция"] = (df.loc[blank, "_sku_key"].map(names)
                                    .fillna(df.loc[blank, "_sku_key"]))
    else:
        df["Позиция"] = df["_sku_key"].map(names).fillna(df["_sku_key"])
    if "Группа" in df.columns:
        df["Группа"] = df["Группа"].fillna("вне расчёта")
    else:
        df["Группа"] = "вне расчёта"

    # Тройное сравнение: правки сотрудника поверх прогноза + перенос-заявка 1С
    eff = _edit_map(sorted(df["date"].unique()))
    if eff:
        keys = list(zip(df["date"], df["_point_key"], df["_sku_key"]))
        df["edit_qty"] = [eff.get(k, np.nan) for k in keys]
    else:
        df["edit_qty"] = np.nan
    # ⚠️ Факт заявки — ТОЛЬКО настоящая заявка: перенос из 1С, а если сотрудник
    # правил наше число в вебе — прогноз с его правкой. Раньше здесь при пустой
    # заявке подставлялся наш же прогноз (12.08: четверть строк, на Красного
    # Знамени почти половина) — страница сравнивала нас с нами и завышала
    # «точность заявки». Нет заявки и нет правки → NaN, строка выпадает из
    # сравнения и живёт в отдельном блоке «продавалось без заявки».
    has_edit = df["edit_qty"].notna()
    df["order_fact"] = np.where(
        df["human"] > 0, df["human"],
        np.where(has_edit, df["edit_qty"], np.nan))
    df["order_missing"] = df["order_fact"].isna()
    df["err_order"] = df["order_fact"] - df["actual"]
    return df.reset_index(drop=True)


@data.ttl_cache(seconds=900)
def _price_map() -> tuple[dict, float]:
    """Цены по (point_key, sku_key): медиана avg_price за последние 28 дней полки
    (пары без свежих продаж — медиана всей истории), плюс общая медиана-fallback."""
    for fname in ("shelf_rolling.parquet", "shelf_daily.parquet"):
        p = data.OUTPUT / fname
        if not p.exists():
            continue
        shelf = data.read_parquet_stable(p)
        need = {"point_key", "sku_key", "trade_day", "avg_price"}
        if not need.issubset(shelf.columns):
            continue
        shelf = filter_frame(
            shelf[list(need)], "metric_scope", date_col="trade_day"
        ).dropna(subset=["avg_price"])
        shelf = shelf[shelf["avg_price"] > 0]
        if shelf.empty:
            continue
        per_all = shelf.groupby(["point_key", "sku_key"])["avg_price"].median()
        last = shelf["trade_day"].max()
        recent = shelf[shelf["trade_day"] >= last - pd.Timedelta(days=27)]
        prices = per_all.to_dict()
        if not recent.empty:
            prices.update(recent.groupby(["point_key", "sku_key"])["avg_price"].median().to_dict())
        return prices, float(shelf["avg_price"].median())
    return {}, 0.0


# --- метрики ------------------------------------------------------------------

def _wape(d: pd.DataFrame, col: str = FC) -> float | None:
    a = d["actual"].sum()
    return round(float(100 * (d[col] - d["actual"]).abs().sum() / a), 1) if a else None


def _bias(d: pd.DataFrame, col: str = FC) -> float | None:
    a = d["actual"].sum()
    return round(float(100 * (d[col] - d["actual"]).sum() / a), 1) if a else None


def _money(d: pd.DataFrame, err_col: str = "err") -> dict:
    """Деньги промаха построчно, без сальдирования. Суммы — в тысячах рублей."""
    if d.empty:
        return dict(_ZERO_MONEY)
    prices, med = _price_map()
    keys = list(zip(d["_point_key"], d["_sku_key"]))
    price = np.array([prices.get(k, np.nan) for k in keys], dtype=float)
    fallback = int(np.isnan(price).sum())
    price = np.where(np.isnan(price), med, price)
    err = d[err_col].to_numpy(dtype=float)
    over = float((np.clip(err, 0, None) * price * OVER_COST_SHARE).sum())
    under = float((np.clip(-err, 0, None) * price * UNDER_MARGIN_SHARE).sum())
    return {
        "over_k": round(over / 1000, 1),
        "under_k": round(under / 1000, 1),
        "miss_k": round((over + under) / 1000, 1),
        "priced_rows": int(len(d) - fallback),
        "fallback_rows": fallback,
    }


def _metrics(d: pd.DataFrame, col: str, err_col: str) -> dict:
    return {"wape": _wape(d, col), "bias": _bias(d, col), "money": _money(d, err_col)}


def _edit_stats(d: pd.DataFrame) -> dict:
    """Правки сотрудника на строках периода: сколько строк и суммарный сдвиг к прогнозу."""
    if d.empty or "edit_qty" not in d.columns:
        return {"rows": 0, "delta_units": 0, "up_units": 0, "down_units": 0}
    ed = d[d["edit_qty"].notna()]
    if ed.empty:
        return {"rows": 0, "delta_units": 0, "up_units": 0, "down_units": 0}
    diff = ed["edit_qty"] - ed[FC]
    return {
        "rows": int(len(ed)),
        "delta_units": int(round(float(diff.sum()))),
        "up_units": int(round(float(diff[diff > 0].sum()))),
        "down_units": int(round(float(-diff[diff < 0].sum()))),
    }


# --- списания из снимка БД ----------------------------------------------------

@data.ttl_cache(seconds=300)
def _writeoffs(frm: str, to: str, point: str | None) -> dict:
    """Списания за период по скоупу прогноза: шт и ₽ + до какой даты есть данные.
    Снимка может не быть — тогда units/rub = None (UI покажет «—»)."""
    try:
        with data.connect_snapshot() as con:
            args = [GROUP_LIKE, GROUP_NOT_LIKE, GROUP_NOT_LIKE_BAR, frm, to]
            point_sql = ""
            if point:
                args.append(point)
                point_sql = " AND nm.canon_name = ?"
            units, rub = con.execute(f"""
                WITH {canon_cte()},
                nm AS (SELECT canon_name, point_key AS pt FROM mart.dim_point
                       WHERE role IN ('market', 'market_hub'))
                SELECT sum(f.qty_writeoff), sum(f.wo_sum)
                FROM mart.fact_writeoff_daily f
                JOIN c  ON c.point_key = f.point_key
                JOIN nm ON nm.pt = c.pt
                JOIN mart.dim_sku s ON s.sku_key = f.sku_key
                WHERE s.group_path LIKE ? AND s.group_path NOT LIKE ?
                  AND s.group_path NOT LIKE ?
                  AND {sql_exclusion_predicate(
                      "metric_scope", point_expr="c.pt", sku_expr="f.sku_key",
                      date_expr="f.wo_date")}
                  AND f.wo_date >= ? AND f.wo_date <= ?{point_sql}
            """, args).fetchone()
            last_wo = con.execute(
                "SELECT max(wo_date) FROM mart.fact_writeoff_daily").fetchone()[0]
        return {
            "units": round(float(units or 0), 1),
            "rub": round(float(rub or 0)),
            "from": frm, "to": to,
            "last_wo_date": str(last_wo)[:10] if last_wo is not None else None,
        }
    except Exception:
        logger.exception("accuracy: списания из снимка не прочитались ({f}–{t})",
                         f=frm, t=to)
        return {"units": None, "rub": None, "from": frm, "to": to, "last_wo_date": None}


# --- выборка ------------------------------------------------------------------

def _period_dates(df: pd.DataFrame, period: str) -> list[str]:
    days = sorted(df["date"].unique())
    n = PERIOD_DAYS[period]
    return days if n is None else days[-n:]


def _slice(period: str, point: str | None = None) -> pd.DataFrame:
    df = _shadow_all()
    if df.empty:
        return df
    d = df[df["date"].isin(_period_dates(df, period))]
    if point:
        d = d[d["canon_name"] == point]
    return d


@data.ttl_cache(seconds=600)
def points() -> list[str]:
    """Точки для выпадашки: из сверки нового движка; пока её нет — из снимка БД."""
    df = _shadow_all()
    if not df.empty:
        return sorted(df["canon_name"].astype(str).unique())
    try:
        with data.connect_snapshot() as con:
            rows = con.execute("""
                SELECT DISTINCT canon_name FROM mart.dim_point
                WHERE role IN ('market', 'market_hub') AND canon_name IS NOT NULL
                ORDER BY 1
            """).fetchall()
        return [r[0] for r in rows]
    except Exception:
        return []


# --- публичные функции (их зовёт routes_accuracy) -----------------------------

def summary(period: str = "7d", level: str = "network", point: str | None = None) -> dict:
    """Свод точности за период: сеть целиком или одна точка (level='point')."""
    if period not in PERIOD_DAYS:
        raise ValueError("period должен быть last / 7d / 30d / all")
    if level not in LEVELS:
        raise ValueError("level должен быть network или point")
    if level == "network":
        point = None

    df = _shadow_all()
    if point and not df.empty:
        df = df[df["canon_name"] == point]
    all_days = sorted(df["date"].unique()) if not df.empty else []
    epoch = engine_epoch()
    # Список исключений — сетевая управляющая метаинформация. В кабинете
    # отдельной точки не раскрываем его: сами исключения уже применены внутри
    # ``_shadow_all``, а менеджеру достаточно диапазона фактов своей точки.
    epoch_excl = ([] if point else
                  [x for x in excluded_days() if x >= epoch.isoformat()])
    base = {
        "period": period, "level": level, "point": point,
        "epoch": epoch.isoformat(),
        "available_days": len(all_days),               # дней сверки накоплено с эпохи
        "last_date": all_days[-1] if all_days else None,
        "points": [point] if point else points(),
        "excluded_days": epoch_excl,
    }

    d = _slice(period, point)
    if d.empty:
        return {**base, "dates": None, "rows": 0,
                "forecast_units": 0, "order_units": 0, "actual_units": 0,
                "forecast": {"wape": None, "bias": None, "money": dict(_ZERO_MONEY)},
                "order": {"wape": None, "bias": None, "money": dict(_ZERO_MONEY)},
                "edits": {"rows": 0, "delta_units": 0, "up_units": 0, "down_units": 0},
                "writeoffs": {"units": None, "rub": None, "from": None, "to": None,
                              "last_wo_date": None},
                "sellout_rows": 0, "by_point": [], "daily": []}

    days = sorted(d["date"].unique())
    # Заявку считаем ТОЛЬКО там, где она была: иначе сравниваем сами с собой
    ordered = d[~d["order_missing"]]

    by_point = []
    for name, g in d.groupby("canon_name"):
        go = g[~g["order_missing"]]
        by_point.append({
            "point": str(name),
            "rows": int(len(g)),
            "forecast": int(round(float(g[FC].sum()))),
            "order": int(round(float(go["order_fact"].sum()))),
            "actual": int(round(float(g["actual"].sum()))),
            "wape_forecast": _wape(g),
            "wape_order": _wape(go, "order_fact") if len(go) else None,
            "rows_no_order": int(g["order_missing"].sum()),
            "miss_k": _money(g, "err")["miss_k"],
        })
    by_point.sort(key=lambda r: r["point"])

    daily = []
    for day, g in d.groupby("date"):
        go = g[~g["order_missing"]]
        daily.append({
            "date": str(day),
            "forecast": int(round(float(g[FC].sum()))),
            "order": int(round(float(go["order_fact"].sum()))),
            "sold": int(round(float(g["actual"].sum()))),
            "wape_forecast": _wape(g),
            "wape_order": _wape(go, "order_fact") if len(go) else None,
        })
    daily.sort(key=lambda r: r["date"])

    return {
        **base,
        "dates": {"from": days[0], "to": days[-1], "n_days": len(days)},
        "rows": int(len(d)),
        "forecast_units": int(round(float(d[FC].sum()))),
        "order_units": int(round(float(ordered["order_fact"].sum()))),
        "actual_units": int(round(float(d["actual"].sum()))),
        "forecast": _metrics(d, FC, "err"),
        "order": (_metrics(ordered, "order_fact", "err_order") if len(ordered)
                  else {"wape": None, "bias": None, "money": dict(_ZERO_MONEY)}),
        # Строки, где заявки в 1С не было, а продажи есть: часть ассортимента
        # точка делает сама (на Точки 1 это заметная доля объёма).
        # В сравнение «ПО против заявки» не входят — показываем отдельно.
        "no_order": {
            "rows": int(d["order_missing"].sum()),
            "sold_units": int(round(float(d.loc[d["order_missing"], "actual"].sum()))),
            "share_pct": (round(100 * float(d.loc[d["order_missing"], "actual"].sum())
                                / float(d["actual"].sum()), 1)
                          if float(d["actual"].sum()) else 0.0),
            "rows_with_order": int(len(ordered)),
        },
        "edits": _edit_stats(d),
        "writeoffs": _writeoffs(days[0], days[-1], point),
        "sellout_rows": int(d["sellout"].sum()),
        "by_point": by_point,
        "daily": daily,
    }


def top_miss(period: str = "7d", direction: str = "down", n: int = 15,
             point: str | None = None) -> list[dict]:
    """Топ промахов ПО. down: прогноз < продаж — недодали, настоящие потери.
    up: прогноз > продаж — лишнее (при sellout может быть и правотой модели)."""
    if period not in PERIOD_DAYS:
        raise ValueError("period должен быть last / 7d / 30d / all")
    d = _slice(period, point)
    if d.empty:
        return []
    sel = d[d["err"] < 0].nsmallest(n, "err") if direction == "down" \
        else d[d["err"] > 0].nlargest(n, "err")
    rows = []
    for _, r in sel.iterrows():
        rows.append({
            "date": str(r["date"]),
            "point": str(r["canon_name"]),
            "sku": str(r["Позиция"]),
            "group": str(r["Группа"]),
            "forecast": int(round(float(r[FC]))),
            # заявки могло не быть вовсе (позиция своего производства) — тогда NaN,
            # и int(nan) раньше ронял ручку целиком в 500
            "order": (None if bool(r["order_missing"])
                      else int(round(float(r["order_fact"])))),
            "actual": int(round(float(r["actual"]))),
            "err": int(round(float(r["err"]))),
            "sellout": bool(r["sellout"]),
        })
    return rows


def _xlsx_safe_frame(rows: list[dict], columns: list[str]) -> pd.DataFrame:
    """DataFrame для Excel без интерпретации названий товаров как формул."""
    frame = pd.DataFrame(rows, columns=columns)
    for column in frame.columns:
        frame[column] = frame[column].map(
            lambda value: ("'" + value
                           if isinstance(value, str)
                           and value.startswith(("=", "+", "-", "@"))
                           else value))
    return frame


def build_export_xlsx(period: str = "7d", level: str = "network",
                      point: str | None = None, n: int = 15) -> tuple[Path, str]:
    """Выгрузить текущий срез вкладки «Точность» в один проверяемый Excel.

    В книге есть все три таблицы страницы, дневная динамика и методика. Пустая
    заявка остаётся пустой, а не превращается в ноль: это важно для позиций,
    которые точка производит сама или по которым документ заявки не найден.
    """
    if period not in PERIOD_DAYS:
        raise ValueError("period должен быть last / 7d / 30d / all")
    if level not in LEVELS:
        raise ValueError("level должен быть network или point")
    if level == "point" and not point:
        raise ValueError("Для уровня «Точка» нужно выбрать точку")
    if not 1 <= int(n) <= 100:
        raise ValueError("Число строк топа должно быть от 1 до 100")

    current = summary(period, level, point)
    down = top_miss(period, "down", n, point)
    up = top_miss(period, "up", n, point)
    dates = current.get("dates") or {}
    period_text = (f"{dates.get('from')} — {dates.get('to')}"
                   if dates.get("from") and dates.get("to") else "данных нет")
    no_order = current.get("no_order") or {}

    info = _xlsx_safe_frame([
        {"Параметр": "Сформировано", "Значение": f"{datetime.now():%d.%m.%Y %H:%M}"},
        {"Параметр": "Период", "Значение": period_text},
        {"Параметр": "Охват", "Значение": point or "вся сеть"},
        {"Параметр": "Строк сверки", "Значение": current.get("rows", 0)},
        {"Параметр": "Строк без документа заявки",
         "Значение": no_order.get("rows", 0)},
        {"Параметр": "Методика",
         "Значение": ("WAPE считается построчно к продажам. Денежный промах: "
                      "перезаказ × 0,33 и недозаказ × 0,67 по цене позиции.")},
        {"Параметр": "Обозначение пустой заявки",
         "Значение": ("Пустая ячейка означает: документ заявки для строки не найден. "
                      "Это не нулевой заказ и не должно подменяться числом 0.")},
    ], ["Параметр", "Значение"])

    by_point = _xlsx_safe_frame([{
        "Точка": row.get("point"),
        "Прогноз ПО, шт": row.get("forecast"),
        "Факт заявки, шт": row.get("order"),
        "Продано, шт": row.get("actual"),
        "WAPE ПО, %": row.get("wape_forecast"),
        "WAPE заявки, %": row.get("wape_order"),
        "Промах ПО, тыс ₽": row.get("miss_k"),
        "Строк без документа заявки": row.get("rows_no_order"),
    } for row in current.get("by_point", [])], [
        "Точка", "Прогноз ПО, шт", "Факт заявки, шт", "Продано, шт",
        "WAPE ПО, %", "WAPE заявки, %", "Промах ПО, тыс ₽",
        "Строк без документа заявки",
    ])
    daily = _xlsx_safe_frame([{
        "Дата": row.get("date"),
        "Прогноз ПО, шт": row.get("forecast"),
        "Факт заявки, шт": row.get("order"),
        "Продано, шт": row.get("sold"),
        "WAPE ПО, %": row.get("wape_forecast"),
        "WAPE заявки, %": row.get("wape_order"),
    } for row in current.get("daily", [])], [
        "Дата", "Прогноз ПО, шт", "Факт заявки, шт", "Продано, шт",
        "WAPE ПО, %", "WAPE заявки, %",
    ])

    def top_frame(rows: list[dict]) -> pd.DataFrame:
        return _xlsx_safe_frame([{
            "Дата": row.get("date"), "Точка": row.get("point"),
            "Позиция": row.get("sku"), "Группа": row.get("group"),
            "Прогноз ПО, шт": row.get("forecast"),
            # None оставляем пустым: нулём считается только настоящий 0.
            "Факт заявки, шт": row.get("order"),
            "Продано, шт": row.get("actual"), "Промах, шт": row.get("err"),
            "Статус": "распродано" if row.get("sellout") else "",
        } for row in rows], [
            "Дата", "Точка", "Позиция", "Группа", "Прогноз ПО, шт",
            "Факт заявки, шт", "Продано, шт", "Промах, шт", "Статус",
        ])

    sheets = [
        ("О расчёте", info),
        ("По точкам", by_point),
        ("По дням", daily),
        ("Недозаказ", top_frame(down)),
        ("Перезаказ", top_frame(up)),
    ]
    fd, tmp = make_export_tempfile(prefix="accuracy_")
    os.close(fd)
    path = Path(tmp)
    try:
        from openpyxl.styles import Alignment, Font, PatternFill  # noqa: PLC0415

        with pd.ExcelWriter(path, engine="openpyxl") as writer:
            for sheet_name, frame in sheets:
                frame.to_excel(writer, sheet_name=sheet_name, index=False)
                ws = writer.sheets[sheet_name]
                ws.freeze_panes = "A2"
                ws.auto_filter.ref = ws.dimensions
                for cell in ws[1]:
                    cell.font = Font(bold=True, color="FFFFFF")
                    cell.fill = PatternFill("solid", fgColor="A61E22")
                    cell.alignment = Alignment(wrap_text=True, vertical="center")
                for idx, column in enumerate(frame.columns, start=1):
                    width = 44 if column in ("Позиция", "Значение") else 24
                    if column in ("Дата", "Статус"):
                        width = 15
                    ws.column_dimensions[ws.cell(1, idx).column_letter].width = width
    except Exception:
        path.unlink(missing_ok=True)
        raise
    filename = f"forecast_Точность_{period}_{date.today():%Y-%m-%d}.xlsx"
    return path, filename
