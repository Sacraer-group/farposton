# -*- coding: utf-8 -*-
"""Агрегаты вкладки «Аналитика»: динамика продаж, профиль по часам и дням недели,
списания/уценка, топ дефицита с «полки» + управление списком нетипичных дней.

Источники (только чтение, всё готовит дневной прогон):
- снимок DuckDB (mart.fact_sales_daily, mart.cheki_sales_net,
  mart.fact_writeoff_daily, mart.dim_point, mart.dim_sku) — через
  data.connect_snapshot, короткоживущим read-only подключением;
- analysis/output/shelf_rolling.parquet (fallback shelf_daily.parquet) — «полка»:
  недопродажи lost_units/lost_rub_margin и списания wo_units/wo_rub_cost;
- analysis/config/excluded_days.json — единственный файл, который модуль ПИШЕТ
  (атомарно, tmp + os.replace); его же читает ночной прогон при обучении.

Скоуп — как в src/prognoz/predict/reconcile.py: группы GROUP_LIKE минус
перепродажа и бар, точки канонизируются через canon_cte() (merged → основная).
"""
from __future__ import annotations

from . import paths  # noqa: F401  — кладёт prognoz/src в sys.path ДО импорта ядра

import json
import os
import threading
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd

from . import data
from .config import ROOT
from .export import make_export_tempfile

from prognoz.assortment import (filter_frame,
                                provenance as assortment_provenance,
                                sql_exclusion_predicate)
from prognoz.scope import (GROUP_LIKE, GROUP_NOT_LIKE, GROUP_NOT_LIKE_BAR,
                           canon_cte, sku_twins_cte)

_config_root = os.getenv("PROGNOZ_CONFIG_DIR", "").strip()
CONFIG_ROOT = (Path(_config_root).resolve()
               if _config_root else ROOT / "analysis" / "config")
EXCLUDED_FILE = CONFIG_ROOT / "excluded_days.json"
SHELF_ROLLING = data.OUTPUT / "shelf_rolling.parquet"
SHELF_DAILY = data.OUTPUT / "shelf_daily.parquet"

# Один SQL-кусок скоупа на все запросы (алиас dim_sku всегда s)
_SCOPE_SQL = ("s.group_path LIKE ? AND s.group_path NOT LIKE ? "
              "AND s.group_path NOT LIKE ? AND "
              + sql_exclusion_predicate(
                  "metric_scope", point_expr="c.pt", sku_expr="f.sku_key"))
_SCOPE_ARGS = [GROUP_LIKE, GROUP_NOT_LIKE, GROUP_NOT_LIKE_BAR]

WD_RU = ("пн", "вт", "ср", "чт", "пт", "сб", "вс")

# Пресеты периодов: «последние N дней до свежей продажи»
PRESET_DAYS = {"day": 1, "week": 7, "month": 30, "quarter": 91, "year": 365}
PRESET_RU = {"day": "день", "week": "неделя", "month": "месяц",
             "quarter": "квартал", "year": "год"}

# Даты подтверждённых инвентаризационных корректировок. Они остаются в первичных
# данных, но в интерфейсе помечаются отдельно: такой всплеск нельзя объяснять
# качеством ежедневной заявки. Исключать дату из модели можно только после
# согласования протокола с заказчиком.
INVENTORY_ADJUSTMENT_DATES = (
    date(2025, 10, 22), date(2025, 11, 19), date(2025, 12, 17),
    date(2026, 1, 21), date(2026, 2, 18), date(2026, 3, 17),
    date(2026, 4, 14), date(2026, 5, 18), date(2026, 6, 23),
    date(2026, 7, 22),
)

_SALES_DOCUMENT_SECTIONS = {"sales", "yoy", "top_sku", "weekdays"}

# Сверка документа продаж с чеками (08.09): не «точко-дни с расхождением > 0,01 шт»
# (по ним расходится всё, и текст пугал заказчика), а СУММАРНЫЙ процент штук за
# зрелое окно. Последние RECON_LAG_DAYS дней перед свежим чеком не берём — чеки
# отстают. Причина показывается только при |расхождение| > RECON_ALERT_PCT.
RECON_LAG_DAYS = 3
RECON_ALERT_PCT = 3.0


def _reason(code: str, text: str, *, frm: date | str | None = None,
            to: date | str | None = None, severity: str = "warning") -> dict:
    """Единый машиночитаемый формат причины для UI и Excel."""
    return {
        "code": code,
        "severity": severity,
        "from": str(frm)[:10] if frm else None,
        "to": str(to)[:10] if to else None,
        "text": text,
    }


def _fmt_ru_number(value: float | int, digits: int = 0) -> str:
    """Число для клиентского текста: пробелы тысяч и десятичная запятая."""
    return f"{value:,.{digits}f}".replace(",", " ").replace(".", ",")


def _reconciliation_pct(sales_units: float | None,
                        checks_units: float | None) -> float | None:
    """(чеки − документ) / документ, в процентах штук; нет базы — None."""
    if sales_units is None or checks_units is None or float(sales_units) <= 0:
        return None
    return round(100.0 * (float(checks_units) - float(sales_units))
                 / float(sales_units), 1)


def _reconciliation_reason(pct: float | None, *, frm=None, to=None) -> dict | None:
    """Причина sales_checks_not_reconciled человеческим языком или None, если
    расхождение в пределах RECON_ALERT_PCT (или сверять нечего)."""
    if pct is None or abs(pct) <= RECON_ALERT_PCT:
        return None
    direction = "чеки ниже" if pct < 0 else "чеки выше"
    return _reason(
        "sales_checks_not_reconciled",
        f"Документ продаж и кассовые чеки — два разных модуля 1С и расходятся на "
        f"{_fmt_ru_number(abs(pct), 1)}% штук за период ({direction}). "
        "Аналитика и прогноз опираются на чеки; последние 2–3 дня чеки могут "
        "отставать, эти дни помечены предварительными.",
        frm=frm, to=to)


def _period_intersection(frm: date | str | None, to: date | str | None,
                         other_from: date | str | None,
                         other_to: date | str | None) -> tuple[date, date] | None:
    if not all((frm, to, other_from, other_to)):
        return None
    left = max(_parse_iso(str(frm)[:10]), _parse_iso(str(other_from)[:10]))
    right = min(_parse_iso(str(to)[:10]), _parse_iso(str(other_to)[:10]))
    return (left, right) if left <= right else None


@data.ttl_cache(seconds=600)
def quality_overview(point: str | None = None) -> dict:
    """Свежесть и сверка источников без сырых строк.

    Сводная продажа и чек — разные документы. Пока их дневные итоги не сведены,
    интерфейс обязан показывать это рядом с каждым зависимым графиком, а Excel —
    повторять предупреждение.
    """
    with data.connect_snapshot() as con:
        def source_bounds(table: str, date_col: str) -> tuple[date | None, date | None]:
            args = [*_SCOPE_ARGS]
            point_sql = _point_clause(point, args)
            return con.execute(f"""
                WITH {_cte()}
                SELECT min(f.{date_col}), max(f.{date_col})
                FROM {table} f
                JOIN c  ON c.point_key = f.point_key
                JOIN nm ON nm.pt = c.pt
                JOIN mart.dim_sku s ON s.sku_key = f.sku_key
                WHERE {_SCOPE_SQL}{point_sql}
            """, args).fetchone()

        sales_from, sales_max = source_bounds("mart.fact_sales_daily", "sale_date")
        checks_from, checks_max = source_bounds("mart.cheki_sales_net", "trade_day")

        wo_bounds_args = [*_SCOPE_ARGS]
        wo_bounds_point = _point_clause(point, wo_bounds_args)
        wo_bounds_cutoff = ""
        if sales_max is not None:
            wo_bounds_cutoff = " AND f.wo_date <= ?"
            wo_bounds_args.append(sales_max)
        writeoffs_from, writeoffs_max = con.execute(f"""
            WITH {_cte()}
            SELECT min(f.wo_date), max(f.wo_date)
            FROM mart.fact_writeoff_daily f
            JOIN c  ON c.point_key = f.point_key
            JOIN nm ON nm.pt = c.pt
            JOIN mart.dim_sku s ON s.sku_key = f.sku_key
            WHERE {_SCOPE_SQL}{wo_bounds_point}{wo_bounds_cutoff}
        """, wo_bounds_args).fetchone()

        sales_args = [*_SCOPE_ARGS]
        sales_point_sql = _point_clause(point, sales_args)
        checks_args = [*_SCOPE_ARGS]
        checks_point_sql = _point_clause(point, checks_args)
        # зрелое окно сверки: до checks_as_of − RECON_LAG_DAYS включительно
        mature_to = (checks_max - timedelta(days=RECON_LAG_DAYS)
                     if checks_max is not None else date(1900, 1, 1))
        recon = con.execute(f"""
            WITH {_cte()},
            sl AS (
                SELECT f.sale_date AS d, nm.canon_name AS point,
                       sum(f.qty_sold - f.qty_returned) AS qty
                FROM mart.fact_sales_daily f
                JOIN c  ON c.point_key = f.point_key
                JOIN nm ON nm.pt = c.pt
                JOIN mart.dim_sku s ON s.sku_key = f.sku_key
                WHERE {_SCOPE_SQL}{sales_point_sql}
                GROUP BY 1, 2),
            ch AS (
                SELECT f.trade_day AS d, nm.canon_name AS point,
                       sum(f.qty * f.sign) AS qty
                FROM mart.cheki_sales_net f
                JOIN c  ON c.point_key = f.point_key
                JOIN nm ON nm.pt = c.pt
                JOIN mart.dim_sku s ON s.sku_key = f.sku_key
                WHERE {_SCOPE_SQL}{checks_point_sql}
                GROUP BY 1, 2),
            paired AS (
                SELECT coalesce(sl.d, ch.d) AS d,
                       coalesce(sl.point, ch.point) AS point,
                       sl.qty AS sales_qty, ch.qty AS check_qty,
                       sl.d IS NOT NULL AS has_sales,
                       ch.d IS NOT NULL AS has_checks
                FROM sl FULL OUTER JOIN ch USING (d, point))
            SELECT min(d) FILTER (WHERE has_sales AND has_checks),
                   max(d) FILTER (WHERE has_sales AND has_checks),
                   count(*) FILTER (WHERE has_sales AND has_checks),
                   count(*) FILTER (WHERE has_sales AND has_checks
                                      AND abs(sales_qty - check_qty) > 0.01),
                   sum(abs(sales_qty - check_qty))
                       FILTER (WHERE has_sales AND has_checks),
                   count(*) FILTER (WHERE has_sales AND NOT has_checks),
                   min(d) FILTER (WHERE has_sales AND NOT has_checks),
                   max(d) FILTER (WHERE has_sales AND NOT has_checks),
                   count(*) FILTER (WHERE has_checks AND NOT has_sales),
                   min(d) FILTER (WHERE has_checks AND NOT has_sales),
                   max(d) FILTER (WHERE has_checks AND NOT has_sales),
                   sum(sales_qty) FILTER (WHERE has_sales AND has_checks AND d <= ?),
                   sum(check_qty) FILTER (WHERE has_sales AND has_checks AND d <= ?),
                   max(d) FILTER (WHERE has_sales AND has_checks AND d <= ?)
            FROM paired
        """, [*sales_args, *checks_args, mature_to, mature_to, mature_to]).fetchone()
        money_args = [*_SCOPE_ARGS]
        money_point_sql = _point_clause(point, money_args)
        money_cutoff_sql = ""
        if sales_max is not None:
            money_cutoff_sql = " AND f.wo_date <= ?"
            money_args.append(sales_max)
        money_nonzero, money_rows = con.execute(f"""
            WITH {_cte()}
            SELECT count(*) FILTER (WHERE f.wo_sum IS NOT NULL AND f.wo_sum <> 0),
                   count(*)
            FROM mart.fact_writeoff_daily f
            JOIN c  ON c.point_key = f.point_key
            JOIN nm ON nm.pt = c.pt
            JOIN mart.dim_sku s ON s.sku_key = f.sku_key
            WHERE {_SCOPE_SQL}{money_point_sql}{money_cutoff_sql}
        """, money_args).fetchone()

    (r_from, r_to, common, mismatched, abs_units,
     missing_checks, missing_checks_from, missing_checks_to,
     missing_sales, missing_sales_from, missing_sales_to,
     mature_sales_units, mature_checks_units, mature_last_day) = recon
    mismatch_pct = round(100 * mismatched / common, 1) if common else None
    money_pct = round(100 * money_nonzero / money_rows, 1) if money_rows else None
    total_diff_pct = _reconciliation_pct(mature_sales_units, mature_checks_units)
    reasons = []
    recon_reason = _reconciliation_reason(total_diff_pct, frm=r_from, to=mature_last_day)
    if recon_reason is not None:
        reasons.append(recon_reason)
    if missing_checks:
        reasons.append(_reason(
            "checks_point_days_missing",
            f"В кассовых чеках отсутствуют {_fmt_ru_number(missing_checks)} "
            "точко-дней, которые "
            "есть в сводном документе продаж. На этих датах чековые и почасовые "
            "графики неполны.",
            frm=missing_checks_from, to=missing_checks_to))
    if missing_sales:
        reasons.append(_reason(
            "sales_point_days_missing",
            f"В сводном документе продаж отсутствуют {_fmt_ru_number(missing_sales)} "
            "точко-дней, "
            "которые есть в кассовых чеках. Динамика продаж и связанные сводки на "
            "этих датах неполны.",
            frm=missing_sales_from, to=missing_sales_to))
    if money_pct is not None and money_pct < 80:
        reasons.append(_reason(
            "writeoff_amount_incomplete",
            f"Сумма списания заполнена только у {money_pct:.1f}% строк актов в "
            "прогнозном ассортименте. Рубли из актов показаны справочно и не "
            "являются полной стоимостью списаний.",
            frm=writeoffs_from, to=writeoffs_max))
    return {
        "status": "warning" if reasons else "ok",
        "title": "Данные сверяются" if reasons else "Источники согласованы",
        "reasons": reasons,
        "sources": {
            "sales_from": str(sales_from) if sales_from else None,
            "sales_as_of": str(sales_max) if sales_max else None,
            "checks_from": str(checks_from) if checks_from else None,
            "checks_as_of": str(checks_max) if checks_max else None,
            "writeoffs_from": str(writeoffs_from) if writeoffs_from else None,
            "writeoffs_as_of": str(writeoffs_max) if writeoffs_max else None,
        },
        "point": point,
        "reconciliation": {
            "from": str(r_from) if r_from else None,
            "to": str(r_to) if r_to else None,
            "common_point_days": int(common or 0),
            "mismatched_point_days": int(mismatched or 0),
            "mismatch_pct": mismatch_pct,
            "absolute_units": round(float(abs_units or 0), 3),
            # суммарная сверка по зрелому окну — то, что видит заказчик в тексте
            "mature_to": str(mature_last_day) if mature_last_day else None,
            "sales_document_units": round(float(mature_sales_units or 0), 3),
            "checks_units": round(float(mature_checks_units or 0), 3),
            "total_diff_pct": total_diff_pct,
            "union_point_days": int((common or 0) + (missing_checks or 0)
                                    + (missing_sales or 0)),
            "missing_in_checks_point_days": int(missing_checks or 0),
            "missing_in_checks_from": (
                str(missing_checks_from) if missing_checks_from else None),
            "missing_in_checks_to": (
                str(missing_checks_to) if missing_checks_to else None),
            "missing_in_sales_point_days": int(missing_sales or 0),
            "missing_in_sales_from": (
                str(missing_sales_from) if missing_sales_from else None),
            "missing_in_sales_to": (
                str(missing_sales_to) if missing_sales_to else None),
        },
        "writeoff_amount_coverage_pct": money_pct,
    }


