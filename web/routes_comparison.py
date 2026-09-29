# -*- coding: utf-8 -*-
"""API вкладки «Сравнение»: формула КВП vs ML-ядро vs факт по дням.

Подключается оркестратором: app.include_router(routes_comparison.router).
Авторизация — только администратор через require_admin.
Хендлеры — обычные def (внутри pandas): FastAPI уводит их в тредпул.
"""
from __future__ import annotations

import os
from pathlib import Path

import pandas as pd
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import FileResponse, JSONResponse
from loguru import logger
from starlette.background import BackgroundTask

from web.access import require_admin
from web.access import principal_from_session
from web import analytics, comparison, svgchart
from web.export import make_export_tempfile, xlsx_safe_frame

router = APIRouter(
    prefix="/api",
    dependencies=[Depends(require_admin)],
)


def _scope_point(request: Request, requested: str | None) -> tuple[str | None, bool]:
    """Return the canonical point allowed by the signed session."""
    principal = principal_from_session(request)
    requested = (requested or "").strip() or None
    if principal.is_admin:
        return requested, True
    own = analytics.point_name_for_key(principal.point_key or "")
    if requested is not None and requested != own:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Нет доступа к другой точке")
    return own, False


def _err(text: str, code: int = status.HTTP_400_BAD_REQUEST) -> JSONResponse:
    return JSONResponse({"ok": False, "message": text}, status_code=code)


def _dm(iso: str) -> str:
    return f"{iso[8:10]}.{iso[5:7]}"


def _svg_money(daily: list[dict]) -> str:
    """Главный график — деньги промаха по дням у трёх методов."""
    series = {
        "ML-ядро": [(_dm(r["date"]), r["miss_ml_k"]) for r in daily],
        "формула КВП": [(_dm(r["date"]), r["miss_baseline_k"]) for r in daily],
        "сотрудник": [(_dm(r["date"]), r["miss_human_k"]) for r in daily],
    }
    return svgchart.lines(series, title="Цена промаха по дням", unit="тыс ₽", h=330)


def _svg_units(daily: list[dict], base: str) -> str:
    ref_name = "спрос" if base == "demand" else "продажи"
    series = {
        "ML-ядро": [(_dm(r["date"]), r["ml"]) for r in daily],
        "формула КВП": [(_dm(r["date"]), r["baseline"]) for r in daily],
        "сотрудник": [(_dm(r["date"]), r["human"]) for r in daily],
        ref_name: [(_dm(r["date"]), r["demand" if base == "demand" else "sold"])
                   for r in daily],
    }
    return svgchart.lines(series, title="Сколько заказали и сколько нужно было",
                          unit="шт", h=330)


@router.get("/comparison")
def api_comparison(
    request: Request,
    period: str = Query("30d"),
    point: str | None = Query(None),
    base: str = Query("demand"),
    scope: str = Query("paired"),
):
    """Свод сравнения движков: деньги промаха, штуки, разрезы по дням и точкам."""
    if period not in comparison.PERIOD_DAYS:
        return _err("Период должен быть last / 7d / 30d / all.")
    if base not in comparison.BASES:
        return _err("Эталон должен быть demand (спрос) или sold (продажи).")
    if scope not in ("paired", "all"):
        return _err("Множество строк должно быть paired или all.")
    point, is_admin = _scope_point(request, point)
    try:
        s = comparison.summary(period, point, base, scope)
        if not is_admin:
            s["points"] = [point]
        daily = s.get("daily") or []
        s["svg_money"] = _svg_money(daily) if daily else None
        s["svg_units"] = _svg_units(daily, base) if daily else None
        s["health"] = comparison.health(point)
    except Exception:
        logger.exception("comparison: не удалось собрать свод (period={p})", p=period)
        return _err("Не удалось прочитать данные пересчёта — см. лог сервиса.",
                    status.HTTP_500_INTERNAL_SERVER_ERROR)
    return JSONResponse(s)


@router.get("/comparison/top")
def api_comparison_top(
    request: Request,
    period: str = Query("30d"),
    point: str | None = Query(None),
    base: str = Query("demand"),
    n: int = Query(20, ge=1, le=100),
):
    """Где движки разошлись сильнее всего и кто оказался ближе к факту."""
    if period not in comparison.PERIOD_DAYS:
        return _err("Период должен быть last / 7d / 30d / all.")
    point, _is_admin = _scope_point(request, point)
    try:
        rows = comparison.top_gap(period, point, n, base)
    except Exception:
        logger.exception("comparison/top: не удалось собрать топ расхождений")
        return _err("Не удалось прочитать данные пересчёта — см. лог сервиса.",
                    status.HTTP_500_INTERNAL_SERVER_ERROR)
    return JSONResponse({"period": period, "rows": rows})


@router.get("/comparison/export")
def api_comparison_export(
    request: Request,
    period: str = Query("30d"),
    point: str | None = Query(None),
    base: str = Query("demand"),
    scope: str = Query("paired"),
):
    """Выгрузка сравнения в Excel: строка = день × точка × позиция."""
    point, _is_admin = _scope_point(request, point)
    try:
        df = comparison.export_rows(period, point, base, scope)
        if df.empty:
            return _err("За выбранный период данных нет.")
        fd, tmp = make_export_tempfile(prefix="sravnenie_")
        os.close(fd)
        path = Path(tmp)
        try:
            with pd.ExcelWriter(path, engine="openpyxl") as xl:
                xlsx_safe_frame(df).to_excel(
                    xl, sheet_name="Сравнение", index=False)
                ws = xl.sheets["Сравнение"]
                ws.freeze_panes = "A2"
                widths = {"A": 11, "B": 22, "C": 42, "D": 20}
                for col, w in widths.items():
                    ws.column_dimensions[col].width = w
        except Exception:
            path.unlink(missing_ok=True)
            raise
    except Exception:
        logger.exception("comparison/export: не удалось собрать файл")
        return _err("Не удалось собрать файл — см. лог сервиса.",
                    status.HTTP_500_INTERNAL_SERVER_ERROR)
    name = f"Сравнение_методов_{period}.xlsx"
    return FileResponse(
        path, filename=name,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        background=BackgroundTask(os.remove, path))
