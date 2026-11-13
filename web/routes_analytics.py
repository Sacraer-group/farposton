# -*- coding: utf-8 -*-
"""API вкладки «Аналитика»: динамика продаж, часы, дни недели, списания,
дефицит-топ, год-к-году, скорость раскупания, топ позиций, выгрузка Excel
+ управление нетипичными днями (excluded_days.json).

Подключается оркестратором: app.include_router(routes_analytics.router).
Авторизация — только администратор через require_admin.
Хендлеры — обычные def (не async): все функции блокирующие (DuckDB/parquet),
FastAPI гоняет их в тредпуле; тяжёлое — в SQL.
"""
from __future__ import annotations

import os

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import FileResponse, JSONResponse
from loguru import logger
from pydantic import BaseModel
from starlette.background import BackgroundTask

from web.access import require_admin
from web.access import principal_from_session
from web import analytics, svgchart

router = APIRouter(
    prefix="/api",
    dependencies=[Depends(require_admin)],
)

XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _dm(iso: str) -> str:
    """'2026-08-03' → '03.08' (подпись оси X)."""
    return f"{iso[8:10]}.{iso[5:7]}"


def _fail(where: str, exc: Exception):
    """ValueError — честная 400 с русским текстом; остальное — 500 в лог."""
    if isinstance(exc, HTTPException):
        raise exc
    if isinstance(exc, ValueError):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc))
    logger.exception("analytics/{w}: не удалось собрать данные", w=where)
    raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR,
                        "Не удалось прочитать данные — см. лог сервиса.")


def _scope_point(request: Request, requested: str | None) -> str | None:
    """Закрепить менеджера за точкой на сервере; параметру браузера не доверяем."""
    principal = principal_from_session(request)
    requested = (requested or "").strip() or None
    if principal.is_admin:
        return requested
    own = analytics.point_name_for_key(principal.point_key or "")
    if requested is not None and requested != own:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Нет доступа к другой точке")
    return own


def _points_safe(request: Request) -> list[str]:
    try:
        principal = principal_from_session(request)
        if principal.is_admin:
            return analytics.points()
        return [analytics.point_name_for_key(principal.point_key or "")]
    except Exception:
        return []   # снимка может не быть — фильтр просто останется пустым


def _days(days: int | None, preset: str | None, default: int) -> int:
    """Окно в днях: явный days сильнее, потом пресет, потом дефолт раздела."""
    if days:
        return days
    if preset:
        return analytics.preset_days(preset)
    return default


@router.get("/analytics/status")
def api_analytics_status(request: Request):
    """Общий статус сверки источников; не содержит сырых строк."""
    principal = principal_from_session(request)
    try:
        point = _scope_point(request, None)
        payload = analytics.quality_overview(point)
    except Exception as exc:
        _fail("status", exc)
    return JSONResponse({**payload, "can_manage_excluded": principal.is_admin})


# --- динамика продаж -----------------------------------------------------------

@router.get("/analytics/sales")
def api_sales(
    request: Request,
    granularity: str = Query("day"),
    point: str | None = Query(None),
    frm: str | None = Query(None, alias="from"),
    to: str | None = Query(None),
    preset: str | None = Query(None),
):
    """Динамика продаж и уценки: день/неделя, фильтр точки, период from/to или preset."""
    try:
        point = _scope_point(request, point)
        s = analytics.sales_series(granularity, point or None, frm or None,
                                   to or None, preset or None)
    except Exception as exc:
        _fail("sales", exc)
    rows = s["rows"]
    series = {"продано, шт": [(_dm(r["date"]), r["sold"]) for r in rows],
              "уценка (у40), шт": [(_dm(r["date"]), r["u40"]) for r in rows]}
    title = ("Продажи по дням, шт" if s["granularity"] == "day"
             else "Продажи по неделям, шт (неделя с понедельника)")
    svg = svgchart.lines(series, title=title + (f" — {point}" if point else " — вся сеть"),
                         unit="шт") if rows else None
    quality = analytics.section_quality(
        "sales", s["from"], s["to"], point=point)
    return JSONResponse({"table": rows, "svg": svg, "from": s["from"], "to": s["to"],
                         "granularity": s["granularity"], "quality": quality,
                         "points": _points_safe(request)})


# --- профиль по часам ----------------------------------------------------------