def section_quality(section: str, frm: date | str | None,
                    to: date | str | None, *, extra: dict | None = None,
                    point: str | None = None) -> dict:
    """Причины неточности конкретного графика и конкретного окна."""
    overview = quality_overview(point)
    reasons: list[dict] = []
    recon = overview["reconciliation"]
    overlap = _period_intersection(frm, to, recon["from"], recon["to"])
    if overlap and section in _SALES_DOCUMENT_SECTIONS:
        reasons.append(_reason(
            "sales_document_preliminary",
            "Числа продаж взяты из сводного документа 1С. За указанный период "
            "они ещё не сопоставлены с кассовыми чеками. Динамику внутри этого "
            "графика смотреть можно; итог в деньгах пока не используйте как "
            "бухгалтерскую сверку.", frm=overlap[0], to=overlap[1]))
    if section == "yoy" and extra and extra.get("comparison_from"):
        reasons.append(_reason(
            "sales_document_comparison_preliminary",
            "Прошлогодняя часть сравнения также рассчитана по сводному документу "
            "продаж 1С, который не сведён с кассовыми чеками. Дельту год к году "
            "используйте как ориентир тренда, а не как финансовую сверку.",
            frm=extra["comparison_from"], to=extra.get("comparison_to")))
    if overlap and section == "hours":
        reasons.append(_reason(
            "checks_not_reconciled",
            "Профиль построен по кассовым чекам. Не сопоставляйте его итоговые "
            "штуки с вкладками на документе продаж до завершения сверки источников.",
            frm=overlap[0], to=overlap[1]))
    if overlap and section == "writeoffs":
        reasons.append(_reason(
            "u40_sales_not_reconciled",
            "Уценка (у40) взята из сводного документа продаж 1С. Её дневные "
            "итоги ещё не сведены с кассовыми чеками; сравнивать уценку со "
            "списаниями можно только как операционный ориентир.",
            frm=overlap[0], to=overlap[1]))
    if section == "top_sku":
        reasons.append(_reason(
            "revenue_is_not_profit",
            "Выручка — сумма продаж до вычета себестоимости и списаний; это не "
            "прибыль позиции.", frm=frm, to=to, severity="info"))
    if section in {"writeoffs", "yoy"}:
        money_pct = (extra or {}).get(
            "writeoff_amount_coverage_pct", overview["writeoff_amount_coverage_pct"])
        if money_pct is not None and money_pct < 80:
            reasons.append(_reason(
                "writeoff_amount_incomplete",
                f"Рублёвая сумма заполнена только у {money_pct:.1f}% строк актов. "
                "Для операционного контроля используйте штуки; сумму актов — "
                "только справочно.", frm=frm, to=to))
    if section == "writeoffs" and to:
        to_d = _parse_iso(str(to)[:10])
        preliminary_from = to_d - timedelta(days=2)
        overlap_prelim = _period_intersection(frm, to, preliminary_from, to_d)
        if overlap_prelim:
            reasons.append(_reason(
                "writeoffs_preliminary",
                "Последние три дня предварительные: ручные акты списания могут "
                "проводиться задним числом в течение 1–3 дней.",
                frm=overlap_prelim[0], to=overlap_prelim[1]))
    if section in {"writeoffs", "yoy"} and frm and to:
        frm_d, to_d = _parse_iso(str(frm)[:10]), _parse_iso(str(to)[:10])
        inventory = [d for d in INVENTORY_ADJUSTMENT_DATES if frm_d <= d <= to_d]
        if inventory:
            dates_ru = ", ".join(f"{d:%d.%m.%Y}" for d in inventory)
            reasons.append(_reason(
                "inventory_adjustments",
                "Инвентаризационные корректировки отражают разбор остатков и "
                f"непроведённых операций, а не качество ежедневной заявки: {dates_ru}.",
                frm=inventory[0], to=inventory[-1]))
    if section in {"shelf", "sellout_speed"}:
        reasons.append(_reason(
            "shelf_is_estimate",
            "Это оценка полки по времени последней продажи, уценке и списаниям, "
            "а не прямой факт нулевого остатка. Упущенные продажи — консервативная "
            "нижняя оценка спроса.", frm=frm, to=to))
        policy = assortment_provenance()
        reasons.append(_reason(
            "assortment_filters_active",
            f"Применена политика ассортимента {policy['assortment_policy_version']} "
            f"({policy['assortment_policy_entries']} правил).",
            frm=frm, to=to, severity="info"))
        if extra and extra.get("preliminary_from"):
            reasons.append(_reason(
                "shelf_unsettled_days_hidden",
                "Более свежие дни скрыты: по ним ещё не созрели акты списания, "
                "поэтому исход дня и оценка дефицита могут измениться.",
                frm=extra["preliminary_from"], to=extra.get("source_to")))
    if extra and extra.get("missing_days"):
        reasons.append(_reason(
            "source_days_missing",
            f"В источнике нет {extra['missing_days']} календарных дней выбранного "
            "окна. Они показаны как нули только в среднем по дням недели; причиной "
            "может быть закрытие точки или неполная загрузка.",
            frm=extra.get("missing_from"), to=extra.get("missing_to")))
    return {
        "status": "warning" if any(r["severity"] == "warning" for r in reasons)
                  else "info" if reasons else "ok",
        "title": "Что важно знать о данных графика" if reasons else "Данные готовы",
        "period": {"from": str(frm)[:10] if frm else None,
                   "to": str(to)[:10] if to else None},
        "reasons": reasons,
        "sources": overview["sources"],
    }


def preset_days(preset: str) -> int:
    """Число дней пресета; неизвестный пресет — ValueError по-русски."""
    if preset not in PRESET_DAYS:
        raise ValueError("Период должен быть day, week, month, quarter или year")
    return PRESET_DAYS[preset]

# Запись excluded_days.json из двух параллельных запросов — под замком
_excluded_lock = threading.Lock()
EXCLUDED_MAX_AGE_DAYS = 400


def _cte() -> str:
    """Канонизация точек + имена рыночных точек: JOIN c → JOIN nm (как в data.freshness)."""
    return (f"{canon_cte()}, "
            "nm AS (SELECT canon_name, point_key AS pt FROM mart.dim_point "
            "WHERE role IN ('market', 'market_hub'))")


def _parse_iso(s: str, what: str = "Дата") -> date:
    try:
        return date.fromisoformat(str(s).strip())
    except ValueError:
        raise ValueError(f"{what} должна быть в формате ГГГГ-ММ-ДД, получено: {s!r}")


def _point_clause(point: str | None, args: list) -> str:
    if point:
        args.append(point)
        return " AND nm.canon_name = ?"
    return ""


_MAX_DAY_SOURCES = {
    "sales": ("mart.fact_sales_daily", "sale_date"),
    "checks": ("mart.cheki_sales_net", "trade_day"),
}


def _max_scoped_day(con, source: str, point: str | None = None):
    """Последняя дата только по фабричному ассортименту выбранной точки.

    Сам ряд каждого графика уже фильтруется через ``_SCOPE_SQL``. Тот же фильтр
    обязателен и здесь: иначе одна более свежая строка собственного производства
    сдвигает окно графика и создаёт ложные нули по фабричному ассортименту.
    """
    try:
        table, date_column = _MAX_DAY_SOURCES[source]
    except KeyError as exc:
        raise ValueError(f"Неизвестный источник даты аналитики: {source}") from exc
    args = list(_SCOPE_ARGS)
    point_sql = _point_clause(point, args)
    return con.execute(f"""
        WITH {_cte()}
        SELECT max(f.{date_column})
        FROM {table} f
        JOIN c  ON c.point_key = f.point_key
        JOIN nm ON nm.pt = c.pt
        JOIN mart.dim_sku s ON s.sku_key = f.sku_key
        WHERE {_SCOPE_SQL}{point_sql}
    """, args).fetchone()[0]


# --- точки для фильтра ---------------------------------------------------------

@data.ttl_cache(seconds=600)
def points() -> list[str]:
    """Канонические имена точек-маркетов — значения для выпадашки-фильтра."""
    with data.connect_snapshot() as con:
        rows = con.execute("""
            SELECT DISTINCT canon_name FROM mart.dim_point
            WHERE role IN ('market', 'market_hub') AND canon_name IS NOT NULL
            ORDER BY 1
        """).fetchall()
    return [r[0] for r in rows]


@data.ttl_cache(seconds=600)
def point_name_for_key(point_key: str) -> str:
    """Каноническое имя точки для серверного RBAC-ключа менеджера."""
    with data.connect_snapshot() as con:
        rows = con.execute(f"""
            WITH {_cte()}
            SELECT DISTINCT nm.canon_name
            FROM c JOIN nm ON nm.pt = c.pt
            WHERE c.point_key = ?
        """, [point_key]).fetchall()
    names = [r[0] for r in rows if r[0]]
    if len(names) != 1:
        raise ValueError("Ключ точки менеджера не найден или неоднозначен")
    return str(names[0])


# --- динамика продаж -----------------------------------------------------------

