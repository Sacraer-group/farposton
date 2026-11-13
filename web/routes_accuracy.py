# -*- coding: utf-8 -*-
"""API вкладки «Точность»: свод нового движка (с 11.08) + SVG-графики.

Подключается оркестратором: app.include_router(routes_accuracy.router).
Авторизация — только администратор через require_admin.
Хендлеры — обычные def (внутри тяжёлый pandas): FastAPI сам уводит их в тредпул,
event loop не блокируется.
"""
from __future__ import annotations

import os

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import FileResponse, JSONResponse
from loguru import logger
from starlette.background import BackgroundTask

from web.access import require_admin
from web.access import principal_from_session
from web import accuracy, analytics, svgchart

router = APIRouter(
    prefix="/api",
    dependencies=[Depends(require_admin)],
)

XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _scope_point(request: Request, requested: str | None) -> tuple[str | None, bool]:
    principal = principal_from_session(request)
    requested = (requested or "").strip() or None
    if principal.is_admin:
        return requested, True
    own = analytics.point_name_for_key(principal.point_key or "")
    if requested is not None and requested != own:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Нет доступа к другой точке")
    return own, False


def _err(text: str, code: int = status.HTTP_400_BAD_REQUEST) -> JSONResponse:
    """Ошибка в форме, которую понимает fetchJSON на страницах: {"message": …}."""
    return JSONResponse({"ok": False, "message": text}, status_code=code)


def _dm(iso: str) -> str:
    """'2026-08-11' → '11.08' (подпись оси X)."""
    return f"{iso[8:10]}.{iso[5:7]}"


def _svg_daily(daily: list[dict]) -> str:
    """Линии по дням: прогноз ПО / факт заявки / продажи, шт."""
    series = {
        "прогноз ПО": [(_dm(r["date"]), r["forecast"]) for r in daily],
        "факт заявки": [(_dm(r["date"]), r["order"]) for r in daily],
        "продажи": [(_dm(r["date"]), r["sold"]) for r in daily],
    }
    return svgchart.lines(series, title="Прогноз, факт заявки и продажи по дням",
                          unit="шт", h=330)


def _svg_wape(daily: list[dict]) -> str:
    """Бары WAPE прогноза ПО по дням (позиция × день, к продажам)."""
    rows = [r for r in daily if r.get("wape_forecast") is not None]
    return svgchart.bars([_dm(r["date"]) for r in rows],
                         [r["wape_forecast"] for r in rows],
                         title="WAPE прогноза ПО по дням (позиция × день)", unit="%")


@router.get("/accuracy")
def api_accuracy(
    request: Request,
    period: str = Query("7d"),
    level: str = Query("network"),
    point: str | None = Query(None),
):
    """Свод точности: тройное сравнение (прогноз ПО / факт заявки / продажи),
    деньги промаха, списания, правки сотрудника + SVG-графики."""
    if period not in accuracy.PERIOD_DAYS:
        return _err("Период должен быть last / 7d / 30d / all.")
    if level not in accuracy.LEVELS:
        return _err("Уровень должен быть network или point.")
    point, is_admin = _scope_point(request, point)
    if not is_admin:
        level = "point"
    if level == "point" and not point:
        return _err("Для уровня «Точка» нужно выбрать точку.")
    try:
        s = accuracy.summary(period, level, point or None)
        if not is_admin:
            s["points"] = [point]
        daily = s.get("daily") or []
        s["svg_daily"] = _svg_daily(daily) if daily else None
        s["svg_wape"] = _svg_wape(daily) if daily else None
    except Exception:
        logger.exception("accuracy: не удалось собрать свод (period={p}, level={l})",
                         p=period, l=level)
        return _err("Не удалось прочитать данные сверки — см. лог сервиса.",
                    status.HTTP_500_INTERNAL_SERVER_ERROR)
    return JSONResponse(s)


@router.get("/accuracy/top")
def api_accuracy_top(
    request: Request,
    period: str = Query("7d"),
    direction: str = Query("down"),
    point: str | None = Query(None),
    n: int = Query(15, ge=1, le=100),
):
    """Топ промахов ПО: down — недодали (настоящие потери), up — лишнее (перезаказ)."""
    if period not in accuracy.PERIOD_DAYS:
        return _err("Период должен быть last / 7d / 30d / all.")
    if direction not in ("up", "down"):
        return _err("Направление должно быть up или down.")
    point, _is_admin = _scope_point(request, point)
    try:
        rows = accuracy.top_miss(period, direction, n, point or None)
    except Exception:
        logger.exception("accuracy/top: не удалось собрать топ (period={p}, dir={d})",
                         p=period, d=direction)
        return _err("Не удалось прочитать данные сверки — см. лог сервиса.",
                    status.HTTP_500_INTERNAL_SERVER_ERROR)
    return JSONResponse({"period": period, "direction": direction, "rows": rows})


@router.get("/accuracy/export")
def api_accuracy_export(
    request: Request,
    period: str = Query("7d"),
    level: str = Query("network"),
    point: str | None = Query(None),
    n: int = Query(15, ge=1, le=100),
):
    """Excel со всеми таблицами текущего среза вкладки «Точность»."""
    if period not in accuracy.PERIOD_DAYS:
        return _err("Период должен быть last / 7d / 30d / all.")
    if level not in accuracy.LEVELS:
        return _err("Уровень должен быть network или point.")
    point, is_admin = _scope_point(request, point)
    if not is_admin:
        level = "point"
    if level == "point" and not point:
        return _err("Для уровня «Точка» нужно выбрать точку.")
    try:
        path, filename = accuracy.build_export_xlsx(
            period, level, point or None, n)
    except ValueError as exc:
        return _err(str(exc))
    except Exception:
        logger.exception("accuracy/export: не удалось собрать Excel")
        return _err("Не удалось собрать Excel — см. лог сервиса.",
                    status.HTTP_500_INTERNAL_SERVER_ERROR)
    return FileResponse(
        str(path), filename=filename, media_type=XLSX_MIME,
        background=BackgroundTask(os.remove, str(path)))