@router.get("/analytics/hours")
def api_hours(request: Request,
              point: str | None = Query(None),
              days: int | None = Query(None, ge=1, le=400),
              preset: str | None = Query(None)):
    """Во сколько покупают: профиль продаж по часам чеков за последние N дней."""
    try:
        point = _scope_point(request, point)
        s = analytics.hours_profile(point or None, _days(days, preset, 28))
    except Exception as exc:
        _fail("hours", exc)
    rows = s["rows"]
    svg = svgchart.bars(
        [r["label"] for r in rows], [r["per_day"] for r in rows],
        title=f"Продажи по часам, шт в день — {point or 'вся сеть'} "
              f"(средние за {s['n_days']} дн)",
        unit="шт/день") if rows else None
    quality = analytics.section_quality(
        "hours", s["from"], s["to"], extra=s, point=point)
    return JSONResponse({"table": rows, "svg": svg, "n_days": s["n_days"],
                         "calendar_days": s["calendar_days"],
                         "from": s["from"], "to": s["to"],
                         "quality": quality, "points": _points_safe(request)})


# --- дни недели ----------------------------------------------------------------

@router.get("/analytics/weekdays")
def api_weekdays(request: Request,
                 point: str | None = Query(None),
                 days: int | None = Query(None, ge=14, le=400),
                 preset: str | None = Query(None)):
    """Средние продажи по дням недели за последние N дней."""
    try:
        point = _scope_point(request, point)
        s = analytics.weekday_profile(point or None, _days(days, preset, 56))
    except Exception as exc:
        _fail("weekdays", exc)
    rows = s["rows"]
    svg = svgchart.bars(
        [r["name"] for r in rows], [r["avg_units"] for r in rows],
        title=f"Средние продажи по дням недели, шт — {point or 'вся сеть'}",
        unit="шт/день") if rows else None
    quality = analytics.section_quality(
        "weekdays", s["from"], s["to"], extra=s, point=point)
    return JSONResponse({"table": rows, "svg": svg, "from": s["from"], "to": s["to"],
                         "calendar_days": s.get("calendar_days"),
                         "observed_days": s.get("observed_days"),
                         "quality": quality, "points": _points_safe(request)})


# --- списания и уценка ---------------------------------------------------------

@router.get("/analytics/writeoffs")
def api_writeoffs(request: Request,
                  point: str | None = Query(None),
                  days: int | None = Query(None, ge=14, le=400),
                  preset: str | None = Query(None)):
    """Списания и уценка по дням (график) и по точкам (таблица)."""
    try:
        point = _scope_point(request, point)
        s = analytics.writeoffs(point or None, _days(days, preset, 56))
    except Exception as exc:
        _fail("writeoffs", exc)
    series = {"списано, шт": [(_dm(r["date"]), r["wo_units"]) for r in s["days"]],
              "уценка (у40), шт": [(_dm(r["date"]), r["u40_units"]) for r in s["days"]]}
    svg = svgchart.lines(
        series, title=f"Списания и уценка по дням, шт — {point or 'вся сеть'}",
        unit="шт") if s["days"] else None
    quality = analytics.section_quality(
        "writeoffs", s["from"], s["to"],
        extra={"writeoff_amount_coverage_pct": s["writeoff_amount_coverage_pct"]},
        point=point)
    return JSONResponse({"table": s["by_point"], "days": s["days"], "svg": svg,
                         "from": s["from"], "to": s["to"],
                         "writeoff_amount_coverage_pct": s["writeoff_amount_coverage_pct"],
                         "quality": quality, "points": _points_safe(request)})


# --- дефицит-топ ---------------------------------------------------------------

