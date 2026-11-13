# -*- coding: utf-8 -*-
"""Read-only инструменты помощника (function calling): белый список обёрток над
УЖЕ существующими функциями сервиса — web.data / web.analytics / web.accuracy /
web.order. Нового SQL здесь нет и быть не должно.

Устойчивость — главный принцип:
- модули берём лениво через importlib, функции — через getattr: соседние вкладки
  могут переехать, помощник от этого не падает, а честно отвечает
  {"error": "данные временно недоступны"};
- kwargs фильтруются по фактической сигнатуре функции (inspect.signature) —
  переживаем переименование/удаление параметров;
- любой сбой внутри инструмента — ошибка строкой в JSON, техдеталей в ответе нет
  (они уходят в лог);
- результат всегда JSON-строка ≤ MAX_RESULT_BYTES: длинные списки режутся
  пополам с пометкой truncated:true.
"""
from __future__ import annotations

import importlib
import inspect
import json
import re
from datetime import datetime, timedelta, timezone

from loguru import logger

MAX_RESULT_BYTES = 8 * 1024
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

UNAVAILABLE = {"error": "данные временно недоступны"}
SCOPE_UNAVAILABLE = {"error": "не удалось безопасно ограничить данные кабинетом точки"}

_PERIODS = ("last", "7d", "30d", "all")
_PRESETS = ("day", "week", "month", "quarter", "year")
_LOCAL_TZ = timezone(timedelta(hours=10))


# --- мелкая санитария аргументов ----------------------------------------------

def _clamp_int(v, lo: int, hi: int, default: int) -> int:
    try:
        i = int(v)
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, i))


def _str_or_none(v) -> str | None:
    s = str(v).strip() if v is not None else ""
    return s or None


def _effective_point(args: dict, point_scope: str | None) -> str | None:
    """A manager's server-side scope always wins over tool arguments from the LLM."""
    return point_scope if point_scope is not None else _str_or_none(args.get("point"))


def _pick(v, allowed: tuple, default: str) -> str:
    return v if v in allowed else default


def _local_now() -> datetime:
    return datetime.now(_LOCAL_TZ)


def _call_fn(fn, **kwargs):
    """Вызов с фильтрацией kwargs по фактической сигнатуре — параллельные правки
    соседних модулей не должны ронять помощника из-за лишнего аргумента."""
    try:
        params = inspect.signature(fn).parameters
        kwargs = {k: v for k, v in kwargs.items() if k in params}
    except (TypeError, ValueError):
        pass
    return fn(**kwargs)


def _supports_point_scope(fn, point_scope: str | None) -> bool:
    """Fail closed if a manager-scoped downstream API cannot accept ``point``."""
    if point_scope is None:
        return True
    try:
        params = inspect.signature(fn).parameters
    except (TypeError, ValueError):
        return False
    # An explicit parameter is part of the trusted downstream contract.  Merely
    # accepting **kwargs is not enough: _call_fn intentionally removes names
    # that are absent from the signature, so point would otherwise disappear.
    return "point" in params


def _contains_foreign_point(node, point_scope: str | None) -> bool:
    """Detect obvious point rows that escaped a trusted downstream filter."""
    if point_scope is None:
        return False
    if isinstance(node, dict):
        for key, value in node.items():
            normalized = str(key).strip().casefold()
            if normalized in {"point", "точка", "canon_name"}:
                if isinstance(value, str) and value.strip() and value != point_scope:
                    return True
            if normalized == "by_point" and isinstance(value, dict):
                if any(str(point) != point_scope for point in value):
                    return True
            if _contains_foreign_point(value, point_scope):
                return True
    elif isinstance(node, list):
        return any(_contains_foreign_point(item, point_scope) for item in node)
    return False


def _scope_checked(result, point_scope: str | None):
    if _contains_foreign_point(result, point_scope):
        logger.error("ИИ-инструмент заблокировал результат с чужой точкой")
        return dict(SCOPE_UNAVAILABLE)
    return result


def _strict_scope_checked(result, point_scope: str | None):
    """Require a verifiable top-level scope marker for aggregate tools."""
    if point_scope is not None:
        if not isinstance(result, dict) or result.get("point") != point_scope:
            logger.error("ИИ-инструмент не подтвердил скоуп кабинета точки")
            return dict(SCOPE_UNAVAILABLE)
    return _scope_checked(result, point_scope)


