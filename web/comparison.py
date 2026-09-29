# -*- coding: utf-8 -*-
"""Вкладка «Сравнение»: формула КВП против ML-ядра против факта, по дням.

Считает по файлам analysis/output/duel/<дата>.parquet — их готовит
predict.duel: оба движка пересчитаны прод-путём на данных, доступных к тому
дню (модель ядра обучена не позже D−3, проверка стоит в самом пересчёте).
Веб здесь ничего не моделирует, только агрегирует.

Деньги промаха — те же доли, что на «Точности»: перевоз стоит себестоимости
(0.33 цены), недовоз — маржи (0.67). Эталон переключается:
  * «спрос» (по умолчанию) — продано + недопродано после того, как позиция
    кончилась. Честный ориентир: продажи обрезаны тем, что стояло на полке;
  * «продажи» — то, что видно в 1С. Понятнее глазу, но занижает недовоз.

Исключаются строки: срыв поставки (спрос неизвестен), точко-дни без отчёта
о продажах, дни из excluded_days.json (инвентаризации и прочая нетипичность);
при эталоне «спрос» — ещё и дни, по которым не пришли акты списаний.
"""
from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
from loguru import logger

from . import data
from .accuracy import OVER_COST_SHARE, UNDER_MARGIN_SHARE, excluded_days
from prognoz.assortment import filter_frame

DUEL_DIR = data.OUTPUT / "duel"

PERIOD_DAYS = {"last": 1, "7d": 7, "30d": 30, "all": None}
BASES = ("demand", "sold")
METHODS = (("ml", "ML-ядро"), ("baseline", "Формула КВП"), ("human", "Заявка сотрудника"))


# --- загрузка -----------------------------------------------------------------

@data.ttl_cache(seconds=300)
def _all() -> pd.DataFrame:
    """Все дни пересчёта одним кадром. Свежий файл читаем «стабильно»."""
    files = sorted(DUEL_DIR.glob("*.parquet"))
    if not files:
        return pd.DataFrame()
    fresh_from = (date.today() - timedelta(days=1)).isoformat()
    frames = [data.read_parquet_stable(p) if p.stem >= fresh_from else pd.read_parquet(p)
              for p in files]
    df = pd.concat(frames, ignore_index=True)
    df["date"] = df["date"].astype(str).str[:10]
    df["canon_name"] = df["canon_name"].fillna("—")
    df["sku_name"] = df["sku_name"].fillna(df["_sku_key"])
    df = filter_frame(
        df, "metric_scope", point_col="_point_key", sku_col="_sku_key",
        date_col="date")
    excl = set(excluded_days())
    if excl:
        df = df[~df["date"].isin(excl)]
    return df


def days_available(point: str | None = None) -> list[str]:
    df = _all()
    if point and not df.empty:
        df = df[df["canon_name"] == point]
    return sorted(df["date"].unique()) if not df.empty else []


@data.ttl_cache(seconds=600)
def points() -> list[str]:
    df = _all()
    if df.empty:
        return []
    return sorted(x for x in df["canon_name"].astype(str).unique() if x != "—")


def _clean(df: pd.DataFrame, base: str) -> pd.DataFrame:
    """Строки, на которых сравнение имеет смысл."""
    d = df[~df["supply_gap"] & ~df["no_report"]]
    if base == "demand":
        d = d[d["wo_settled"]]
    return d


def _slice(period: str, point: str | None, base: str) -> pd.DataFrame:
    df = _all()
    if df.empty:
        return df
    # Сначала ограничиваем точку и только затем выбираем последние N дней.
    # Иначе отстающая по загрузке точка теряет собственные последние дни, а её
    # кабинет косвенно зависит от календаря остальных магазинов.
    if point:
        df = df[df["canon_name"] == point]
    if df.empty:
        return df
    days = sorted(df["date"].unique())
    n = PERIOD_DAYS[period]
    if n is not None:
        days = days[-n:]
    d = df[df["date"].isin(days)]
    return _clean(d, base)


# --- метрики ------------------------------------------------------------------

def _money(d: pd.DataFrame, col: str, ref: str) -> dict:
    """Деньги промаха метода в тыс ₽: перевоз ×0.33 + недовоз ×0.67."""
    err = d[col] - d[ref]
    over = float((err.clip(lower=0) * d["price"]).sum() * OVER_COST_SHARE)
    under = float(((-err).clip(lower=0) * d["price"]).sum() * UNDER_MARGIN_SHARE)
    return {"over_k": round(over / 1000, 1), "under_k": round(under / 1000, 1),
            "miss_k": round((over + under) / 1000, 1)}