@router.get("/analytics/shelf")
def api_shelf(request: Request,
              n: int = Query(20, ge=5, le=100),
              point: str | None = Query(None),
              days: int = Query(28, ge=7, le=91)):
    """Топ пар «точка × позиция» по недопродажам и списаниям за 28 дней полки."""
    try:
        point = _scope_point(request, point)
        s = analytics.shelf_top(n, point, days)
    except Exception as exc:
        _fail("shelf", exc)
    lost_series = {
        "оценено упущено за день": [(_dm(r["date"]), r["lost_units"]) for r in s["daily"]],
        "оценка упущенных продаж — среднее по доступным наблюдениям за 7 календарных дней": [
            (_dm(r["date"]), r["lost_units_ma7"]) for r in s["daily"]],
    }
    writeoff_series = {
        "списано за день": [(_dm(r["date"]), r["wo_units"]) for r in s["daily"]],
        "списания — среднее по доступным наблюдениям за 7 календарных дней": [
            (_dm(r["date"]), r["wo_units_ma7"]) for r in s["daily"]],
    }
    lost_margin_series = {
        "оценка упущенной маржи за день": [
            (_dm(r["date"]), r["lost_rub_margin"]) for r in s["daily"]],
        "оценка упущенной маржи — среднее по доступным наблюдениям за 7 календарных дней": [
            (_dm(r["date"]), r["lost_rub_margin_ma7"]) for r in s["daily"]],
    }
    writeoff_cost_series = {
        "оценка себестоимости списаний за день": [
            (_dm(r["date"]), r["wo_rub_cost"]) for r in s["daily"]],
        "оценка себестоимости списаний — среднее по доступным наблюдениям за 7 календарных дней": [
            (_dm(r["date"]), r["wo_rub_cost_ma7"]) for r in s["daily"]],
    }
    svg_lost = svgchart.lines(
        lost_series, title=f"Оценка упущенных продаж — {point or 'вся сеть'}",
        unit="шт") if s["daily"] else None
    svg_writeoff = svgchart.lines(
        writeoff_series, title=f"Списания — {point or 'вся сеть'}",
        unit="шт") if s["daily"] else None
    svg_lost_margin = svgchart.lines(
        lost_margin_series, title=f"Оценка упущенной маржи — {point or 'вся сеть'}",
        unit="₽") if s["daily"] else None
    svg_writeoff_cost = svgchart.lines(
        writeoff_cost_series,
        title=f"Оценка себестоимости списаний — {point or 'вся сеть'}",
        unit="₽") if s["daily"] else None
    quality = analytics.section_quality(
        "shelf", s["from"], s["to"], extra=s, point=point)
    return JSONResponse({"table": s["deficit"],
                         "deficit_network": s["deficit_network"],
                         "deficit_details": s["deficit_details"],
                         "writeoff": s["writeoff"],
                         "daily": s["daily"], "totals": s["totals"],
                         "svg_lost": svg_lost, "svg_writeoff": svg_writeoff,
                         "svg_lost_margin": svg_lost_margin,
                         "svg_writeoff_cost": svg_writeoff_cost,
                         "from": s["from"], "to": s["to"], "source": s["source"],
                         "point": s["point"],
                         "provenance": s["provenance"], "quality": quality,
                         "points": _points_safe(request)})


@router.get("/analytics/shelf_drilldown")
def api_shelf_drilldown(
    request: Request,
    sku: str = Query(..., min_length=1, max_length=300),
    sku_key: str | None = Query(None, min_length=1, max_length=100),
    point: str | None = Query(None),
    day: str | None = Query(None, alias="date"),
    as_of: str | None = Query(None),
    days: int = Query(28, ge=7, le=91),
):
    """Network → point → day detail for one product from the mature shelf."""
    try:
        is_admin = principal_from_session(request).is_admin
        point = _scope_point(request, point)
        result = analytics.shelf_drilldown(
            sku, point, day, days, sku_key=sku_key, as_of=as_of,
            allow_network_as_of=is_admin)
    except Exception as exc:
        _fail("shelf_drilldown", exc)
    hourly = result.get("hourly") or []
    svg_hourly = None
    if hourly and result.get("selected"):
        selected = result["selected"]
        svg_hourly = svgchart.lines(
            {"чистые продажи": [
                (row["label"], row["qty"]) for row in hourly
            ]},
            title=(f"Продажи по чекам: {result['sku']} — {result['point']} — "
                   f"{selected['date']}"),
            unit="шт",
        )
    quality = analytics.section_quality(
        "shelf", result.get("from"), result.get("to"),
        extra=result, point=point)
    return JSONResponse({**result, "svg_hourly": svg_hourly, "quality": quality})


@router.get("/analytics/shelf_drilldown/export")
def api_shelf_drilldown_export(
    request: Request,
    sku: str = Query(..., min_length=1, max_length=300),
    sku_key: str | None = Query(None, min_length=1, max_length=100),
    point: str | None = Query(None),
    day: str | None = Query(None, alias="date"),
    as_of: str | None = Query(None),
    days: int = Query(28, ge=7, le=91),
):
    """Excel ровно той детализации полки, которую видит текущий пользователь."""
    try:
        is_admin = principal_from_session(request).is_admin
        point = _scope_point(request, point)
        result = analytics.shelf_drilldown(
            sku, point, day, days, sku_key=sku_key, as_of=as_of,
            allow_network_as_of=is_admin)
        quality = analytics.section_quality(
            "shelf", result.get("from"), result.get("to"),
            extra=result, point=point)
        path, filename = analytics.build_shelf_drilldown_xlsx(result, quality)
    except Exception as exc:
        _fail("shelf_drilldown/export", exc)
    return FileResponse(
        str(path), filename=filename, media_type=XLSX_MIME,
        background=BackgroundTask(os.remove, str(path)))


# --- год к году ----------------------------------------------------------------