def _rows_match_scope(rows, point_scope: str | None) -> bool:
    """Every point-bearing row must explicitly belong to the manager's point."""
    if point_scope is None:
        return True
    return isinstance(rows, list) and all(
        isinstance(row, dict) and row.get("point") == point_scope for row in rows
    )


_SAFE_SCALAR = object()


def _fields(*names: str) -> dict:
    return {name: _SAFE_SCALAR for name in names}


def _project_safe(node, schema):
    """Project an untrusted downstream response onto a fixed JSON schema."""
    if schema is _SAFE_SCALAR:
        if node is None or isinstance(node, (bool, int, float)):
            return node
        if isinstance(node, str):
            return node[:1000]
        return None
    if node is None:
        return None
    if isinstance(schema, dict):
        if not isinstance(node, dict):
            return {}
        return {
            key: _project_safe(node[key], child)
            for key, child in schema.items() if key in node
        }
    if isinstance(schema, list) and len(schema) == 1:
        if not isinstance(node, list):
            return []
        return [_project_safe(item, schema[0]) for item in node[:400]]
    raise TypeError("Некорректная схема ИИ-инструмента")


_MONEY_SCHEMA = _fields("over_k", "under_k", "miss_k", "priced_rows", "fallback_rows")
_METRIC_SCHEMA = {
    "wape": _SAFE_SCALAR,
    "bias": _SAFE_SCALAR,
    "money": _MONEY_SCHEMA,
}
_SALES_SCHEMA = {
    "point": _SAFE_SCALAR,
    "rows": [_fields("date", "sold", "u40", "u40_share")],
    "from": _SAFE_SCALAR,
    "to": _SAFE_SCALAR,
    "granularity": _SAFE_SCALAR,
}
_ACCURACY_SCHEMA = {
    "period": _SAFE_SCALAR,
    "level": _SAFE_SCALAR,
    "point": _SAFE_SCALAR,
    "epoch": _SAFE_SCALAR,
    "available_days": _SAFE_SCALAR,
    "last_date": _SAFE_SCALAR,
    "points": [_SAFE_SCALAR],
    "excluded_days": [_SAFE_SCALAR],
    "dates": _fields("from", "to", "n_days"),
    "rows": _SAFE_SCALAR,
    "forecast_units": _SAFE_SCALAR,
    "order_units": _SAFE_SCALAR,
    "actual_units": _SAFE_SCALAR,
    "forecast": _METRIC_SCHEMA,
    "order": _METRIC_SCHEMA,
    "no_order": _fields("rows", "sold_units", "share_pct", "rows_with_order"),
    "edits": _fields("rows", "delta_units", "up_units", "down_units"),
    "writeoffs": _fields("units", "rub", "from", "to", "last_wo_date"),
    "sellout_rows": _SAFE_SCALAR,
    "by_point": [_fields(
        "point", "rows", "forecast", "order", "actual", "wape_forecast",
        "wape_order", "rows_no_order", "miss_k",
    )],
    "daily": [_fields(
        "date", "forecast", "order", "sold", "wape_forecast", "wape_order",
    )],
}
_TOP_MISS_SCHEMA = [_fields(
    "date", "point", "sku", "group", "forecast", "order", "actual", "err", "sellout",
)]
_WRITEOFFS_SCHEMA = {
    "point": _SAFE_SCALAR,
    "days": [_fields("date", "wo_units", "wo_rub", "wo_sum_coverage_pct", "u40_units")],
    "by_point": [_fields("point", "wo_units", "wo_rub", "wo_sum_coverage_pct", "u40_units")],
    "from": _SAFE_SCALAR,
    "to": _SAFE_SCALAR,
    "writeoff_amount_coverage_pct": _SAFE_SCALAR,
}
_SHELF_SCHEMA = {
    "point": _SAFE_SCALAR,
    "deficit": [_fields("point", "sku", "units", "rub")],
    "writeoff": [_fields("point", "sku", "units", "rub")],
    "daily": [_fields(
        "date", "lost_units", "lost_rub_margin", "wo_units", "wo_rub_cost",
        "lost_units_ma7", "wo_units_ma7",
    )],
    "totals": _fields("lost_units", "lost_rub_margin", "wo_units", "wo_rub_cost"),
    "from": _SAFE_SCALAR,
    "to": _SAFE_SCALAR,
    "source_to": _SAFE_SCALAR,
    "preliminary_from": _SAFE_SCALAR,
    "missing_days": _SAFE_SCALAR,
    "missing_from": _SAFE_SCALAR,
    "missing_to": _SAFE_SCALAR,
    "provenance": _fields("checks_as_of", "writeoffs_as_of"),
}
_METHOD_SCHEMA = _fields(
    "key", "name", "units", "wape", "bias", "over_k", "under_k", "miss_k",
)
_COMPARISON_SCHEMA = {
    "period": _SAFE_SCALAR,
    "point": _SAFE_SCALAR,
    "base": _SAFE_SCALAR,
    "base_note": _SAFE_SCALAR,
    "scope": _SAFE_SCALAR,
    "points": [_SAFE_SCALAR],
    "available_days": _SAFE_SCALAR,
    "first_date": _SAFE_SCALAR,
    "last_date": _SAFE_SCALAR,
    "rows": _SAFE_SCALAR,
    "rows_all": _SAFE_SCALAR,
    "rows_paired": _SAFE_SCALAR,
    "dates": _fields("from", "to", "n_days"),
    "sold_units": _SAFE_SCALAR,
    "demand_units": _SAFE_SCALAR,
    "lost_units": _SAFE_SCALAR,
    "methods": [_METHOD_SCHEMA],
    "chek_lag": _fields("days", "from", "to"),
    "daily": [_fields(
        "date", "baseline", "ml", "human", "sold", "demand", "miss_ml_k",
        "miss_baseline_k", "miss_human_k", "chek_settled", "winner",
    )],
    "by_point": [_fields(
        "point", "rows", "baseline", "ml", "human", "sold", "demand",
        "miss_ml_k", "miss_baseline_k", "miss_human_k", "rows_no_order",
    )],
    "alt": {
        "scope": _SAFE_SCALAR,
        "rows": _SAFE_SCALAR,
        "methods": [_METHOD_SCHEMA],
    },
}