@data.ttl_cache(seconds=300)
def sales_series(granularity: str = "day", point: str | None = None,
                 frm: str | None = None, to: str | None = None,
                 preset: str | None = None) -> dict:
    """Динамика продаж (qty_sold − qty_returned) и уценки u40 по дням или неделям.

    Период: явные frm/to сильнее пресета; пресет — последние N дней от свежей
    продажи; без всего — 90 дней (day) / 26 недель (week).
    Возвращает {"rows": [{date, sold, u40, u40_share}], "from", "to", "granularity"}.
    """
    if granularity not in ("day", "week"):
        raise ValueError("granularity должен быть day или week")
    with data.connect_snapshot() as con:
        max_day = _max_scoped_day(con, "sales", point)
        if max_day is None:
            return {"rows": [], "from": None, "to": None,
                    "granularity": granularity, "point": point}
        to_d = _parse_iso(to, "Дата «по»") if to else max_day
        default_span = 89 if granularity == "day" else 181
        if frm:
            frm_d = _parse_iso(frm, "Дата «с»")
        elif preset:
            frm_d = to_d - timedelta(days=preset_days(preset) - 1)
        else:
            frm_d = to_d - timedelta(days=default_span)
        if frm_d > to_d:
            raise ValueError("Период задан наоборот: дата «с» позже даты «по»")
        date_expr = ("f.sale_date" if granularity == "day"
                     else "date_trunc('week', f.sale_date)")
        args = [*_SCOPE_ARGS, frm_d, to_d]
        point_sql = _point_clause(point, args)
        rows = con.execute(f"""
            WITH {_cte()}
            SELECT {date_expr} AS d,
                   sum(f.qty_sold - f.qty_returned) AS sold,
                   sum(f.u40)                       AS u40
            FROM mart.fact_sales_daily f
            JOIN c  ON c.point_key = f.point_key
            JOIN nm ON nm.pt = c.pt
            JOIN mart.dim_sku s ON s.sku_key = f.sku_key
            WHERE {_SCOPE_SQL} AND f.sale_date >= ? AND f.sale_date <= ?{point_sql}
            GROUP BY 1 ORDER BY 1
        """, args).fetchall()
    out = []
    for d, sold, u40 in rows:
        sold = float(sold or 0)
        u40 = float(u40 or 0)
        out.append({
            "date": str(d)[:10],
            "sold": round(sold, 1),
            "u40": round(u40, 1),
            "u40_share": round(100 * u40 / sold, 1) if sold > 0 else None,
        })
    return {"rows": out, "from": str(frm_d), "to": str(to_d),
            "granularity": granularity, "point": point}


# --- профиль по часам («во сколько покупают») ----------------------------------

@data.ttl_cache(seconds=300)
def hours_profile(point: str | None = None, days: int = 28) -> dict:
    """Профиль продаж по часам из строк чеков (net: возвраты со знаком минус).

    Час — из времени чека (ts). Окно — последние `days` торговых дней от свежего
    чека. Возвращает {"rows": [{hour, label, qty, per_day, share_pct}], ...}.
    """
    with data.connect_snapshot() as con:
        max_day = _max_scoped_day(con, "checks", point)
        if max_day is None:
            return {"rows": [], "n_days": 0, "calendar_days": 0,
                    "missing_days": 0, "from": None, "to": None}
        frm_d = max_day - timedelta(days=days - 1)
        args = [*_SCOPE_ARGS, frm_d, max_day]
        point_sql = _point_clause(point, args)
        base = f"""
            FROM mart.cheki_sales_net f
            JOIN c  ON c.point_key = f.point_key
            JOIN nm ON nm.pt = c.pt
            JOIN mart.dim_sku s ON s.sku_key = f.sku_key
            WHERE {_SCOPE_SQL} AND f.trade_day >= ? AND f.trade_day <= ?{point_sql}
        """
        rows = con.execute(f"""
            WITH {_cte()}
            SELECT CAST(extract(hour FROM f.ts) AS INTEGER) AS h,
                   sum(f.qty * f.sign) AS qty
            {base}
            GROUP BY 1 ORDER BY 1
        """, args).fetchall()
        n_days = con.execute(f"WITH {_cte()} SELECT count(DISTINCT f.trade_day) {base}",
                              args).fetchone()[0] or 0
        observed = {r[0] for r in con.execute(
            f"WITH {_cte()} SELECT DISTINCT f.trade_day {base}", args).fetchall()}
    calendar = [frm_d + timedelta(days=i) for i in range((max_day - frm_d).days + 1)]
    missing = [d for d in calendar if d not in observed]
    by_hour = {int(h): float(q or 0) for h, q in rows if h is not None}
    total = sum(q for q in by_hour.values() if q > 0)
    if not by_hour or total <= 0 or not n_days:
        return {"rows": [], "n_days": int(n_days), "calendar_days": len(calendar),
                "missing_days": len(missing),
                "missing_from": str(missing[0]) if missing else None,
                "missing_to": str(missing[-1]) if missing else None,
                "from": str(frm_d), "to": str(max_day)}
    # часы без продаж внутри рабочего диапазона показываем нулём (провал виден)
    active = [h for h, q in by_hour.items() if q > 0]
    out = []
    for h in range(min(active), max(active) + 1):
        q = by_hour.get(h, 0.0)
        out.append({
            "hour": h,
            "label": f"{h:02d}",
            "qty": round(q, 1),
            "per_day": round(q / n_days, 1),
            "share_pct": round(100 * q / total, 1),
        })
    return {"rows": out, "n_days": int(n_days), "calendar_days": len(calendar),
            "missing_days": len(missing),
            "missing_from": str(missing[0]) if missing else None,
            "missing_to": str(missing[-1]) if missing else None,
            "from": str(frm_d), "to": str(max_day)}


# --- средние по дням недели ----------------------------------------------------

@data.ttl_cache(seconds=300)
def weekday_profile(point: str | None = None, days: int = 56) -> dict:
    """Календарные средние по дням недели, включая дни без строк продаж как нули.

    Старый расчёт делил только на дни, где в витрине была хотя бы одна продажа,
    и систематически завышал среднее. Теперь знаменатель — календарные даты
    активного окна; пропуски источника возвращаются отдельной метаданной.
    """
    days = max(14, min(int(days), 400))
    with data.connect_snapshot() as con:
        max_day = _max_scoped_day(con, "sales", point)
        if max_day is None:
            return {"rows": [], "from": None, "to": None, "missing_days": 0}
        frm_d = max_day - timedelta(days=days - 1)
        if point:
            first_args = [*_SCOPE_ARGS, point]
            first_day = con.execute(f"""
                WITH {_cte()}
                SELECT min(f.sale_date)
                FROM mart.fact_sales_daily f
                JOIN c  ON c.point_key = f.point_key
                JOIN nm ON nm.pt = c.pt
                JOIN mart.dim_sku s ON s.sku_key = f.sku_key
                WHERE {_SCOPE_SQL} AND nm.canon_name = ?
            """, first_args).fetchone()[0]
            if first_day is None:
                return {"rows": [], "from": None, "to": str(max_day),
                        "missing_days": 0}
            frm_d = max(frm_d, first_day)
        args = [*_SCOPE_ARGS, frm_d, max_day]
        point_sql = _point_clause(point, args)
        rows = con.execute(f"""
            WITH {_cte()}
            SELECT f.sale_date,
                   sum(f.qty_sold - f.qty_returned) AS sold,
                   sum(f.u40)                       AS u40
            FROM mart.fact_sales_daily f
            JOIN c  ON c.point_key = f.point_key
            JOIN nm ON nm.pt = c.pt
            JOIN mart.dim_sku s ON s.sku_key = f.sku_key
            WHERE {_SCOPE_SQL} AND f.sale_date >= ? AND f.sale_date <= ?{point_sql}
            GROUP BY 1 ORDER BY 1
        """, args).fetchall()
    observed = {d: (float(sold or 0), float(u40 or 0)) for d, sold, u40 in rows}
    calendar = [frm_d + timedelta(days=i) for i in range((max_day - frm_d).days + 1)]
    missing = [d for d in calendar if d not in observed]
    out = []
    for wd in range(1, 8):
        dates = [d for d in calendar if d.isoweekday() == wd]
        sold = sum(observed.get(d, (0.0, 0.0))[0] for d in dates)
        u40 = sum(observed.get(d, (0.0, 0.0))[1] for d in dates)
        n_calendar = len(dates)
        n_observed = sum(d in observed for d in dates)
        out.append({
            "weekday": wd,
            "name": WD_RU[wd - 1],
            "avg_units": round(sold / n_calendar, 1) if n_calendar else 0.0,
            "avg_u40": round(u40 / n_calendar, 1) if n_calendar else 0.0,
            "n_days": n_calendar,
            "n_observed": n_observed,
        })
    return {"rows": out, "from": str(frm_d), "to": str(max_day),
            "calendar_days": len(calendar), "observed_days": len(observed),
            "missing_days": len(missing),
            "missing_from": str(missing[0]) if missing else None,
            "missing_to": str(missing[-1]) if missing else None}


# --- списания и уценка ---------------------------------------------------------

@data.ttl_cache(seconds=300)
def writeoffs(point: str | None = None, days: int = 56) -> dict:
    """Списания (fact_writeoff_daily) и уценка u40 — по дням и по точкам.

    Окно привязано к свежей продаже (списания могут отставать на день-два).
    Возвращает {"days": [{date, wo_units, wo_rub, u40_units}], "by_point": [...]}.
    """
    with data.connect_snapshot() as con:
        max_day = _max_scoped_day(con, "sales", point)
        if max_day is None:
            return {"days": [], "by_point": [], "from": None, "to": None,
                    "point": point}
        frm_d = max_day - timedelta(days=days - 1)

        def wo_query(dim: str):
            args = [*_SCOPE_ARGS, frm_d, max_day]
            point_sql = _point_clause(point, args)
            return con.execute(f"""
                WITH {_cte()}
                SELECT {dim} AS k, sum(f.qty_writeoff) AS wo_units,
                       sum(f.wo_sum) AS wo_rub,
                       count(*) FILTER (WHERE f.wo_sum IS NOT NULL AND f.wo_sum <> 0)
                           AS money_nonzero,
                       count(*) AS money_rows
                FROM mart.fact_writeoff_daily f
                JOIN c  ON c.point_key = f.point_key
                JOIN nm ON nm.pt = c.pt
                JOIN mart.dim_sku s ON s.sku_key = f.sku_key
                WHERE {_SCOPE_SQL} AND f.wo_date >= ? AND f.wo_date <= ?{point_sql}
                GROUP BY 1 ORDER BY 1
            """, args).fetchall()

        def u40_query(dim: str, date_col: str):
            args = [*_SCOPE_ARGS, frm_d, max_day]
            point_sql = _point_clause(point, args)
            return con.execute(f"""
                WITH {_cte()}
                SELECT {dim} AS k, sum(f.u40) AS u40
                FROM mart.fact_sales_daily f
                JOIN c  ON c.point_key = f.point_key
                JOIN nm ON nm.pt = c.pt
                JOIN mart.dim_sku s ON s.sku_key = f.sku_key
                WHERE {_SCOPE_SQL} AND f.{date_col} >= ? AND f.{date_col} <= ?{point_sql}
                GROUP BY 1 ORDER BY 1
            """, args).fetchall()

        wo_by_day = wo_query("f.wo_date")
        wo_by_point = wo_query("nm.canon_name")
        u40_by_day = {str(k)[:10]: float(v or 0) for k, v in u40_query("f.sale_date", "sale_date")}
        u40_by_point = {k: float(v or 0) for k, v in u40_query("nm.canon_name", "sale_date")}

    day_keys = sorted({str(k)[:10] for k, *_ in wo_by_day} | set(u40_by_day))
    wo_day_map = {str(k)[:10]: (float(u or 0), float(r or 0), int(nz), int(n))
                  for k, u, r, nz, n in wo_by_day}
    days_rows = [{
        "date": k,
        "wo_units": round(wo_day_map.get(k, (0, 0, 0, 0))[0], 1),
        "wo_rub": round(wo_day_map.get(k, (0, 0, 0, 0))[1]),
        "wo_sum_coverage_pct": (
            round(100 * wo_day_map[k][2] / wo_day_map[k][3], 1)
            if k in wo_day_map and wo_day_map[k][3] else None),
        "u40_units": round(u40_by_day.get(k, 0.0), 1),
    } for k in day_keys]

    point_keys = sorted({k for k, *_ in wo_by_point} | set(u40_by_point))
    wo_pt_map = {k: (float(u or 0), float(r or 0), int(nz), int(n))
                 for k, u, r, nz, n in wo_by_point}
    point_rows = [{
        "point": k,
        "wo_units": round(wo_pt_map.get(k, (0, 0, 0, 0))[0], 1),
        "wo_rub": round(wo_pt_map.get(k, (0, 0, 0, 0))[1]),
        "wo_sum_coverage_pct": (
            round(100 * wo_pt_map[k][2] / wo_pt_map[k][3], 1)
            if k in wo_pt_map and wo_pt_map[k][3] else None),
        "u40_units": round(u40_by_point.get(k, 0.0), 1),
    } for k in point_keys]
    money_nonzero = sum(r[3] for r in wo_by_day)
    money_rows = sum(r[4] for r in wo_by_day)
    return {"days": days_rows, "by_point": point_rows,
            "from": str(frm_d), "to": str(max_day),
            "point": point,
            "writeoff_amount_coverage_pct": (
                round(100 * money_nonzero / money_rows, 1) if money_rows else None)}


# --- топ дефицита и списаний с «полки» -----------------------------------------

def _shelf_provenance(src: Path) -> dict:
    stamp_path = src.with_suffix(".stamp")
    parts: list[str] = []
    try:
        parts = stamp_path.read_text(encoding="utf-8").strip().split("|")
    except OSError:
        pass
    return {
        "source": src.name,
        "generated_at": datetime.fromtimestamp(src.stat().st_mtime).isoformat(timespec="minutes"),
        "method_version": parts[0] if parts else None,
        "method_fingerprint": parts[1] if len(parts) > 1 else None,
        "checks_as_of": parts[2] if len(parts) > 2 else None,
        "writeoffs_as_of": parts[3] if len(parts) > 3 else None,
    }