def _wape(d: pd.DataFrame, col: str, ref: str) -> float | None:
    tot = float(d[ref].sum())
    if not tot:
        return None
    return round(100 * float((d[col] - d[ref]).abs().sum()) / tot, 1)


def _bias(d: pd.DataFrame, col: str, ref: str) -> float | None:
    tot = float(d[ref].sum())
    if not tot:
        return None
    return round(100 * float((d[col] - d[ref]).sum()) / tot, 1)


def _method_stats(d: pd.DataFrame, ref: str) -> list[dict]:
    out = []
    for key, name in METHODS:
        out.append({
            "key": key, "name": name,
            "units": int(round(float(d[key].sum()))),
            "wape": _wape(d, key, ref),
            "bias": _bias(d, key, ref),
            **_money(d, key, ref),
        })
    return out


def _winner(row: dict) -> str:
    """Кто ближе в этот день по деньгам промаха."""
    pairs = [(row["miss_ml_k"], "ml"), (row["miss_baseline_k"], "baseline"),
             (row["miss_human_k"], "human")]
    return min(pairs)[1]


# --- публичные функции --------------------------------------------------------

def summary(period: str = "30d", point: str | None = None,
            base: str = "demand", scope: str = "paired") -> dict:
    """Свод сравнения. scope='paired' — только строки, где сотрудник заказывал.

    Множество выбрано умолчанием осознанно: на строках без его заявки человеку
    засчитывается ноль против спроса, а таких строк много там, где точка часть
    ассортимента производит сама (на Точки 1 — сотни строк). На «всех
    строках» человек из-за этого выглядит вдвое хуже, чем он есть.
    """
    if period not in PERIOD_DAYS:
        raise ValueError("period должен быть last / 7d / 30d / all")
    if base not in BASES:
        raise ValueError("base должен быть demand или sold")
    if scope not in ("paired", "all"):
        raise ValueError("scope должен быть paired или all")

    all_days = days_available(point)
    ref = base
    base_note = ("спрос полки: продано + недопродано после того, как позиция кончилась"
                 if base == "demand" else "продажи по документу 1С")
    out = {
        "period": period, "point": point, "base": base, "base_note": base_note,
        "scope": scope, "points": [point] if point else points(),
        "available_days": len(all_days),
        "first_date": all_days[0] if all_days else None,
        "last_date": all_days[-1] if all_days else None,
        # Список исключений управляется на уровне сети и доступен администратору.
        # В ответе отдельной точки он не должен раскрывать сетевые даты.
        "excluded_days": [] if point else excluded_days(),
    }

    full = _slice(period, point, base)
    if full.empty:
        return {**out, "rows": 0, "dates": None, "methods": [], "daily": [],
                "by_point": [], "alt": None, "sold_units": 0, "demand_units": 0,
                "chek_lag": None}

    # Основное множество — по scope; второе идёт справочной строкой, чтобы
    # разница между «все строки» и «строки с заявкой» была видна, а не скрыта.
    d = full[full["has_order"]] if scope == "paired" else full
    if d.empty:                                    # заявок в периоде не было вовсе
        d, scope = full, "all"
        out["scope"] = scope

    days = sorted(d["date"].unique())
    out.update({
        "rows": int(len(d)),
        "rows_all": int(len(full)),
        "rows_paired": int(full["has_order"].sum()),
        "dates": {"from": days[0], "to": days[-1], "n_days": len(days)},
        "sold_units": int(round(float(d["sold"].sum()))),
        "demand_units": int(round(float(d["demand"].sum()))),
        "lost_units": int(round(float(d["lost_units"].sum()))),
        "methods": _method_stats(d, ref),
    })

    # Дни, где чеки ещё не добраны: недопродажи там занижены не по факту, а по
    # незнанию — показываем это явно, а не молчим.
    unsettled = sorted(d.loc[~d["chek_settled"], "date"].unique())
    out["chek_lag"] = {"days": len(unsettled),
                       "from": unsettled[0] if unsettled else None,
                       "to": unsettled[-1] if unsettled else None}

    daily = []
    for day, g in d.groupby("date"):
        row = {
            "date": str(day),
            "baseline": int(round(float(g["baseline"].sum()))),
            "ml": int(round(float(g["ml"].sum()))),
            "human": int(round(float(g["human"].sum()))),
            "sold": int(round(float(g["sold"].sum()))),
            "demand": int(round(float(g["demand"].sum()))),
            "miss_ml_k": _money(g, "ml", ref)["miss_k"],
            "miss_baseline_k": _money(g, "baseline", ref)["miss_k"],
            "miss_human_k": _money(g, "human", ref)["miss_k"],
            "chek_settled": bool(g["chek_settled"].all()),
        }
        row["winner"] = _winner(row)
        daily.append(row)
    daily.sort(key=lambda r: r["date"])
    out["daily"] = daily

    by_point = []
    for name, g in d.groupby("canon_name"):
        by_point.append({
            "point": str(name),
            "rows": int(len(g)),
            "baseline": int(round(float(g["baseline"].sum()))),
            "ml": int(round(float(g["ml"].sum()))),
            "human": int(round(float(g["human"].sum()))),
            "sold": int(round(float(g["sold"].sum()))),
            "demand": int(round(float(g["demand"].sum()))),
            "miss_ml_k": _money(g, "ml", ref)["miss_k"],
            "miss_baseline_k": _money(g, "baseline", ref)["miss_k"],
            "miss_human_k": _money(g, "human", ref)["miss_k"],
            "rows_no_order": int((~g["has_order"]).sum()),
        })
    by_point.sort(key=lambda r: r["point"])
    out["by_point"] = by_point

    # Второе множество справочной строкой: если смотрим парное — покажем «все
    # строки», и наоборот. Разница между ними и есть цена вопроса «а сколько
    # позиций сотрудник вообще не заказывал».
    other = full if scope == "paired" else full[full["has_order"]]
    out["alt"] = ({"scope": "all" if scope == "paired" else "paired",
                   "rows": int(len(other)), "methods": _method_stats(other, ref)}
                  if len(other) and len(other) != len(d) else None)
    return out