def _visible_date_bounds(rows: list, key: str = "date") -> tuple[str | None, str | None]:
    dates = [
        row.get(key) for row in rows
        if isinstance(row, dict) and isinstance(row.get(key), str)
        and _DATE_RE.match(row[key])
    ]
    return (min(dates), max(dates)) if dates else (None, None)


def _fn(module: str, name: str):
    """Функция модуля через importlib+getattr; нет модуля/функции — None."""
    try:
        mod = importlib.import_module(module)
    except Exception:
        logger.exception("ИИ-инструменты: модуль {m} не импортировался", m=module)
        return None
    return getattr(mod, name, None)


# --- обработчики инструментов --------------------------------------------------

def _tool_get_meta(args: dict, point_scope: str | None = None) -> dict:
    freshness = _fn("web.data", "freshness")
    forecast_dates = _fn("web.data", "forecast_dates")
    now = _local_now()
    out: dict = {
        "current_datetime_local": now.isoformat(timespec="minutes"),
        "current_date_local": now.date().isoformat(),
    }
    if freshness is not None:
        sales_freshness = _call_fn(freshness)
        if point_scope is not None and isinstance(sales_freshness, dict):
            scoped_rows = [
                row for row in (sales_freshness.get("freshness") or [])
                if row.get("point") == point_scope
            ]
            sales_freshness = {
                "points": [point_scope] if scoped_rows else [],
                "max_sale_date": max(
                    (row.get("last_sale") for row in scoped_rows if row.get("last_sale")),
                    default=None,
                ),
                "freshness": scoped_rows,
            }
        out["sales_freshness"] = sales_freshness
    dates = _call_fn(forecast_dates) if forecast_dates is not None else []
    out["first_forecast_date"] = dates[0] if dates else None
    out["last_forecast_date"] = dates[-1] if dates else None
    out["forecast_dates_recent"] = dates[-14:]
    if not out.get("sales_freshness") and not dates:
        out["data_status"] = "данные временно недоступны"
    return out


