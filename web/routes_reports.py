# -*- coding: utf-8 -*-
"""API вкладки «Сводки»: список утренних сводок и их содержимое.

Сводки генерит дневной прогон в analysis/output/ai_reports/<дата>.{json,md,html};
веб их только читает с диска, ничего не пересчитывая.
Подключается оркестратором: app.include_router(routes_reports.router).
"""
from __future__ import annotations

import json
import re
from datetime import date as calendar_date

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import JSONResponse
from loguru import logger

from web import data
from web.access import principal_from_session
from web.auth import require_auth

router = APIRouter(prefix="/api", dependencies=[Depends(require_auth)])

AI_REPORTS = None  # Test override; production resolves its configured root dynamically.


def _reports_dir():
    if AI_REPORTS is not None:
        return AI_REPORTS
    resolver = getattr(data, "ai_reports_dir", None)
    return resolver() if resolver else data.OUTPUT / "ai_reports"


def _operational_payload(request: Request, day: str | None = None) -> dict:
    from prognoz.report.operational import load_latest, scope_report, render_html
    principal = principal_from_session(request)
    try:
        report = load_latest(_reports_dir(), day)
        report = scope_report(report, None if principal.is_admin else principal.point_key)
    except (OSError, ValueError) as exc:
        logger.warning("Operational report unavailable: {kind}", kind=type(exc).__name__)
        raise HTTPException(503, "Сводка временно недоступна; требуется проверка отчёта.") from None
    # No network HTML/LLM text survives point scoping.
    return {"date": report["trade_date"], "version": report["version"],
            "complete": report["complete"], "generated_at": report["generated_at"],
            "html": render_html(report), "json": report}

# Имя файла = дата; всё остальное (и попытки path traversal) отсекаем этим шаблоном
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _require_network_report_access(request: Request) -> None:
    """Generated reports contain network totals and cannot be point-filtered safely."""
    if not principal_from_session(request).is_admin:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "Сводки по отдельным точкам пока не формируются; сетевые доступны только администратору.",
        )


# --- чистые функции (их же зовёт интеграционная проверка) ---------------------

@data.ttl_cache(seconds=60)
def list_reports() -> list[dict]:
    """[{date, has_llm_text}] по файлам ai_reports/*.json, свежие первыми.

    has_llm_text — поле `llm` из json сводки: генерился ли текст помощника
    (false = сводка только с числами, без текста ИИ).
    ttl_cache(60): список читает json-файлы с диска на каждый заход на вкладку;
    новая сводка появляется раз в сутки — минуты запаздывания незаметны.
    """
    reports_dir = _reports_dir()
    if not reports_dir.is_dir():
        return []
    out: list[dict] = []
    for p in sorted(reports_dir.glob("*.json"), reverse=True):
        if not _DATE_RE.match(p.stem):
            continue
        has_llm = False
        try:
            has_llm = bool(json.loads(p.read_text(encoding="utf-8")).get("llm"))
        except (OSError, json.JSONDecodeError):
            logger.warning("Сводка {name} не прочиталась как json", name=p.name)
        out.append({"date": p.stem, "has_llm_text": has_llm})
    return out


def latest_report() -> dict:
    """{date, html} самой свежей сводки, у которой есть html. Нет ни одной — KeyError."""
    reports_dir = _reports_dir()
    if reports_dir.is_dir():
        for p in sorted(reports_dir.glob("*.html"), reverse=True):
            if _DATE_RE.match(p.stem):
                return {"date": p.stem, "html": p.read_text(encoding="utf-8")}
    raise KeyError("нет ни одной сводки")


def report_payload(date: str) -> dict:
    """{date, html, json} сводки за дату. Нет ни html, ни json — KeyError."""
    if not _DATE_RE.match(date):
        raise KeyError(date)
    html_p = _reports_dir() / f"{date}.html"
    json_p = _reports_dir() / f"{date}.json"
    if not html_p.exists() and not json_p.exists():
        raise KeyError(date)
    html = html_p.read_text(encoding="utf-8") if html_p.exists() else None
    data = None
    if json_p.exists():
        try:
            data = json.loads(json_p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            logger.warning("Сводка {date}.json не прочиталась", date=date)
    return {"date": date, "html": html, "json": data}


# --- эндпоинты ----------------------------------------------------------------

@router.get("/reports")
async def api_reports(request: Request):
    """Список сводок: [{date, has_llm_text}], свежие первыми."""
    items = {}
    if principal_from_session(request).is_admin:
        items = {r["date"]: r for r in list_reports()}
    for folder in (_reports_dir() / "v2").glob("????-??-??"):
        try:
            p = _operational_payload(request, folder.name)
        except (KeyError, ValueError):
            continue
        except HTTPException as exc:
            if exc.status_code != 503:
                raise
            continue
        items[folder.name] = {"date": folder.name, "has_llm_text": any(
            bool(x.get("narrative")) for x in p["json"]["points"]), "version": p["version"], "complete": p["complete"]}
    return JSONResponse(sorted(items.values(), key=lambda x: x["date"], reverse=True))


# ВАЖНО: /reports/latest объявлен ДО /reports/{date}, иначе «latest» уйдёт в {date}
@router.get("/reports/latest")
async def api_reports_latest(request: Request):
    """Самая свежая сводка: {date, html}. 404 — если сводок ещё нет."""
    try:
        return JSONResponse(_operational_payload(request))
    except KeyError:
        pass
    if not principal_from_session(request).is_admin:
        raise HTTPException(404, "Сводки для вашей точки пока нет.")
    _require_network_report_access(request)
    try:
        return JSONResponse(latest_report())
    except KeyError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Сводок пока нет — они появятся после первого утреннего прогона.")


@router.get("/reports/{date}")
async def api_reports_date(date: str, request: Request):
    """Сводка за дату YYYY-MM-DD: {date, html, json}. 404 — нет такой."""
    try:
        if not _DATE_RE.fullmatch(date):
            raise ValueError
        calendar_date.fromisoformat(date)
    except ValueError:
        raise HTTPException(404, "Некорректная дата сводки.") from None
    try:
        return JSONResponse(_operational_payload(request, date))
    except (KeyError, ValueError):
        pass
    if not principal_from_session(request).is_admin:
        raise HTTPException(404, "Сводки для вашей точки за эту дату нет.")
    _require_network_report_access(request)
    try:
        return JSONResponse(report_payload(date))
    except KeyError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Сводки за {date} нет.")