@data.ttl_cache(seconds=600)
def shelf_top(n: int = 20, point: str | None = None, days: int = 28) -> dict:
    """Топ и дневные графики оценённых потерь на зрелом окне полки.

    В пользовательские цифры входят только ``wo_settled=True``. Более свежие
    строки не превращаются в ложные нули, а возвращаются как предварительный
    диапазон в метаданных качества.
    """
    days = max(7, min(int(days), 91))
    src = SHELF_ROLLING if SHELF_ROLLING.exists() else SHELF_DAILY
    empty = {"deficit": [], "deficit_network": [], "deficit_details": [],
             "writeoff": [], "daily": [], "totals": {},
             "from": None, "to": None, "source": None, "provenance": {},
             "point": point, "missing_days": 0, "preliminary_from": None,
             "source_to": None}
    if not src.exists():
        return empty
    df = data.read_parquet_stable(src)
    need = {"point_key", "sku_key", "canon_name", "sku_name", "trade_day", "lost_units",
            "lost_rub_margin", "wo_units", "wo_rub_cost", "wo_settled"}
    if df.empty or not need.issubset(df.columns):
        return {**empty, "source": src.name, "provenance": _shelf_provenance(src)}
    df = filter_frame(df, "metric_scope", date_col="trade_day")
    df["trade_day"] = pd.to_datetime(df["trade_day"]).dt.date
    if point:
        df = df[df["canon_name"] == point]
    if df.empty:
        return {**empty, "source": src.name, "provenance": _shelf_provenance(src)}
    source_to = df["trade_day"].max()
    preliminary = df[~df["wo_settled"].fillna(False).astype(bool)]
    preliminary_from = preliminary["trade_day"].min() if not preliminary.empty else None
    settled = df[df["wo_settled"].fillna(False).astype(bool)]
    if settled.empty:
        return {**empty, "source": src.name, "source_to": str(source_to),
                "preliminary_from": str(preliminary_from) if preliminary_from else None,
                "provenance": _shelf_provenance(src)}
    to_d = settled["trade_day"].max()
    frm_d = to_d - timedelta(days=days - 1)
    win = settled[(settled["trade_day"] >= frm_d) & (settled["trade_day"] <= to_d)]

    def top(units_col: str, rub_col: str) -> list[dict]:
        g = (win[win[units_col] > 0]
             .groupby(["canon_name", "sku_key", "sku_name"], as_index=False)[[units_col, rub_col]]
             .sum()
             .sort_values(rub_col, ascending=False)
             .head(n))
        return [{
            "point": r["canon_name"],
            "sku_key": str(r["sku_key"]),
            "sku": r["sku_name"],
            "units": round(float(r[units_col]), 1),
            "rub": round(float(r[rub_col])),
        } for _i, r in g.iterrows()]

    def top_network(units_col: str, rub_col: str) -> list[dict]:
        """Network total by SKU without losing the point-level table below."""
        g = (win[win[units_col] > 0]
             .groupby(["sku_key", "sku_name"], as_index=False)[[units_col, rub_col]]
             .sum()
             .sort_values(rub_col, ascending=False)
             .head(n))
        return [{
            "sku_key": str(r["sku_key"]),
            "sku": r["sku_name"],
            "units": round(float(r[units_col]), 1),
            "rub": round(float(r[rub_col])),
        } for _i, r in g.iterrows()]

    def deficit_details(limit: int = 100) -> list[dict]:
        """Explain each largest lost-sales estimate at point × SKU × day level."""
        details = win[win["lost_units"] > 0].copy()
        if details.empty:
            return []
        details = details.sort_values("lost_rub_margin", ascending=False).head(limit)
        out: list[dict] = []
        for _idx, row in details.iterrows():
            t_last = row.get("t_last")
            close_h = row.get("close_h")
            t_last = None if pd.isna(t_last) else float(t_last)
            close_h = None if pd.isna(close_h) else float(close_h)
            hours_left = (max(close_h - t_last, 0.0)
                          if t_last is not None and close_h is not None else None)
            lost = float(row.get("lost_units") or 0)
            tail_rate = (lost / hours_left
                         if hours_left is not None and hours_left > 0 else None)
            out.append({
                "date": str(row["trade_day"]),
                "point": str(row["canon_name"]).strip(),
                "sku_key": str(row["sku_key"]),
                "sku": str(row["sku_name"]).strip(),
                "ordered": round(float(row.get("ordered_human") or 0), 1),
                "sold": round(float(row.get("sold_units") or 0), 1),
                "last_sale": _hhmm(t_last) if t_last is not None else None,
                "estimated_close": _hhmm(close_h) if close_h is not None else None,
                "hours_left": round(hours_left, 1) if hours_left is not None else None,
                "tail_rate": round(tail_rate, 2) if tail_rate is not None else None,
                "lost_units": round(lost, 1),
                "lost_rub_margin": round(float(row.get("lost_rub_margin") or 0)),
            })
        return out

    by_day = (win.groupby("trade_day", as_index=False)
              .agg(lost_units=("lost_units", "sum"),
                   lost_rub_margin=("lost_rub_margin", "sum"),
                   wo_units=("wo_units", "sum"),
                   wo_rub_cost=("wo_rub_cost", "sum"))
              .sort_values("trade_day"))
    rolling_frame = by_day.copy()
    rolling_frame.index = pd.to_datetime(rolling_frame["trade_day"])
    for col in ("lost_units", "lost_rub_margin", "wo_units", "wo_rub_cost"):
        window = rolling_frame[col].rolling("7D", min_periods=1)
        by_day[f"{col}_ma7"] = window.mean().to_numpy()
        by_day[f"{col}_ma7_observed_days"] = window.count().astype(int).to_numpy()
    daily = [{
        "date": str(r.trade_day),
        "lost_units": round(float(r.lost_units), 1),
        "lost_rub_margin": round(float(r.lost_rub_margin)),
        "wo_units": round(float(r.wo_units), 1),
        "wo_rub_cost": round(float(r.wo_rub_cost)),
        "lost_units_ma7": round(float(r.lost_units_ma7), 1),
        "lost_rub_margin_ma7": round(float(r.lost_rub_margin_ma7)),
        "wo_units_ma7": round(float(r.wo_units_ma7), 1),
        "wo_rub_cost_ma7": round(float(r.wo_rub_cost_ma7)),
        "ma7_observed_days": int(min(
            r.lost_units_ma7_observed_days,
            r.lost_rub_margin_ma7_observed_days,
            r.wo_units_ma7_observed_days,
            r.wo_rub_cost_ma7_observed_days,
        )),
    } for r in by_day.itertuples(index=False)]
    observed = set(by_day["trade_day"].tolist())
    calendar = {frm_d + timedelta(days=i) for i in range((to_d - frm_d).days + 1)}
    missing = sorted(calendar - observed)

    return {
        "deficit": top("lost_units", "lost_rub_margin"),
        "deficit_network": top_network("lost_units", "lost_rub_margin"),
        "deficit_details": deficit_details(),
        "writeoff": top("wo_units", "wo_rub_cost"),
        "daily": daily,
        "totals": {
            "lost_units": round(float(win["lost_units"].sum()), 1),
            "lost_rub_margin": round(float(win["lost_rub_margin"].sum())),
            "wo_units": round(float(win["wo_units"].sum()), 1),
            "wo_rub_cost": round(float(win["wo_rub_cost"].sum())),
        },
        "from": str(frm_d), "to": str(to_d), "source": src.name,
        "source_to": str(source_to),
        "preliminary_from": str(preliminary_from) if preliminary_from else None,
        "missing_days": len(missing),
        "missing_from": str(missing[0]) if missing else None,
        "missing_to": str(missing[-1]) if missing else None,
        "provenance": _shelf_provenance(src), "point": point,
    }


def _shelf_operations(sku_key: str, point: str, frm_d: date, to_d: date,
                      selected_day: date | None) -> tuple[dict, dict, list[dict]]:
    """Order/assembly/supply facts and receipt-hour sales for one point and SKU.

    The shelf parquet intentionally does not depend on supply documents because
    their coverage is incomplete.  Drill-down may still show those fields as
    evidence, but keeps missing values as ``None`` instead of turning them into
    false zeroes.
    """
    ctes = f"{_cte()}, {sku_twins_cte()}"
    joins = """
        JOIN c  ON c.point_key = f.point_key
        JOIN nm ON nm.pt = c.pt
        LEFT JOIN tw ON tw.ghost = f.sku_key
        JOIN mart.dim_sku s ON s.sku_key = coalesce(tw.live, f.sku_key)
    """
    query_args = [*_SCOPE_ARGS, sku_key, frm_d, to_d, point]
    with data.connect_snapshot() as con:
        order_rows = con.execute(f"""
            WITH {ctes}
            SELECT nm.canon_name, f.trade_date,
                   sum(f.qty_ordered) AS ordered,
                   sum(f.qty_collected) AS collected,
                   count(*) FILTER (WHERE f.qty_collected > 0) AS collected_positive_rows
            FROM mart.fact_orders_daily f
            {joins}
            WHERE {_SCOPE_SQL}
              AND coalesce(tw.live, f.sku_key) = ? AND f.channel = 'transfer'
              AND f.trade_date BETWEEN ? AND ? AND nm.canon_name = ?
            GROUP BY 1, 2
        """, query_args).fetchall()
        supply_rows = con.execute(f"""
            WITH {ctes}
            SELECT nm.canon_name, f.supply_date, sum(f.qty) AS supplied
            FROM mart.fact_supply_daily f
            {joins}
            WHERE {_SCOPE_SQL}
              AND coalesce(tw.live, f.sku_key) = ?
              AND f.supply_date BETWEEN ? AND ? AND nm.canon_name = ?
            GROUP BY 1, 2
        """, query_args).fetchall()

        hourly_rows = []
        if selected_day is not None:
            hourly_rows = con.execute(f"""
                WITH {ctes}
                SELECT CAST(extract(hour FROM f.ts) AS INTEGER) AS hour,
                       sum(f.qty * f.sign) AS qty
                FROM mart.cheki_sales_net f
                {joins}
                WHERE {_SCOPE_SQL}
                  AND coalesce(tw.live, f.sku_key) = ? AND f.trade_day = ?
                  AND nm.canon_name = ?
                GROUP BY 1 ORDER BY 1
            """, [*_SCOPE_ARGS, sku_key, selected_day, point]).fetchall()

    orders = {
        (str(p), str(d)[:10]): {
            "ordered_document": round(float(ordered or 0), 1),
            "collected": (round(float(collected), 1)
                          if int(collected_positive_rows or 0) > 0
                          and collected is not None else None),
        }
        for p, d, ordered, collected, collected_positive_rows in order_rows
    }
    supplies = {
        (str(p), str(d)[:10]): round(float(supplied), 1)
        for p, d, supplied in supply_rows if supplied is not None
    }
    hourly = [{"hour": int(hour), "label": f"{int(hour):02d}:00",
               "qty": round(float(qty or 0), 1)}
              for hour, qty in hourly_rows if hour is not None]
    return orders, supplies, hourly