@router.get("/analytics/yoy")
def api_yoy(request: Request,
            point: str | None = Query(None), preset: str = Query("month")):
    """Продажи/списания/уценка за период и за тот же период год назад, с дельтами.
    Точке без истории год назад — честный {available:false, reason}."""
    try:
        point = _scope_point(request, point)
        s = analytics.yoy(point or None, preset)
    except Exception as exc:
        _fail("yoy", exc)
    quality = None
    if s.get("available"):
        quality = analytics.section_quality(
            "yoy", s["cur"]["from"], s["cur"]["to"],
            extra={
                "writeoff_amount_coverage_pct": s["cur"]["wo_sum_coverage_pct"],
                "comparison_from": s["prev"]["from"],
                "comparison_to": s["prev"]["to"],
            },
            point=point)
    return JSONResponse({**s, "quality": quality, "points": _points_safe(request)})


# --- раннее прекращение продаж (оценка по чекам, не факт остатка) --------------

@router.get("/analytics/sellout_speed")
def api_sellout_speed(request: Request,
                      point: str | None = Query(None),
                      days: int | None = Query(None, ge=7, le=400),
                      preset: str | None = Query(None)):
    """Ранняя последняя продажа и наблюдаемый темп по чекам — для проверки."""
    try:
        point = _scope_point(request, point)
        s = analytics.sellout_speed(point or None, _days(days, preset, 28))
    except Exception as exc:
        _fail("sellout_speed", exc)
    quality = analytics.section_quality(
        "sellout_speed", s["from"], s["to"], extra=s, point=point)
    return JSONResponse({**s, "quality": quality, "points": _points_safe(request)})


# --- топ позиций ---------------------------------------------------------------

@router.get("/analytics/top_sku")
def api_top_sku(request: Request,
                point: str | None = Query(None),
                days: int | None = Query(None, ge=1, le=400),
                preset: str | None = Query(None),
                by: str = Query("qty")):
    """Топ-20 позиций по продажам за окно: шт или ₽."""
    try:
        point = _scope_point(request, point)
        s = analytics.top_sku(point or None, _days(days, preset, 28), by)
    except Exception as exc:
        _fail("top_sku", exc)
    quality = analytics.section_quality(
        "top_sku", s["from"], s["to"], point=point)
    return JSONResponse({**s, "quality": quality, "points": _points_safe(request)})


# --- выгрузка Excel ------------------------------------------------------------

@router.get("/analytics/export")
def api_export(
    request: Request,
    section: str = Query(...),
    point: str | None = Query(None),
    granularity: str = Query("day"),
    frm: str | None = Query(None, alias="from"),
    to: str | None = Query(None),
    preset: str | None = Query(None),
    days: int | None = Query(None, ge=1, le=400),
    by: str = Query("qty"),
    n: int = Query(20, ge=5, le=100),
):
    """Excel-файл раздела аналитики с теми же параметрами, что у его ручки;
    временный файл удаляется после отдачи (BackgroundTask)."""
    try:
        point = _scope_point(request, point)
        path, fname = analytics.build_export_xlsx(
            section, point=point or None, granularity=granularity,
            frm=frm or None, to=to or None, preset=preset or None,
            days=days, by=by, n=n)
    except Exception as exc:
        _fail("export", exc)
    return FileResponse(str(path), filename=fname, media_type=XLSX_MIME,
                        background=BackgroundTask(os.remove, str(path)))


# --- нетипичные дни ------------------------------------------------------------

class ExcludedPayload(BaseModel):
    date: str
    reason: str = ""
    action: str = "add"


@router.get("/excluded-days")
def api_excluded_get(request: Request):
    """Текущий список нетипичных дней."""
    try:
        principal = principal_from_session(request)
        return JSONResponse({"days": analytics.excluded_days_list(),
                             "editable": principal.is_admin,
                             "message": (None if principal.is_admin else
                                         "Менеджер видит список, но менять глобальные "
                                         "исключения может только администратор.")})
    except Exception as exc:
        _fail("excluded-days", exc)


@router.post("/excluded-days")
def api_excluded_post(request: Request, payload: ExcludedPayload):
    """Добавить/убрать нетипичный день. Ошибка валидации → {ok:false, message}
    (fetchJSON на странице показывает message как есть)."""
    principal = principal_from_session(request)
    if not principal.is_admin:
        raise HTTPException(status.HTTP_403_FORBIDDEN,
                            "Глобальные нетипичные дни может менять только администратор")
    try:
        days = analytics.excluded_days_update(payload.date, payload.reason, payload.action,
                                              actor=principal.username)
    except ValueError as exc:
        return JSONResponse({"ok": False, "message": str(exc)},
                            status_code=status.HTTP_400_BAD_REQUEST)
    except Exception:
        logger.exception("excluded-days: не удалось записать файл")
        return JSONResponse({"ok": False, "message": "Не удалось записать файл списка — "
                             "см. лог сервиса."},
                            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR)
    logger.info("excluded-days: {a} {d} ({r}) — {u}",
                a=payload.action, d=payload.date, r=payload.reason or "—",
                u=principal.username)
    return JSONResponse({"ok": True, "days": days})