def top_gap(period: str = "30d", point: str | None = None, n: int = 20,
            base: str = "demand") -> list[dict]:
    """Строки, где движки разошлись сильнее всего — и кто оказался прав."""
    d = _slice(period, point, base)
    if d.empty:
        return []
    d = d.copy()
    # Таблица показывает целые количества, поэтому и видимое расхождение считаем
    # из этих же целых чисел. Иначе возможно противоречие «10 / 10 / разница 0,4».
    d["ml_display"] = d["ml"].map(lambda value: int(round(float(value))))
    d["baseline_display"] = d["baseline"].map(
        lambda value: int(round(float(value))))
    d["gap"] = (d["ml_display"] - d["baseline_display"]).abs()
    # Вторичные ключи делают порядок воспроизводимым при одинаковом разрыве.
    sel = d.sort_values(
        ["gap", "date", "canon_name", "sku_name"],
        ascending=[False, False, True, True], kind="mergesort",
    ).head(n)
    ref = base
    rows = []
    for _, r in sel.iterrows():
        err_ml, err_kr = abs(r["ml"] - r[ref]), abs(r["baseline"] - r[ref])
        rows.append({
            "date": str(r["date"]),
            "point": str(r["canon_name"]),
            "sku": str(r["sku_name"]),
            "baseline": int(r["baseline_display"]),
            "ml": int(r["ml_display"]),
            "gap_units": int(r["gap"]),
            "human": int(round(float(r["human"]))),
            "sold": int(round(float(r["sold"]))),
            "demand": int(round(float(r["demand"]))),
            "closer": ("ml" if err_ml < err_kr else
                       "baseline" if err_kr < err_ml else "tie"),
        })
    return rows


def export_rows(period: str = "30d", point: str | None = None,
                base: str = "demand", scope: str = "paired") -> pd.DataFrame:
    """Плоская таблица для Excel: строка = день × точка × позиция."""
    d = _slice(period, point, base)
    if scope == "paired" and not d.empty:
        d = d[d["has_order"]]
    if d.empty:
        return pd.DataFrame()
    out = d[["date", "canon_name", "sku_name", "Группа", "baseline", "ml", "human",
             "sold", "lost_units", "demand", "price", "has_order", "chek_settled"]].copy()
    out.columns = ["Дата", "Точка", "Позиция", "Группа", "Формула КВП, шт",
                   "ML-ядро, шт", "Заявка сотрудника, шт", "Продано, шт",
                   "Недопродано, шт", "Спрос, шт", "Цена, ₽", "Заявка была",
                   "Чеки добраны"]
    return out.sort_values(["Дата", "Точка", "Позиция"])


def health(point: str | None = None) -> dict:
    """Свежесть пересчёта — для строки на странице и для помощника."""
    days = days_available(point)
    # Время изменения файлов относится ко всему сетевому выпуску. Менеджеру
    # показываем только свежесть данных его точки, без сетевого побочного канала.
    last_mtime = None
    if point is None:
        try:
            last_mtime = max(p.stat().st_mtime for p in DUEL_DIR.glob("*.parquet"))
        except ValueError:
            pass
        except OSError:
            logger.exception("comparison: не прочитались файлы пересчёта")
    return {"days": len(days), "first": days[0] if days else None,
            "last": days[-1] if days else None, "mtime": last_mtime}