def _tool_get_sales(args: dict, point_scope: str | None = None) -> dict:
    fn = _fn("web.analytics", "sales_series")
    if fn is None or not _supports_point_scope(fn, point_scope):
        return UNAVAILABLE
    result = _call_fn(
        fn,
        granularity=_pick(args.get("granularity"), ("day", "week"), "day"),
        point=_effective_point(args, point_scope),
        preset=_pick(args.get("preset"), _PRESETS, "month"),
    )
    result = _strict_scope_checked(result, point_scope)
    if isinstance(result, dict) and result.get("error"):
        return result
    safe = _project_safe(result, _SALES_SCHEMA)
    if point_scope is not None:
        safe["point"] = point_scope
        safe["from"], safe["to"] = _visible_date_bounds(safe.get("rows") or [])
    return safe


def _tool_get_accuracy(args: dict, point_scope: str | None = None) -> dict:
    fn = _fn("web.accuracy", "summary")
    if fn is None or not _supports_point_scope(fn, point_scope):
        return UNAVAILABLE
    point = _effective_point(args, point_scope)
    res = _call_fn(
        fn,
        period=_pick(args.get("period"), _PERIODS, "7d"),
        level="point" if point else "network",
        point=point,
    )
    res = _strict_scope_checked(res, point_scope)
    if isinstance(res, dict) and res.get("error"):
        return res
    if point_scope is not None and not _rows_match_scope(
            res.get("by_point") or [], point_scope):
        return dict(SCOPE_UNAVAILABLE)
    safe = _project_safe(res, _ACCURACY_SCHEMA)
    if point_scope is not None:
        safe["point"] = point_scope
        safe["points"] = [point_scope]
        safe.pop("excluded_days", None)
        daily = safe.get("daily") or []
        _first, last = _visible_date_bounds(daily)
        safe["available_days"] = len(daily)
        safe["last_date"] = last
        safe["by_point"] = [
            row for row in (safe.get("by_point") or [])
            if row.get("point") == point_scope
        ]
        if isinstance(safe.get("writeoffs"), dict):
            safe["writeoffs"].pop("last_wo_date", None)
    return safe


def _tool_get_top_miss(args: dict, point_scope: str | None = None) -> dict:
    fn = _fn("web.accuracy", "top_miss")
    if fn is None or not _supports_point_scope(fn, point_scope):
        return UNAVAILABLE
    rows = _call_fn(
        fn,
        period=_pick(args.get("period"), _PERIODS, "7d"),
        direction=_pick(args.get("direction"), ("down", "up"), "down"),
        n=_clamp_int(args.get("n"), 1, 30, 10),
        point=_effective_point(args, point_scope),
    )
    if not isinstance(rows, list):
        return dict(UNAVAILABLE)
    if not _rows_match_scope(rows, point_scope):
        return dict(SCOPE_UNAVAILABLE)
    checked = _scope_checked(rows, point_scope)
    if isinstance(checked, dict) and checked.get("error"):
        return checked
    return {"rows": _project_safe(checked, _TOP_MISS_SCHEMA)}