@data.ttl_cache(seconds=600)
def shelf_drilldown(sku: str, point: str | None = None,
                    selected_day: str | None = None, days: int = 28,
                    sku_key: str | None = None,
                    as_of: str | None = None,
                    allow_network_as_of: bool = False) -> dict:
    """Hierarchy for one SKU: network → point → day → receipt-hour facts.

    Every monetary and lost-sales value comes from the same mature shelf window
    as :func:`shelf_top`.  Actual receipt and assembly fields are read from their
    own documents and remain empty when the source has no row.
    """
    sku = str(sku or "").strip()
    point = str(point or "").strip() or None
    if not sku:
        raise ValueError("Укажите позицию для детализации")
    if len(sku) > 300:
        raise ValueError("Название позиции слишком длинное")
    sku_key = str(sku_key or "").strip() or None
    if sku_key is not None and len(sku_key) > 100:
        raise ValueError("Идентификатор позиции слишком длинный")
    day_d = _parse_iso(selected_day, "Дата детализации") if selected_day else None
    as_of_d = _parse_iso(as_of, "Дата среза") if as_of else None
    if day_d is not None and point is None:
        raise ValueError("Для детализации дня сначала выберите точку")

    days = max(7, min(int(days), 91))
    src = SHELF_ROLLING if SHELF_ROLLING.exists() else SHELF_DAILY
    empty = {"sku": sku, "sku_key": sku_key, "point": point,
             "as_of": str(as_of_d) if as_of_d else None,
             "from": None, "to": None,
             "source": None, "source_to": None, "preliminary_from": None,
             "missing_days": 0, "missing_from": None, "missing_to": None,
             "points": [], "days": [], "selected": None, "hourly": [],
             "provenance": {}, "notes": []}
    if not src.exists():
        return empty
    df = data.read_parquet_stable(src)
    need = {"point_key", "sku_key", "canon_name", "sku_name", "trade_day",
            "lost_units", "lost_rub_margin", "wo_units", "wo_settled"}
    if df.empty or not need.issubset(df.columns):
        return {**empty, "source": src.name, "provenance": _shelf_provenance(src)}
    df = filter_frame(df, "metric_scope", date_col="trade_day")
    df["trade_day"] = pd.to_datetime(df["trade_day"]).dt.date
    network_settled = df[df["wo_settled"].fillna(False).astype(bool)]
    network_settled_to = (
        network_settled["trade_day"].max() if not network_settled.empty else None)
    if point:
        df = df[df["canon_name"] == point]
    if df.empty:
        return {**empty, "source": src.name, "provenance": _shelf_provenance(src)}

    point_settled = df[df["wo_settled"].fillna(False).astype(bool)]
    point_settled_to = (
        point_settled["trade_day"].max() if not point_settled.empty else None)
    settled_to = network_settled_to if allow_network_as_of else point_settled_to
    if as_of_d is not None and (settled_to is None or as_of_d > settled_to):
        raise ValueError(
            "Дата среза позже последнего зрелого дня аналитики")

    source_to = df["trade_day"].max()
    preliminary = df[~df["wo_settled"].fillna(False).astype(bool)]
    preliminary_from = preliminary["trade_day"].min() if not preliminary.empty else None
    df = df[df["wo_settled"].fillna(False).astype(bool)]
    if df.empty:
        return {
            **empty,
            "source": src.name,
            "source_to": str(source_to),
            "preliminary_from": str(preliminary_from) if preliminary_from else None,
            "provenance": _shelf_provenance(src),
        }

    # ``as_of`` фиксирует одну дату среза на всём пути сеть → точка → день.
    # У отстающей точки окно не должно самопроизвольно съезжать назад.
    to_d = as_of_d or df["trade_day"].max()
    frm_d = to_d - timedelta(days=days - 1)
    source_days = set(df.loc[
        (df["trade_day"] >= frm_d) & (df["trade_day"] <= to_d), "trade_day"
    ].tolist())
    calendar = {frm_d + timedelta(days=i) for i in range(days)}
    missing = sorted(calendar - source_days)
    win = df[(df["trade_day"] >= frm_d) & (df["trade_day"] <= to_d)]
    named = win[win["sku_name"].astype(str).str.strip() == sku]
    if sku_key is None:
        candidates = named["sku_key"].astype(str).drop_duplicates().tolist()
        if len(candidates) > 1:
            raise ValueError(
                "Найдено несколько карточек с таким названием; выберите позицию заново")
        sku_key = candidates[0] if candidates else None
    win = named[named["sku_key"].astype(str) == sku_key] if sku_key else named
    if win.empty:
        return {
            **empty,
            "from": str(frm_d), "to": str(to_d), "source": src.name,
            "source_to": str(source_to),
            "preliminary_from": str(preliminary_from) if preliminary_from else None,
            "missing_days": len(missing),
            "missing_from": str(missing[0]) if missing else None,
            "missing_to": str(missing[-1]) if missing else None,
            "provenance": _shelf_provenance(src),
        }

    def total(frame: pd.DataFrame, column: str) -> float:
        if column not in frame.columns:
            return 0.0
        return float(pd.to_numeric(frame[column], errors="coerce").fillna(0).sum())

    def latest(frame: pd.DataFrame, column: str) -> float | None:
        if column not in frame.columns:
            return None
        values = pd.to_numeric(frame[column], errors="coerce").dropna()
        return float(values.max()) if not values.empty else None

    def earliest(frame: pd.DataFrame, column: str) -> float | None:
        if column not in frame.columns:
            return None
        values = pd.to_numeric(frame[column], errors="coerce").dropna()
        return float(values.min()) if not values.empty else None

    point_rows = []
    for point_name, group in win.groupby("canon_name", sort=True):
        point_rows.append({
            "point": str(point_name).strip(),
            "days": int(group["trade_day"].nunique()),
            "ordered": round(total(group, "ordered_human"), 1),
            "sold": round(total(group, "sold_units"), 1),
            "lost_units": round(total(group, "lost_units"), 1),
            "lost_rub_margin": round(total(group, "lost_rub_margin")),
            "wo_units": round(total(group, "wo_units"), 1),
        })
    point_rows.sort(key=lambda row: (row["lost_rub_margin"], row["lost_units"]),
                    reverse=True)

    orders: dict = {}
    supplies: dict = {}
    hourly: list[dict] = []
    if point:
        orders, supplies, hourly = _shelf_operations(
            sku_key, point, frm_d, to_d, day_d)

    day_rows = []
    if point:
        for trade_day, group in win.groupby("trade_day", sort=True):
            point_name = str(group["canon_name"].iloc[0]).strip()
            date_key = str(trade_day)
            order_fact = orders.get((point_name, date_key), {})
            t_first = earliest(group, "t_first")
            t_last = latest(group, "t_last")
            close_h = latest(group, "close_h")
            day_rate = latest(group, "day_rate")
            hours_left = (max(close_h - t_last, 0.0)
                          if close_h is not None and t_last is not None else None)
            lost = total(group, "lost_units")
            day_rows.append({
                "date": date_key,
                "point": point_name,
                "ordered": round(total(group, "ordered_human"), 1),
                "ordered_document": order_fact.get("ordered_document"),
                "collected": order_fact.get("collected"),
                "supplied": supplies.get((point_name, date_key)),
                "sold": round(total(group, "sold_units"), 1),
                "first_sale": _hhmm(t_first) if t_first is not None else None,
                "last_sale": _hhmm(t_last) if t_last is not None else None,
                "estimated_close": _hhmm(close_h) if close_h is not None else None,
                "hours_left": round(hours_left, 1) if hours_left is not None else None,
                "day_rate": round(day_rate, 2) if day_rate is not None else None,
                "tail_rate": (round(lost / hours_left, 2)
                              if hours_left is not None and hours_left > 0 else None),
                "lost_units": round(lost, 1),
                "lost_rub_margin": round(total(group, "lost_rub_margin")),
                "wo_units": round(total(group, "wo_units"), 1),
            })
        day_rows.sort(key=lambda row: row["date"], reverse=True)

    selected = None
    if day_d is not None:
        selected = next((row for row in day_rows if row["date"] == str(day_d)), None)
        if selected is None:
            raise ValueError("Для выбранной точки и даты нет строки этой позиции")
        by_hour = {row["hour"]: row["qty"] for row in hourly}
        if by_hour:
            first_h = min(by_hour)
            close_h = latest(win[win["trade_day"] == day_d], "close_h")
            last_h = max(max(by_hour), int(close_h) if close_h is not None else max(by_hour))
            hourly = [{"hour": hour, "label": f"{hour:02d}:00",
                       "qty": round(float(by_hour.get(hour, 0)), 1)}
                      for hour in range(first_h, last_h + 1)]

    notes = [
        "Недопродажи — оценка по чекам и профилю продаж, а не факт пустой полки.",
        "Собрано — поле заявки фабрики; это не подтверждение приёмки магазином.",
        "Поступило показывается только при наличии строки в частично заполненном источнике привозов.",
        ("Нулевой заказ в этот день и положительная продажа не противоречат друг другу: "
         "товар мог остаться от прежней поставки, быть произведён на точке или прийти "
         "вне канала перемещения. Эта таблица не показывает фактический остаток."),
        ("Небольшая расчётная недопродажа ниже настроенного порога значимости "
         "показывается как 0, чтобы не выдавать случайный спрос редких позиций за дефицит."),
    ]
    if selected is not None and float(selected.get("sold") or 0) > 0:
        if hourly:
            notes.append("Продажа выбранного дня подтверждена строками кассовых чеков.")
        else:
            notes.append(
                "Продажа выбранного дня взята из сводного документа; строка чека не "
                "найдена, поэтому время продажи и почасовая оценка недоступны.")

    return {
        "sku": sku, "sku_key": sku_key, "point": point,
        "as_of": str(to_d), "from": str(frm_d), "to": str(to_d),
        "source": src.name, "source_to": str(source_to),
        "preliminary_from": str(preliminary_from) if preliminary_from else None,
        "missing_days": len(missing),
        "missing_from": str(missing[0]) if missing else None,
        "missing_to": str(missing[-1]) if missing else None,
        "points": point_rows, "days": day_rows,
        "selected": selected, "hourly": hourly,
        "provenance": _shelf_provenance(src),
        "notes": notes,
    }


# --- год к году ----------------------------------------------------------------

@data.ttl_cache(seconds=600)
def point_first_sale() -> dict[str, date]:
    """Первая продажа по скоупу прогноза для каждой точки: canon_name → date.
    По ней честно отвечаем, есть ли у точки история год назад."""
    with data.connect_snapshot() as con:
        rows = con.execute(f"""
            WITH {_cte()}
            SELECT nm.canon_name, min(f.sale_date)
            FROM mart.fact_sales_daily f
            JOIN c  ON c.point_key = f.point_key
            JOIN nm ON nm.pt = c.pt
            JOIN mart.dim_sku s ON s.sku_key = f.sku_key
            WHERE {_SCOPE_SQL}
            GROUP BY 1 ORDER BY 1
        """, _SCOPE_ARGS).fetchall()
    return {p: d for p, d in rows if d is not None}


def _pct(cur: float, prev: float) -> float | None:
    """Изменение в % к прошлому году; при нулевой базе — None (в UI «—»)."""
    if prev and prev > 0:
        return round(100.0 * (cur - prev) / prev, 1)
    return None


@data.ttl_cache(seconds=600)
def yoy(point: str | None = None, preset: str = "month") -> dict:
    """Год к году: продажи (шт и ₽), списания (шт и ₽), доля уценки — за последние
    N дней пресета и за те же даты год назад (минус 365 дней), с дельтами.

    ₽ продаж — колонка revenue витрины fact_sales_daily (выручка 1С),
    ₽ списаний — wo_sum витрины fact_writeoff_daily.
    Точке без истории год назад — честный отказ {available: False, reason}.
    По сети сравниваются только точки, работавшие уже год назад, — иначе рост
    был бы «нарисован» открытием новых точек.
    """
    if preset not in ("week", "month", "quarter", "year"):
        raise ValueError("Период сравнения должен быть week, month, quarter или year")
    n = PRESET_DAYS[preset]
    first = point_first_sale()
    with data.connect_snapshot() as con:
        max_day = _max_scoped_day(con, "sales", point)
        if max_day is None:
            return {"available": False, "reason": "Данных о продажах в снимке ещё нет."}
        cur_to, cur_frm = max_day, max_day - timedelta(days=n - 1)
        prev_to, prev_frm = cur_to - timedelta(days=365), cur_frm - timedelta(days=365)

        if point:
            fs = first.get(point)
            if fs is None:
                raise ValueError(f"Точка «{point}» не найдена в данных продаж")
            if fs > prev_frm:
                return {"available": False, "point": point, "preset": preset,
                        "reason": f"точка открыта {fs:%d.%m.%Y} — "
                                  "истории прошлого года нет"}
            pts = [point]
        else:
            pts = sorted(p for p, d in first.items() if d <= prev_frm)
            if not pts:
                earliest = min(first.values()) if first else None
                extra = (f" Данные продаж ведутся с {earliest:%d.%m.%Y}."
                         if earliest else "")
                return {"available": False, "preset": preset,
                        "reason": "Ни одна точка не работала весь такой же период "
                                  f"год назад ({prev_frm:%d.%m.%Y} — {prev_to:%d.%m.%Y})."
                                  + extra}

        ph = ", ".join("?" for _ in pts)

        def metrics(frm_d: date, to_d: date) -> dict:
            args = [*_SCOPE_ARGS, frm_d, to_d, *pts]
            sold, rub, u40 = con.execute(f"""
                WITH {_cte()}
                SELECT sum(f.qty_sold - f.qty_returned), sum(f.revenue), sum(f.u40)
                FROM mart.fact_sales_daily f
                JOIN c  ON c.point_key = f.point_key
                JOIN nm ON nm.pt = c.pt
                JOIN mart.dim_sku s ON s.sku_key = f.sku_key
                WHERE {_SCOPE_SQL} AND f.sale_date >= ? AND f.sale_date <= ?
                  AND nm.canon_name IN ({ph})
            """, args).fetchone()
            wo_u, wo_r, wo_money_nonzero, wo_rows = con.execute(f"""
                WITH {_cte()}
                SELECT sum(f.qty_writeoff), sum(f.wo_sum),
                       count(*) FILTER (WHERE f.wo_sum IS NOT NULL AND f.wo_sum <> 0),
                       count(*)
                FROM mart.fact_writeoff_daily f
                JOIN c  ON c.point_key = f.point_key
                JOIN nm ON nm.pt = c.pt
                JOIN mart.dim_sku s ON s.sku_key = f.sku_key
                WHERE {_SCOPE_SQL} AND f.wo_date >= ? AND f.wo_date <= ?
                  AND nm.canon_name IN ({ph})
            """, args).fetchone()
            sold = float(sold or 0)
            u40 = float(u40 or 0)
            money_coverage = (round(100 * wo_money_nonzero / wo_rows, 1)
                              if wo_rows else None)
            return {
                "from": str(frm_d), "to": str(to_d),
                "sales_units": round(sold, 1),
                "sales_rub": round(float(rub or 0)),
                "u40_units": round(u40, 1),
                "u40_share": round(100 * u40 / sold, 1) if sold > 0 else None,
                "wo_units": round(float(wo_u or 0), 1),
                "wo_rub": round(float(wo_r or 0)),
                "wo_sum_coverage_pct": money_coverage,
                "wo_rub_reliable": bool(money_coverage is not None
                                        and money_coverage >= 80),
            }

        cur = metrics(cur_frm, cur_to)
        prev = metrics(prev_frm, prev_to)

    delta = {k: _pct(cur[k], prev[k])
             for k in ("sales_units", "sales_rub", "wo_units")}
    delta["wo_rub"] = (_pct(cur["wo_rub"], prev["wo_rub"])
                       if cur["wo_rub_reliable"] and prev["wo_rub_reliable"]
                       else None)
    delta["u40_share_pp"] = (round(cur["u40_share"] - prev["u40_share"], 1)
                             if cur["u40_share"] is not None
                             and prev["u40_share"] is not None else None)
    return {
        "available": True, "preset": preset, "point": point,
        "cur": cur, "prev": prev, "delta": delta,
        "points_used": pts,
        "price_source": "выручка из витрины продаж 1С (revenue), "
                        "списания в ₽ — по актам списания (wo_sum)",
    }