def _tool_get_order(args: dict, point_scope: str | None = None) -> dict:
    get_order = _fn("web.order", "get_order")
    forecast_dates = _fn("web.data", "forecast_dates")
    if (get_order is None or forecast_dates is None
            or not _supports_point_scope(get_order, point_scope)):
        return UNAVAILABLE
    dates = _call_fn(forecast_dates)
    if not dates:
        return {"error": "выпущенных заявок ещё нет"}
    target = _str_or_none(args.get("date")) or _str_or_none(args.get("target_date"))
    if target is None:
        target = dates[-1]
    elif not _DATE_RE.match(target):
        return {"error": "дата должна быть в формате ГГГГ-ММ-ДД",
                "last_forecast_date": dates[-1]}
    elif target not in dates:
        return {"error": f"заявки на {target} нет",
                "available_recent": dates[-10:]}

    res = _call_fn(get_order, target_date=target,
                   point=_effective_point(args, point_scope))
    if not isinstance(res, dict):
        return dict(UNAVAILABLE)
    res = _scope_checked(res, point_scope)
    if isinstance(res, dict) and res.get("error"):
        return res
    rows = res.get("rows") or []
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        return dict(UNAVAILABLE)
    if not _rows_match_scope(rows, point_scope):
        return dict(SCOPE_UNAVAILABLE)
    source_meta = res.get("meta") if isinstance(res.get("meta"), dict) else {}
    source_points = source_meta.get("points")
    if point_scope is not None and isinstance(source_points, (list, tuple, set)):
        if any(str(point).strip() and str(point) != point_scope
               for point in source_points):
            return dict(SCOPE_UNAVAILABLE)

    # Never forward arbitrary page metadata to an external model.  Rebuild the
    # small, point-safe subset used by the assistant; all counts come from the
    # already scoped rows rather than potentially network-wide source totals.
    excluded = source_meta.get("excluded_in_window")
    if not isinstance(excluded, list):
        excluded = []
    meta = {
        "target_date": target,
        "engine": str(source_meta.get("engine") or "")[:80],
        "version": str(source_meta.get("version") or "")[:120],
        "max_sale_date": str(source_meta.get("max_sale_date") or "")[:10],
        "excluded_in_window": [
            value for value in excluded[:8]
            if isinstance(value, str) and _DATE_RE.match(value)
        ],
        "n_rows": len(rows),
        "has_baseline": bool(source_meta.get("has_baseline")),
        "edited_count": sum(row.get("qty_user") is not None for row in rows),
    }

    def eff(r: dict) -> float:
        q = r.get("qty_user")
        return float(q if q is not None else (r.get("qty") or 0))

    by_point: dict[str, dict] = {}
    by_group: dict[str, dict] = {}
    for r in rows:
        p = r.get("point") or "без точки"
        g = r.get("group") or "без группы"
        sp = by_point.setdefault(p, {"point": p, "qty": 0.0, "rows": 0})
        sg = by_group.setdefault(g, {"group": g, "qty": 0.0, "rows": 0})
        sp["qty"] += eff(r); sp["rows"] += 1
        sg["qty"] += eff(r); sg["rows"] += 1
    for st in (*by_point.values(), *by_group.values()):
        st["qty"] = int(round(st["qty"]))

    top = sorted(rows, key=eff, reverse=True)[:30]
    top_rows = [{
        "point": r.get("point"), "sku": r.get("name"), "group": r.get("group"),
        "qty": r.get("qty"), "qty_user": r.get("qty_user"),
    } for r in top]

    return {
        "meta": meta,
        "total_qty_effective": int(round(sum(eff(r) for r in rows))),
        "by_point": sorted(by_point.values(), key=lambda s: -s["qty"]),
        "by_group": sorted(by_group.values(), key=lambda s: -s["qty"]),
        "top_rows_by_qty": top_rows,
    }


def _tool_get_writeoffs(args: dict, point_scope: str | None = None) -> dict:
    fn = _fn("web.analytics", "writeoffs")
    if fn is None or not _supports_point_scope(fn, point_scope):
        return UNAVAILABLE
    result = _call_fn(
        fn,
        point=_effective_point(args, point_scope),
        days=_clamp_int(args.get("days"), 1, 120, 28),
    )
    result = _strict_scope_checked(result, point_scope)
    if isinstance(result, dict) and result.get("error"):
        return result
    if point_scope is not None and not _rows_match_scope(
            result.get("by_point") or [], point_scope):
        return dict(SCOPE_UNAVAILABLE)
    safe = _project_safe(result, _WRITEOFFS_SCHEMA)
    if point_scope is not None:
        safe["point"] = point_scope
        safe["by_point"] = [
            row for row in (safe.get("by_point") or [])
            if row.get("point") == point_scope
        ]
        safe["from"], safe["to"] = _visible_date_bounds(safe.get("days") or [])
    return safe


def _tool_get_shelf_top(args: dict, point_scope: str | None = None) -> dict:
    fn = _fn("web.analytics", "shelf_top")
    if fn is None or not _supports_point_scope(fn, point_scope):
        return UNAVAILABLE
    result = _call_fn(
        fn,
        n=_clamp_int(args.get("n"), 1, 30, 10),
        point=_effective_point(args, point_scope),
    )
    result = _strict_scope_checked(result, point_scope)
    if isinstance(result, dict) and result.get("error"):
        return result
    if point_scope is not None:
        if not _rows_match_scope(result.get("deficit") or [], point_scope):
            return dict(SCOPE_UNAVAILABLE)
        if not _rows_match_scope(result.get("writeoff") or [], point_scope):
            return dict(SCOPE_UNAVAILABLE)
    return _project_safe(result, _SHELF_SCHEMA)


def _tool_get_comparison(args: dict, point_scope: str | None = None) -> dict:
    """Сравнение движков за прошедшие дни (вкладка «Сравнение»)."""
    fn = _fn("web.comparison", "summary")
    if fn is None or not _supports_point_scope(fn, point_scope):
        return UNAVAILABLE
    base = args.get("base") if args.get("base") in ("demand", "sold") else "demand"
    res = _call_fn(
        fn,
        period=args.get("period") if args.get("period") in _PERIODS else "30d",
        point=_effective_point(args, point_scope),
        base=base,
    )
    res = _strict_scope_checked(res, point_scope)
    if isinstance(res, dict) and res.get("error"):
        return res
    if point_scope is not None and not _rows_match_scope(
            res.get("by_point") or [], point_scope):
        return dict(SCOPE_UNAVAILABLE)
    safe = _project_safe(res, _COMPARISON_SCHEMA)
    if point_scope is not None:
        safe["point"] = point_scope
        safe["points"] = [point_scope]
        daily = safe.get("daily") or []
        first, last = _visible_date_bounds(daily)
        safe["available_days"] = len(daily)
        safe["first_date"] = first
        safe["last_date"] = last
        safe["by_point"] = [
            row for row in (safe.get("by_point") or [])
            if row.get("point") == point_scope
        ]
    return safe


_REGISTRY = {
    "get_meta": _tool_get_meta,
    "get_sales": _tool_get_sales,
    "get_accuracy": _tool_get_accuracy,
    "get_top_miss": _tool_get_top_miss,
    "get_order": _tool_get_order,
    "get_writeoffs": _tool_get_writeoffs,
    "get_shelf_top": _tool_get_shelf_top,
    "get_comparison": _tool_get_comparison,
}

TOOL_NAMES = tuple(_REGISTRY)


# --- схемы для API (OpenAI function calling) -----------------------------------

_POINT_PROP = {"type": "string",
               "description": "Каноническое имя точки (например «Точка 2»); "
                              "не указано — вся сеть."}
_PERIOD_PROP = {"type": "string", "enum": list(_PERIODS),
                "description": "Период: last — последний день сверки, 7d, 30d, all."}

OPENAI_TOOLS = [
    {"type": "function", "function": {
        "name": "get_meta",
        "description": "Текущие дата/время локального времени UTC+10, свежесть продаж по каждой точке "
                       "и даты, на которые выпущены заявки. Обязательно зови для любых "
                       "относительных дат («сегодня», «вчера», «последняя заявка»).",
        "parameters": {"type": "object", "properties": {}},
    }},
    {"type": "function", "function": {
        "name": "get_order",
        "description": "Выпущенная заявка на дату (по умолчанию — самая свежая): итог, "
                       "суммы по точкам и группам, топ-30 строк по количеству, правки "
                       "сотрудников. Зови на вопросы «сколько заказано», «что в заявке».",
        "parameters": {"type": "object", "properties": {
            "date": {"type": "string",
                     "description": "Дата заявки ГГГГ-ММ-ДД; не указана — свежая."},
            "point": _POINT_PROP,
        }},
    }},
    {"type": "function", "function": {
        "name": "get_accuracy",
        "description": "Свод точности ML-движка с 11.08.2026: WAPE и перекос прогноза и "
                       "факта заявки, деньги промаха, правки, разбивка по точкам и дням. "
                       "Зови на вопросы «насколько точен прогноз», «кто ошибается больше».",
        "parameters": {"type": "object", "properties": {
            "period": _PERIOD_PROP,
            "point": _POINT_PROP,
        }},
    }},
    {"type": "function", "function": {
        "name": "get_top_miss",
        "description": "Топ промахов прогноза по строкам (позиция × день). "
                       "direction=down — недодали (потери), up — заказали лишнее.",
        "parameters": {"type": "object", "properties": {
            "period": _PERIOD_PROP,
            "direction": {"type": "string", "enum": ["down", "up"],
                          "description": "down — прогноз меньше продаж, up — больше."},
            "n": {"type": "integer", "minimum": 1, "maximum": 30,
                  "description": "Сколько строк вернуть (по умолчанию 10)."},
            "point": _POINT_PROP,
        }},
    }},
    {"type": "function", "function": {
        "name": "get_sales",
        "description": "Динамика продаж (шт) и уценки u40 по дням или неделям за период. "
                       "Зови на вопросы «как продажи», «сколько продали за неделю/месяц».",
        "parameters": {"type": "object", "properties": {
            "granularity": {"type": "string", "enum": ["day", "week"],
                            "description": "Шаг ряда: день или неделя."},
            "preset": {"type": "string", "enum": list(_PRESETS),
                       "description": "Окно от свежей продажи: day/week/month/quarter/year."},
            "point": _POINT_PROP,
        }},
    }},
    {"type": "function", "function": {
        "name": "get_writeoffs",
        "description": "Списания (шт и ₽) и уценка u40 за окно до 120 дней: по дням и по "
                       "точкам. Зови на вопросы «сколько списываем», «где списания больше».",
        "parameters": {"type": "object", "properties": {
            "point": _POINT_PROP,
            "days": {"type": "integer", "minimum": 1, "maximum": 120,
                     "description": "Окно в днях (по умолчанию 28)."},
        }},
    }},
    {"type": "function", "function": {
        "name": "get_comparison",
        "description": "Сравнение движков за прошедшие дни: базовая формула (КВП) против "
                       "ML-ядра против заявки сотрудника — цена промаха в тыс ₽, штуки, "
                       "разрезы по дням и точкам. Зови на вопросы «что точнее», «чем ядро "
                       "лучше формулы», «кто выигрывает на такой-то точке».",
        "parameters": {"type": "object", "properties": {
            "period": _PERIOD_PROP,
            "point": _POINT_PROP,
            "base": {"type": "string", "enum": ["demand", "sold"],
                     "description": "Эталон промаха: demand — спрос полки (продано + "
                                    "недопродано, по умолчанию), sold — продажи по 1С."},
        }},
    }},
    {"type": "function", "function": {
        "name": "get_shelf_top",
        "description": "Топ пар «точка × позиция» за 28 дней «полки»: дефицит (недопродажи "
                       "и упущенная маржа) и списания (потерянная себестоимость). Зови на "
                       "вопросы «чего не хватает», «что чаще всего списываем».",
        "parameters": {"type": "object", "properties": {
            "n": {"type": "integer", "minimum": 1, "maximum": 30,
                  "description": "Сколько строк в каждом топе (по умолчанию 10)."},
        }},
    }},
]