# --- последняя продажа и темп продаж (оценка, не остаток) -----------------------

SELLOUT_OUTCOMES = ("кончилось рано", "распродано под закрытие")
RECEIPT_RATE_FORMULA_VERSION = "receipt-window-v1"


def _hhmm(t: float) -> str:
    """Float-час 19.66 → «19:40»."""
    h = int(t)
    m = int(round((t - h) * 60))
    if m == 60:
        h, m = h + 1, 0
    return f"{h:02d}:{m:02d}"


def _receipt_rate_stats(frame: pd.DataFrame, pair: list[str]) -> tuple[dict, dict]:
    """Observed net sales per product-hour between first and last receipt.

    This is deliberately not a stock or sellout metric.  Each eligible
    point×SKU×day contributes ``floor(t_last) - floor(t_first) + 1`` product
    hours, so a same-hour sale has a one-hour denominator.  Missing/invalid
    receipt windows are counted as excluded observations rather than zero rate.
    """
    total_pair_days = int(frame.groupby(pair)["trade_day"].nunique().sum())
    required = {"sold_units", "t_first", "t_last"}
    empty_summary = {
        "rate_u_h": None,
        "units": 0.0,
        "receipt_window_hours": 0.0,
        "observed_pair_days": 0,
        "excluded_pair_days": total_pair_days,
        "formula_version": RECEIPT_RATE_FORMULA_VERSION,
        "available": False,
    }
    if not required.issubset(frame.columns):
        return {}, {**empty_summary, "reason": "В снимке нет времени первого чека."}

    rate = frame[[*pair, "trade_day", "sold_units", "t_first", "t_last"]].copy()
    for col in ("sold_units", "t_first", "t_last"):
        rate[col] = pd.to_numeric(rate[col], errors="coerce")
    valid = (
        rate["sold_units"].gt(0)
        & rate["t_first"].notna()
        & rate["t_last"].notna()
        & rate["t_last"].ge(rate["t_first"])
    )
    rate = rate[valid].copy()
    if rate.empty:
        return {}, {**empty_summary, "reason": "Нет дней с полным чековым окном."}

    rate["receipt_window_hours"] = (
        (rate["t_last"] // 1) - (rate["t_first"] // 1) + 1
    ).clip(lower=1)
    grouped = (rate.groupby(pair, as_index=False)
               .agg(receipt_sale_units=("sold_units", "sum"),
                    receipt_window_hours=("receipt_window_hours", "sum"),
                    receipt_rate_observed_days=("trade_day", "nunique")))
    all_days = (frame.groupby(pair, as_index=False)["trade_day"].nunique()
                .rename(columns={"trade_day": "receipt_rate_total_days"}))
    grouped = grouped.merge(all_days, on=pair, how="left")
    grouped["receipt_sale_rate_u_h"] = (
        grouped["receipt_sale_units"] / grouped["receipt_window_hours"])
    grouped["receipt_rate_excluded_days"] = (
        grouped["receipt_rate_total_days"] - grouped["receipt_rate_observed_days"])

    by_key = {}
    for row in grouped.itertuples(index=False):
        key = tuple(getattr(row, col) for col in pair)
        by_key[key] = {
            "receipt_sale_rate_u_h": round(float(row.receipt_sale_rate_u_h), 2),
            "receipt_sale_units": round(float(row.receipt_sale_units), 1),
            "receipt_window_hours": round(float(row.receipt_window_hours), 1),
            "receipt_rate_observed_days": int(row.receipt_rate_observed_days),
            "receipt_rate_excluded_days": int(row.receipt_rate_excluded_days),
            "receipt_rate_formula_version": RECEIPT_RATE_FORMULA_VERSION,
        }

    units = float(grouped["receipt_sale_units"].sum())
    hours = float(grouped["receipt_window_hours"].sum())
    observed = int(grouped["receipt_rate_observed_days"].sum())
    summary = {
        "rate_u_h": round(units / hours, 2) if hours > 0 else None,
        "units": round(units, 1),
        "receipt_window_hours": round(hours, 1),
        "observed_pair_days": observed,
        "excluded_pair_days": max(total_pair_days - observed, 0),
        "formula_version": RECEIPT_RATE_FORMULA_VERSION,
        "available": bool(hours > 0),
    }
    return by_key, summary


@data.ttl_cache(seconds=600)
def sellout_speed(point: str | None = None, days: int = 28) -> dict:
    """Last-sale diagnostics from the shelf model, without claiming stock truth.

    ``t_last`` is an observed receipt time.  ``outcome`` is only a model flag,
    therefore the UI calls it "last sale" rather than "the shelf was empty".

    The response also includes slow movers by average sold units per observed
    day.  Nothing is removed from the historical shelf dataset.
    """
    days = max(7, min(int(days), 400))
    empty = {"top": [], "slow": [], "all": [], "summary": None,
             "from": None, "to": None,
             "source": None, "point": point, "provenance": {},
             "source_to": None, "preliminary_from": None, "missing_days": 0}
    src = SHELF_ROLLING if SHELF_ROLLING.exists() else SHELF_DAILY
    if not src.exists():
        return empty
    df = data.read_parquet_stable(src)
    need = {"point_key", "sku_key", "canon_name", "sku_name", "trade_day", "t_last", "outcome",
            "wo_settled"}
    if df.empty or not need.issubset(df.columns):
        return {**empty, "source": src.name, "provenance": _shelf_provenance(src)}
    df = filter_frame(df, "metric_scope", date_col="trade_day")
    df["trade_day"] = pd.to_datetime(df["trade_day"]).dt.date
    if point:
        df = df[df["canon_name"] == point]
    if df.empty:
        return {**empty, "source": src.name, "provenance": _shelf_provenance(src)}
    source_to = df["trade_day"].max()
    preliminary = df[~df["wo_settled"].fillna(False).astype(bool)]
    preliminary_from = preliminary["trade_day"].min() if not preliminary.empty else None
    df = df[df["wo_settled"].fillna(False).astype(bool)]
    if df.empty:
        return {**empty, "source": src.name, "source_to": str(source_to),
                "preliminary_from": str(preliminary_from) if preliminary_from else None,
                "provenance": _shelf_provenance(src)}
    to_d = df["trade_day"].max()
    frm_d = to_d - timedelta(days=days - 1)
    win = df[(df["trade_day"] >= frm_d) & (df["trade_day"] <= to_d)]

    pair = ["canon_name", "sku_key", "sku_name"]
    rate_by_key, receipt_rate = _receipt_rate_stats(win, pair)

    slow: list[dict] = []
    if "sold_units" in win.columns:
        activity = (win.groupby(pair, as_index=False)
                    .agg(sold_units=("sold_units", "sum"),
                         days_seen=("trade_day", "nunique")))
        if "ordered_human" in win.columns:
            ordered = (win.groupby(pair, as_index=False)["ordered_human"].sum()
                       .rename(columns={"ordered_human": "ordered_units"}))
            activity = activity.merge(ordered, on=pair, how="left")
        else:
            activity["ordered_units"] = 0.0
        activity["avg_sold_per_day"] = (
            activity["sold_units"] / activity["days_seen"].clip(lower=1))
        activity = activity[
            (activity["days_seen"] >= 3)
            & ((activity["sold_units"] > 0) | (activity["ordered_units"] > 0))
        ].sort_values(["avg_sold_per_day", "sold_units"], ascending=[True, True]).head(15)
        slow = []
        for r in activity.itertuples(index=False):
            key = (r.canon_name, r.sku_key, r.sku_name)
            slow.append({
                "point": str(r.canon_name).strip(),
                "sku_key": str(r.sku_key),
                "sku": str(r.sku_name).strip(),
                "avg_sold_per_day": round(float(r.avg_sold_per_day), 2),
                "sold_units": round(float(r.sold_units), 1),
                "ordered_units": round(float(r.ordered_units), 1),
                "days_seen": int(r.days_seen),
                **rate_by_key.get(key, {
                    "receipt_sale_rate_u_h": None,
                    "receipt_rate_observed_days": 0,
                    "receipt_rate_excluded_days": int(r.days_seen),
                }),
            })

    sell = win[win["outcome"].isin(SELLOUT_OUTCOMES) & win["t_last"].notna()]
    if sell.empty:
        return {**empty, "slow": slow,
                "summary": {"receipt_rate": receipt_rate}, "source": src.name,
                "from": str(frm_d), "to": str(to_d), "source_to": str(source_to),
                "preliminary_from": str(preliminary_from) if preliminary_from else None,
                "provenance": _shelf_provenance(src)}

    seen = win.groupby(pair).size()
    early = win[win["outcome"] == "кончилось рано"].groupby(pair).size()
    g = (sell.groupby(pair)["t_last"].agg(["mean", "size"])
         .rename(columns={"mean": "t", "size": "n"}).reset_index())
    rows = []
    for r in g.itertuples(index=False):
        key = (r.canon_name, r.sku_key, r.sku_name)
        d_seen = int(seen.get(key, 0)) or 1
        rows.append({
            "point": str(r.canon_name).strip(),
            "sku_key": str(r.sku_key),
            "sku": str(r.sku_name).strip(),
            "t_avg": round(float(r.t), 2),
            "t_hhmm": _hhmm(float(r.t)),
            "n_sellout": int(r.n),
            "days_seen": d_seen,
            "early_share_pct": round(100 * int(early.get(key, 0)) / d_seen, 1),
            **rate_by_key.get(key, {
                "receipt_sale_rate_u_h": None,
                "receipt_rate_observed_days": 0,
                "receipt_rate_excluded_days": d_seen,
            }),
        })
    qualified = sorted((r for r in rows if r["n_sellout"] >= 3),
                       key=lambda r: r["t_avg"])

    by_point = [{"point": p, "hhmm": _hhmm(float(t)), "n": int(n)}
                for p, t, n in (sell.groupby("canon_name")["t_last"]
                                .agg(["mean", "size"]).reset_index()
                                .itertuples(index=False, name=None))]
    summary = {
        "avg_hhmm": _hhmm(float(sell["t_last"].mean())),
        "n_obs": int(len(sell)),
        "by_point": sorted(by_point, key=lambda x: x["hhmm"]),
        "receipt_rate": receipt_rate,
    }
    observed = set(win["trade_day"].unique().tolist())
    calendar = {frm_d + timedelta(days=i) for i in range((to_d - frm_d).days + 1)}
    missing = sorted(calendar - observed)
    return {"top": qualified[:15], "slow": slow, "all": qualified,
            "summary": summary,
            "from": str(frm_d), "to": str(to_d), "source": src.name,
            "point": point, "source_to": str(source_to),
            "preliminary_from": str(preliminary_from) if preliminary_from else None,
            "missing_days": len(missing),
            "missing_from": str(missing[0]) if missing else None,
            "missing_to": str(missing[-1]) if missing else None,
            "provenance": _shelf_provenance(src)}


# --- топ позиций ---------------------------------------------------------------

@data.ttl_cache(seconds=300)
def top_sku(point: str | None = None, days: int = 28, by: str = "qty") -> dict:
    """Топ-20 позиций по продажам за окно: шт (qty) или ₽ (rub, выручка revenue)."""
    if by not in ("qty", "rub"):
        raise ValueError("Сортировка by должна быть qty (штуки) или rub (рубли)")
    days = max(1, min(int(days), 400))
    with data.connect_snapshot() as con:
        max_day = _max_scoped_day(con, "sales", point)
        if max_day is None:
            return {"rows": [], "from": None, "to": None, "by": by}
        frm_d = max_day - timedelta(days=days - 1)
        args = [*_SCOPE_ARGS, frm_d, max_day]
        point_sql = _point_clause(point, args)
        base = f"""
            FROM mart.fact_sales_daily f
            JOIN c  ON c.point_key = f.point_key
            JOIN nm ON nm.pt = c.pt
            JOIN mart.dim_sku s ON s.sku_key = f.sku_key
            WHERE {_SCOPE_SQL} AND f.sale_date >= ? AND f.sale_date <= ?{point_sql}
        """
        order = "sum(f.qty_sold - f.qty_returned)" if by == "qty" else "sum(f.revenue)"
        rows = con.execute(f"""
            WITH {_cte()}
            SELECT s.name, s.group_path,
                   sum(f.qty_sold - f.qty_returned) AS qty,
                   sum(f.revenue)                   AS rub,
                   sum(f.u40)                       AS u40
            {base}
            GROUP BY 1, 2
            HAVING sum(f.qty_sold - f.qty_returned) > 0
            ORDER BY {order} DESC LIMIT 20
        """, args).fetchall()
        tot_qty, tot_rub = con.execute(
            f"WITH {_cte()} SELECT sum(f.qty_sold - f.qty_returned), sum(f.revenue) {base}",
            args).fetchone()
    tot_qty, tot_rub = float(tot_qty or 0), float(tot_rub or 0)
    out = []
    for name, gp, qty, rub, u40 in rows:
        qty, rub, u40 = float(qty or 0), float(rub or 0), float(u40 or 0)
        share_base = tot_qty if by == "qty" else tot_rub
        share_val = qty if by == "qty" else rub
        out.append({
            "sku": str(name or "").strip(),
            "group": str(gp or "").split(" / ")[-1].strip(),
            "qty": round(qty, 1),
            "rub": round(rub),
            "u40_share": round(100 * u40 / qty, 1) if qty > 0 else None,
            "share_pct": round(100 * share_val / share_base, 1) if share_base > 0 else None,
        })
    return {"rows": out, "from": str(frm_d), "to": str(max_day), "by": by,
            "price_source": "выручка из витрины продаж 1С (revenue)"}


# --- выгрузка Excel -------------------------------------------------------------

EXPORT_TITLES = {
    "sales": "Динамика продаж",
    "writeoffs": "Списания и уценка",
    "shelf": "Дефицит и списания с полки",
    "yoy": "Год к году",
    "top_sku": "Топ позиций",
    "sellout_speed": "Раннее прекращение продаж (оценка)",
    "hours": "Продажи по часам",
    "weekdays": "Продажи по дням недели",
}
_EXPORT_FILE = {
    "sales": "Продажи", "writeoffs": "Списания", "shelf": "Дефицит",
    "yoy": "Год_к_году", "top_sku": "Топ_позиций",
    "sellout_speed": "Раннее_прекращение_продаж",
    "hours": "По_часам", "weekdays": "Дни_недели",
}


def _fmt_period(frm: str | None, to: str | None) -> str:
    if frm and to:
        return f"{frm} — {to}"
    return "период не определён (данных нет)"


def _xlsx_safe_df(frame: pd.DataFrame) -> pd.DataFrame:
    """Не даём строкам из 1С превращаться в исполняемые формулы Excel."""
    frame = frame.copy()
    for column in frame.columns:
        frame[column] = frame[column].map(
            lambda value: ("'" + value
                           if isinstance(value, str)
                           and value.startswith(("=", "+", "-", "@"))
                           else value))
    return frame


def build_export_xlsx(section: str, *, point: str | None = None,
                      granularity: str = "day", frm: str | None = None,
                      to: str | None = None, preset: str | None = None,
                      days: int | None = None, by: str = "qty",
                      n: int = 20) -> tuple[Path, str]:
    """Собирает xlsx раздела аналитики во временный файл.

    Возвращает (путь, имя для скачивания); файл удаляет вызывающий маршрут
    (BackgroundTask после отдачи — как в routes_order.api_order_export).
    """
    if section not in EXPORT_TITLES:
        raise ValueError("Неизвестный раздел выгрузки: " + str(section))

    sheets: list[tuple[str, pd.DataFrame, dict]] = []   # (имя листа, таблица, ширины)
    extra_lines: list[str] = []
    period = None
    quality: dict | None = None

    if section == "sales":
        s = sales_series(granularity, point, frm, to, preset)
        period = _fmt_period(s["from"], s["to"])
        quality = section_quality("sales", s["from"], s["to"], point=point)
        df = pd.DataFrame([{
            "Дата": r["date"], "Продано, шт": r["sold"], "Уценка (у40), шт": r["u40"],
            "Доля уценки, %": r["u40_share"],
        } for r in s["rows"]])
        sheets.append(("Продажи", df, {"Дата": 12}))
        extra_lines.append("Скоуп прогноза (демонстрационное меню), "
                           "возвраты вычтены.")
    elif section == "writeoffs":
        s = writeoffs(point, days or 56)
        period = _fmt_period(s["from"], s["to"])
        quality = section_quality(
            "writeoffs", s["from"], s["to"],
            extra={"writeoff_amount_coverage_pct": s["writeoff_amount_coverage_pct"]},
            point=point)
        df_d = pd.DataFrame([{
            "Дата": r["date"], "Списано, шт": r["wo_units"],
            "Сумма заполненных актов, ₽": r["wo_rub"],
            "Заполнение суммы актов, %": r["wo_sum_coverage_pct"],
            "Уценка (у40), шт": r["u40_units"],
        } for r in s["days"]])
        df_p = pd.DataFrame([{
            "Точка": r["point"], "Списано, шт": r["wo_units"],
            "Сумма заполненных актов, ₽": r["wo_rub"],
            "Заполнение суммы актов, %": r["wo_sum_coverage_pct"],
            "Уценка (у40), шт": r["u40_units"],
        } for r in s["by_point"]])
        sheets.append(("По дням", df_d, {"Дата": 12}))
        sheets.append(("По точкам", df_p, {"Точка": 26}))
        extra_lines.append("Списания — акты 1С; уценка — продажи со скидкой 40% (у40). "
                           "Рубли — сумма только заполненных актов, не полная потеря.")
    elif section == "shelf":
        s = shelf_top(n, point, days or 28)
        period = _fmt_period(s["from"], s["to"])
        quality = section_quality(
            "shelf", s["from"], s["to"], extra=s, point=point)
        df_d = pd.DataFrame([{
            "Позиция": r["sku"], "Точка": r["point"], "Недопродано, шт": r["units"],
            "Упущенная маржа, ₽": r["rub"],
        } for r in s["deficit"]])
        df_w = pd.DataFrame([{
            "Позиция": r["sku"], "Точка": r["point"], "Списано, шт": r["units"],
            "Потеряно, ₽": r["rub"],
        } for r in s["writeoff"]])
        df_n = pd.DataFrame([{
            "Позиция": r["sku"], "Недопродано по сети, шт": r["units"],
            "Оценка упущенной маржи, ₽": r["rub"],
        } for r in s["deficit_network"]])
        df_x = pd.DataFrame([{
            "Дата": r["date"], "Точка": r["point"], "Позиция": r["sku"],
            "Заказано, шт": r["ordered"], "Продано, шт": r["sold"],
            "Последняя продажа": r["last_sale"],
            "Оценочное закрытие": r["estimated_close"],
            "Часов до закрытия": r["hours_left"],
            "Оценочный темп хвоста, шт/ч": r["tail_rate"],
            "Оценено упущено, шт": r["lost_units"],
            "Оценка упущенной маржи, ₽": r["lost_rub_margin"],
        } for r in s["deficit_details"]])
        sheets.append(("Недопродажи", df_d, {"Позиция": 42, "Точка": 24}))
        sheets.append(("По сети", df_n, {"Позиция": 42}))
        sheets.append(("Расшифровка", df_x, {"Дата": 12, "Точка": 24, "Позиция": 42}))
        sheets.append(("Списания", df_w, {"Позиция": 42, "Точка": 24}))
        df_daily = pd.DataFrame([{
            "Дата": r["date"], "Оценено упущено, шт": r["lost_units"],
            "Оценка упущенной маржи, ₽": r["lost_rub_margin"],
            "Списано, шт": r["wo_units"],
            "Оценка себестоимости списаний, ₽": r["wo_rub_cost"],
            "Упущено, среднее по доступным наблюдениям за 7 календарных дней, шт":
                r["lost_units_ma7"],
            "Упущенная маржа, среднее по доступным наблюдениям за 7 календарных дней, ₽":
                r["lost_rub_margin_ma7"],
            "Списано, среднее по доступным наблюдениям за 7 календарных дней, шт":
                r["wo_units_ma7"],
            "Себестоимость списаний, среднее по доступным наблюдениям за 7 календарных дней, ₽":
                r["wo_rub_cost_ma7"],
            "Доступных дней в окне среднего": r["ma7_observed_days"],
        } for r in s["daily"]])
        sheets.insert(0, ("Динамика", df_daily, {"Дата": 12}))
        extra_lines.append("Недопродажи — консервативная модельная оценка полки; "
                           "списания в ₽ — оценка себестоимости по цене позиции. "
                           "Источник: " + str(s["source"]))
    elif section == "yoy":
        y = yoy(point, preset or "month")
        if not y.get("available"):
            reason = y.get("reason", "Нет сопоставимого периода прошлого года.")
            period = "сопоставимый период отсутствует"
            quality = {
                "status": "caution",
                "title": "Сравнение год к году недоступно",
                "reasons": [_reason("no_comparable_period", reason)],
            }
            sheets.append(("Год к году", pd.DataFrame([{
                "Статус": "Недоступно", "Причина": reason,
            }]), {"Статус": 15, "Причина": 70}))
            extra_lines.append(reason)
        else:
            cur, prev, d = y["cur"], y["prev"], y["delta"]

            def dl(v, suffix=" %"):
                return "—" if v is None else f"{v:+.1f}{suffix}"

            df = pd.DataFrame([
                {"Показатель": "Продажи, шт", "Текущий период": cur["sales_units"],
                 "Год назад": prev["sales_units"], "Изменение": dl(d["sales_units"])},
                {"Показатель": "Продажи, ₽", "Текущий период": cur["sales_rub"],
                 "Год назад": prev["sales_rub"], "Изменение": dl(d["sales_rub"])},
                {"Показатель": "Списания, шт", "Текущий период": cur["wo_units"],
                 "Год назад": prev["wo_units"], "Изменение": dl(d["wo_units"])},
                {"Показатель": "Сумма заполненных актов списания, ₽",
                 "Текущий период": cur["wo_rub"] if cur["wo_rub_reliable"] else "—",
                 "Год назад": prev["wo_rub"] if prev["wo_rub_reliable"] else "—",
                 "Изменение": dl(d["wo_rub"])},
                {"Показатель": "Доля уценки, %", "Текущий период": cur["u40_share"],
                 "Год назад": prev["u40_share"], "Изменение": dl(d["u40_share_pp"], " п.п.")},
            ])
            period = (f"{cur['from']} — {cur['to']} против {prev['from']} — {prev['to']}")
            quality = section_quality(
                "yoy", cur["from"], cur["to"],
                extra={
                    "writeoff_amount_coverage_pct": cur["wo_sum_coverage_pct"],
                    "comparison_from": prev["from"],
                    "comparison_to": prev["to"],
                },
                point=point)
            sheets.append(("Год к году", df, {"Показатель": 20, "Текущий период": 16,
                                              "Год назад": 16, "Изменение": 14}))
            extra_lines.append("В сравнении участвуют точки, работавшие уже год назад: "
                               + ", ".join(y["points_used"]) + ".")
            extra_lines.append("Деньги: " + y["price_source"] + ".")
    elif section == "top_sku":
        t = top_sku(point, days or 28, by)
        period = _fmt_period(t["from"], t["to"])
        quality = section_quality("top_sku", t["from"], t["to"], point=point)
        metric = "штукам" if by == "qty" else "выручке"
        df = pd.DataFrame([{
            "Позиция": r["sku"], "Группа": r["group"], "Продано, шт": r["qty"],
            "Выручка, ₽": r["rub"], "Доля уценки, %": r["u40_share"],
            f"Доля в продажах по {metric}, %": r["share_pct"],
        } for r in t["rows"]])
        sheets.append(("Топ позиций", df, {"Позиция": 42, "Группа": 18}))
        extra_lines.append("Топ-20 по " + ("продажам в штуках" if by == "qty"
                                           else "выручке") + ", скоуп прогноза.")
    elif section == "sellout_speed":
        s = sellout_speed(point, days or 28)
        period = _fmt_period(s["from"], s["to"])
        quality = section_quality(
            "sellout_speed", s["from"], s["to"], extra=s, point=point)
        df = pd.DataFrame([{
            "Точка": r["point"], "Позиция": r["sku"],
            "Среднее время последней продажи в отмеченные дни": r["t_hhmm"],
            "Наблюдаемый темп продаж по чекам, шт/ч": r["receipt_sale_rate_u_h"],
            "Дней расчёта темпа": r["receipt_rate_observed_days"],
            "Дней с признаком раннего окончания, %": r["early_share_pct"],
            "Дней с признаком окончания": r["n_sellout"],
            "Дней наблюдений": r["days_seen"],
        } for r in s["all"]])
        slow = pd.DataFrame([{
            "Точка": r["point"], "Позиция": r["sku"],
            "Продано в среднем, шт/день": r["avg_sold_per_day"],
            "Наблюдаемый темп продаж по чекам, шт/ч": r["receipt_sale_rate_u_h"],
            "Дней расчёта темпа": r["receipt_rate_observed_days"],
            "Продано за окно, шт": r["sold_units"],
            "Заказано за окно, шт": r["ordered_units"],
            "Дней наблюдений": r["days_seen"],
        } for r in s["slow"]])
        sheets.append(("Раннее прекращение", df, {"Точка": 24, "Позиция": 42}))
        sheets.append(("Медленные позиции", slow, {"Точка": 24, "Позиция": 42}))
        extra_lines.append(
            "Темп по чекам = чистые продажи / сумму товаро-часов от первого до "
            "последнего чека дня. Это не измеренный остаток или скорость опустения "
            "полки. Позиции — с ≥3 наблюдениями за окно.")
    elif section == "hours":
        s = hours_profile(point, days or 28)
        period = _fmt_period(s["from"], s["to"])
        quality = section_quality(
            "hours", s["from"], s["to"], extra=s, point=point)
        df = pd.DataFrame([{
            "Час": f"{r['label']}:00", "Шт в день с чеками": r["per_day"],
            "Всего за период, шт": r["qty"], "Доля, %": r["share_pct"],
        } for r in s["rows"]])
        sheets.append(("По часам", df, {"Час": 10}))
        extra_lines.append(
            f"Среднее по {s['n_days']} дням с чеками из {s['calendar_days']} "
            "календарных дней окна; возвраты вычтены.")
    elif section == "weekdays":
        s = weekday_profile(point, days or 56)
        period = _fmt_period(s["from"], s["to"])
        quality = section_quality(
            "weekdays", s["from"], s["to"], extra=s, point=point)
        df = pd.DataFrame([{
            "День недели": r["name"],
            "Продажи, шт/календарный день": r["avg_units"],
            "Уценка, шт/календарный день": r["avg_u40"],
            "Календарных дней": r["n_days"],
            "Дней со строками продаж": r["n_observed"],
        } for r in s["rows"]])
        sheets.append(("Дни недели", df, {"День недели": 15}))
        extra_lines.append("Среднее делится на все календарные дни активного окна; "
                           "дни без строк продаж учтены как нули и помечены в статусе.")

    title = EXPORT_TITLES[section]
    quality_lines = []
    if quality and quality.get("reasons"):
        quality_lines.append("СТАТУС: данные требуют оговорок; причины ниже.")
        for reason in quality["reasons"]:
            affected = (f" [{reason['from']} — {reason['to']}]"
                        if reason.get("from") and reason.get("to") else "")
            quality_lines.append(f"{reason['code']}{affected}: {reason['text']}")
    head = [
        f"Прогноз заказов · {title} · {period} · сгенерировано "
        f"{datetime.now():%d.%m.%Y %H:%M}",
        f"Точка: {point or 'вся сеть'}",
        *quality_lines,
        *extra_lines,
    ]

    from openpyxl.styles import Font  # noqa: PLC0415

    fd, tmp = make_export_tempfile(prefix=f"analytics_{section}_")
    os.close(fd)
    path = Path(tmp)
    try:
        with pd.ExcelWriter(path, engine="openpyxl") as xl:
            for name, df, widths in sheets:
                df = _xlsx_safe_df(df)
                if df.empty:
                    df = pd.DataFrame({"Нет данных за период": []})
                df.to_excel(xl, sheet_name=name, index=False, startrow=len(head) + 1)
                ws = xl.sheets[name]
                for i, line in enumerate(head, start=1):
                    cell = ws.cell(row=i, column=1, value=line)
                    cell.font = Font(bold=(i == 1))
                hdr = len(head) + 2
                for c in ws[hdr]:
                    c.font = Font(bold=True)
                ws.freeze_panes = ws.cell(row=hdr + 1, column=1)
                for idx, col in enumerate(df.columns, start=1):
                    letter = ws.cell(row=hdr, column=idx).column_letter
                    ws.column_dimensions[letter].width = widths.get(
                        col, max(13, min(34, len(str(col)) + 4)))
    except Exception:
        path.unlink(missing_ok=True)
        raise
    fname = f"forecast_{_EXPORT_FILE[section]}_{date.today():%Y-%m-%d}.xlsx"
    return path, fname


def build_shelf_drilldown_xlsx(result: dict,
                               quality: dict | None = None) -> tuple[Path, str]:
    """Excel текущей детализации полки: сеть → точка → день → часы.

    ``result`` должен быть уже ограничен правами пользователя в маршруте. Так
    выгрузка повторяет ровно тот же серверный срез, который показан в диалоге,
    и не доверяет данным DOM браузера.
    """
    notes = list(result.get("notes") or [])
    for reason in (quality or {}).get("reasons", []):
        affected = (f" [{reason.get('from')} — {reason.get('to')}]"
                    if reason.get("from") and reason.get("to") else "")
        notes.append(f"{reason.get('code', 'ограничение')}{affected}: "
                     f"{reason.get('text', '')}")
    info_rows = [
        {"Параметр": "Сформировано", "Значение": f"{datetime.now():%d.%m.%Y %H:%M}"},
        {"Параметр": "Позиция", "Значение": result.get("sku") or "—"},
        {"Параметр": "Точка", "Значение": result.get("point") or "вся сеть"},
        {"Параметр": "Зрелое окно",
         "Значение": _fmt_period(result.get("from"), result.get("to"))},
        {"Параметр": "Источник", "Значение": result.get("source") or "—"},
    ]
    info_rows.extend({"Параметр": f"Оговорка {idx}", "Значение": note}
                     for idx, note in enumerate(notes, start=1))

    points = pd.DataFrame([{
        "Точка": row.get("point"), "Дней": row.get("days"),
        "Заказано, шт": row.get("ordered"), "Продано, шт": row.get("sold"),
        "Оценено упущено, шт": row.get("lost_units"),
        "Упущенная маржа, ₽": row.get("lost_rub_margin"),
        "Списано, шт": row.get("wo_units"),
    } for row in result.get("points", [])], columns=[
        "Точка", "Дней", "Заказано, шт", "Продано, шт",
        "Оценено упущено, шт", "Упущенная маржа, ₽", "Списано, шт",
    ])
    days = pd.DataFrame([{
        "Дата": row.get("date"), "Точка": row.get("point"),
        "Заказано по полке, шт": row.get("ordered"),
        "Заказано в документе, шт": row.get("ordered_document"),
        "Собрано фабрикой, шт": row.get("collected"),
        "Поступило по документам, шт": row.get("supplied"),
        "Продано, шт": row.get("sold"), "Первая продажа": row.get("first_sale"),
        "Последняя продажа": row.get("last_sale"),
        "Расчётное закрытие точки": row.get("estimated_close"),
        "Средний профиль, шт/день": row.get("day_rate"),
        "Оценочный темп хвоста, шт/ч": row.get("tail_rate"),
        "Оценено упущено, шт": row.get("lost_units"),
        "Упущенная маржа, ₽": row.get("lost_rub_margin"),
        "Списано, шт": row.get("wo_units"),
    } for row in result.get("days", [])], columns=[
        "Дата", "Точка", "Заказано по полке, шт", "Заказано в документе, шт",
        "Собрано фабрикой, шт", "Поступило по документам, шт", "Продано, шт",
        "Первая продажа", "Последняя продажа", "Расчётное закрытие точки",
        "Средний профиль, шт/день", "Оценочный темп хвоста, шт/ч",
        "Оценено упущено, шт", "Упущенная маржа, ₽", "Списано, шт",
    ])
    selected_rows = [result["selected"]] if result.get("selected") else []
    selected = days[days["Дата"].isin(
        [row.get("date") for row in selected_rows])].copy() if selected_rows else days.iloc[0:0]
    hourly = pd.DataFrame([{
        "Час": row.get("label"), "Продано по чекам, шт": row.get("qty"),
    } for row in result.get("hourly", [])],
        columns=["Час", "Продано по чекам, шт"])
    info = pd.DataFrame(info_rows, columns=["Параметр", "Значение"])
    sheets = [
        ("О детализации", info), ("По точкам", points), ("По дням", days),
        ("Выбранный день", selected), ("По часам", hourly),
    ]

    fd, tmp = make_export_tempfile(prefix="shelf_drilldown_")
    os.close(fd)
    path = Path(tmp)
    try:
        from openpyxl.styles import Alignment, Font, PatternFill  # noqa: PLC0415

        with pd.ExcelWriter(path, engine="openpyxl") as writer:
            for sheet_name, frame in sheets:
                frame = _xlsx_safe_df(frame)
                frame.to_excel(writer, sheet_name=sheet_name, index=False)
                ws = writer.sheets[sheet_name]
                ws.freeze_panes = "A2"
                ws.auto_filter.ref = ws.dimensions
                for cell in ws[1]:
                    cell.font = Font(bold=True, color="FFFFFF")
                    cell.fill = PatternFill("solid", fgColor="A61E22")
                    cell.alignment = Alignment(wrap_text=True, vertical="center")
                for idx, column in enumerate(frame.columns, start=1):
                    width = 48 if column in ("Значение",) else 24
                    if column in ("Дата", "Час"):
                        width = 15
                    ws.column_dimensions[ws.cell(1, idx).column_letter].width = width
    except Exception:
        path.unlink(missing_ok=True)
        raise
    filename = f"forecast_Детализация_полки_{date.today():%Y-%m-%d}.xlsx"
    return path, filename


# --- нетипичные дни (excluded_days.json) ---------------------------------------

_DEFAULT_COMMENT = ("Нетипичные дни: исключаются из обучения ML-ядра и окон расчёта. "
                    "Правится из веб-интерфейса (вкладка «Аналитика») или руками.")


def _read_doc() -> dict:
    """Документ целиком, с сохранением _comment (его пишет ночной прогон/ТЗ)."""
    if not EXCLUDED_FILE.exists():
        return {"_comment": _DEFAULT_COMMENT, "days": []}
    doc = json.loads(EXCLUDED_FILE.read_text(encoding="utf-8"))
    doc.setdefault("_comment", _DEFAULT_COMMENT)
    doc.setdefault("days", [])
    return doc


def _write_doc(doc: dict) -> None:
    """Атомарная запись: tmp-файл рядом + os.replace — ночной прогон никогда
    не увидит недописанный json."""
    EXCLUDED_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = EXCLUDED_FILE.with_name(EXCLUDED_FILE.name + ".tmp")
    tmp.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, EXCLUDED_FILE)


def excluded_days_list() -> list[dict]:
    """Текущий список нетипичных дней, отсортирован по дате."""
    return sorted(_read_doc()["days"], key=lambda d: d.get("date", ""))


def excluded_days_update(date_s: str, reason: str, action: str,
                         actor: str = "") -> list[dict]:
    """Добавить/убрать нетипичный день. Ошибки валидации — ValueError по-русски.

    Правила: ISO-дата, строго в прошлом, не старше 400 дней; при add причина
    обязательна и дата не должна уже быть в списке.
    """
    if action not in ("add", "remove"):
        raise ValueError("action должен быть add или remove")
    d = _parse_iso(date_s)
    today = date.today()
    if d >= today:
        raise ValueError("Исключать можно только прошедшие дни — сегодня и будущее нельзя.")
    if (today - d).days > EXCLUDED_MAX_AGE_DAYS:
        raise ValueError(f"Дата старше {EXCLUDED_MAX_AGE_DAYS} дней — "
                         "такие дни в расчётах уже не участвуют.")
    with _excluded_lock:
        doc = _read_doc()
        existing = {x.get("date") for x in doc["days"]}
        if action == "add":
            if not str(reason or "").strip():
                raise ValueError("Укажите причину — почему день нетипичный "
                                 "(закрытие точки, авария, акция и т.п.).")
            if d.isoformat() in existing:
                raise ValueError("Эта дата уже в списке. Чтобы поменять причину — "
                                 "удалите её и добавьте заново.")
            doc["days"].append({"date": d.isoformat(),
                                "reason": str(reason).strip(),
                                "added_at": today.isoformat(),
                                # кто внёс: день выкидывается из обучения модели
                                # и метрик всех точек — действие не должно быть
                                # анонимным (ревью 07.09)
                                "added_by": str(actor or "").strip()})
        else:
            if d.isoformat() not in existing:
                raise ValueError("Этой даты нет в списке.")
            doc["days"] = [x for x in doc["days"] if x.get("date") != d.isoformat()]
        doc["days"].sort(key=lambda x: x.get("date", ""))
        _write_doc(doc)
    return doc["days"]