# --- исполнение и упаковка результата ------------------------------------------

def _to_json(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"), default=str)


def _collect_list_slots(node, out: list) -> None:
    """Все (родитель-словарь, ключ) со списками — кандидаты на обрезку."""
    if isinstance(node, dict):
        for k, v in node.items():
            if isinstance(v, list):
                out.append((node, k))
            _collect_list_slots(v, out)
    elif isinstance(node, list):
        for item in node:
            _collect_list_slots(item, out)


def _cap(obj) -> str:
    """JSON ≤ MAX_RESULT_BYTES: самый длинный список режем пополам, пока не влезет."""
    text = _to_json(obj)
    for _ in range(40):
        if len(text.encode("utf-8")) <= MAX_RESULT_BYTES:
            return text
        slots: list = []
        _collect_list_slots(obj, slots)
        slots = [(p, k) for p, k in slots
                 if isinstance(p.get(k), list) and len(p[k]) > 3]
        if not slots:
            return _to_json({"error": "результат слишком большой — сузьте запрос",
                             "truncated": True})
        parent, key = max(slots, key=lambda s: len(s[0][s[1]]))
        parent[key] = parent[key][:max(3, len(parent[key]) // 2)]
        if isinstance(obj, dict):
            obj["truncated"] = True
        text = _to_json(obj)
    return _to_json({"error": "результат слишком большой — сузьте запрос",
                     "truncated": True})


def execute_tool(name: str, args: dict | None, *, point_scope: str | None = None) -> str:
    """Единственная точка входа петли tool-use: имя по белому списку, аргументы —
    что дал LLM (санитария внутри обработчиков). Всегда возвращает JSON-строку."""
    handler = _REGISTRY.get(name)
    if handler is None:
        return _to_json({"error": f"неизвестный инструмент: {name}",
                         "available": list(TOOL_NAMES)})
    if not isinstance(args, dict):
        args = {}
    try:
        result = handler(args, point_scope)
    except Exception:
        logger.exception("ИИ-инструмент {n} упал", n=name)
        result = dict(UNAVAILABLE)
    return _cap(result)
