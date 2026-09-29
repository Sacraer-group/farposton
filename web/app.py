"""FastAPI-сервис прогноза заказов готовой продукции (базовая методика).

Каркас: пять страниц-вкладок (Заявка / Точность / Аналитика / Сводки / Помощник)
за авторизацией сервиса, + /api/meta. Данные веб только
ЧИТАЕТ — из снимка DuckDB и parquet-выходов дневного прогона; сам прогон не запускает.
Запуск:  python -m web.app  (из каталога prognoz/).
"""
from __future__ import annotations

from . import paths  # noqa: F401  — кладёт prognoz/src в sys.path ДО импорта ядра

import os
import time
import copy
from contextlib import asynccontextmanager
from datetime import date, timedelta
from pathlib import Path

from fastapi import Depends, FastAPI, Form, HTTPException, Request, status
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from loguru import logger
from starlette.middleware.sessions import SessionMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware

from . import data
from .config import load_settings
from .logging_setup import setup_logging
from .auth import (
    RateLimiter, authenticate_user, client_ip, ip_allowed, require_auth, session_is_valid,
)
from .access import principal_from_session, demo_no_auth

settings = load_settings()

STATIC = Path(__file__).resolve().parent / "static"
VERSION = "0.1.0"

# Серверное время UTC+3; операционный день в часовом поясе UTC+10.
LOCAL_SHIFT_H = 7

# Снимок БД подменяется дневным прогоном раз в сутки; старше 26 часов — значит
# ночной прогон не отработал (час запаса поверх суточного цикла).
SNAPSHOT_STALE_H = 26

# TLS считается включённым, только если оба файла реально существуют: пути в .env
# прописаны заранее, а сертификат появляется позже (make-cert.ps1). Иначе https_only
# у cookie сломал бы вход по HTTP ещё до выпуска сертификата.
_TLS_ON = bool(settings.tls_cert and settings.tls_key
               and settings.tls_cert.exists() and settings.tls_key.exists())
_SECURE_COOKIE = bool(_TLS_ON or settings.session_secure_cookie)

_limiter = RateLimiter(settings.ratelimit_max_fails, settings.ratelimit_window_seconds)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    setup_logging(settings.log_file, settings.log_level)
    logger.info(
        "Старт веб-сервиса прогноза. {host}:{port} (TLS: {tls}). LLM: {llm}. "
        "Доступ: {acc}. Доверенных прокси: {proxy}. Снимок БД: {db}.",
        host=settings.host, port=settings.port, tls=("вкл" if _TLS_ON else "ВЫКЛ"),
        llm=(settings.llm_model or "выкл") if settings.llm_api_key else "выкл",
        acc=(f"allowlist {len(settings.allowed_ips)} IP" if settings.allowed_ips else "любой IP (пароль)"),
        proxy=len(settings.trusted_proxy_ips),
        db=settings.db_snapshot,
    )
    if not _TLS_ON and not settings.public_https:
        logger.warning("TLS ВЫКЛЮЧЕН — пароль пойдёт по сети открыто. Для прод-доступа "
                       "снаружи выпустите сертификат (настройках сервера).")
    if not settings.db_snapshot.exists():
        logger.warning("Снимок БД {p} ещё не создан — страницы поднимутся, "
                       "но данных не покажут.", p=settings.db_snapshot)
    try:
        yield
    finally:
        logger.info("Веб-сервис остановлен")


app = FastAPI(title="Прогноз заказов", docs_url=None, redoc_url=None, lifespan=lifespan)
if settings.allowed_hosts:
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=list(settings.allowed_hosts))
app.add_middleware(
    SessionMiddleware,
    secret_key=settings.secret_key,
    max_age=settings.session_ttl_seconds,
    same_site="strict",
    https_only=_SECURE_COOKIE,
)

@app.middleware("http")
async def security_headers(request: Request, call_next):
    """Базовые security-заголовки на каждый ответ.

    X-Frame-Options DENY запрещает встраивать страницы по URL в чужие iframe;
    сводка на вкладке «Сводки» встраивается через iframe srcdoc — это не загрузка
    по URL, DENY ей не мешает."""
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "same-origin"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    response.headers["Content-Security-Policy"] = "frame-ancestors 'none'; base-uri 'self'; object-src 'none'"
    if settings.public_https:
        response.headers["Strict-Transport-Security"] = "max-age=31536000"
    return response


# Общие css/js без авторизации: стилям секретность не нужна, а login.html проще
app.mount("/assets", StaticFiles(directory=STATIC / "assets"), name="assets")

# Разделы: каждый живёт своим APIRouter-ом (все под require_auth внутри)
from . import (routes_accuracy, routes_analytics, routes_chat,  # noqa: E402
               routes_comparison, routes_order, routes_reports)

app.include_router(routes_order.router)
app.include_router(routes_accuracy.router)
app.include_router(routes_comparison.router)
app.include_router(routes_analytics.router)
app.include_router(routes_reports.router)
app.include_router(routes_chat.router)


# ----- страницы ---------------------------------------------------------------

def _page(request: Request, name: str):
    """Страница за авторизацией; без валидной сессии — на /login (как в такси)."""
    if not session_is_valid(request):
        return RedirectResponse("/login", status_code=status.HTTP_303_SEE_OTHER)
    return FileResponse(STATIC / name)


def _admin_page(request: Request, name: str):
    """Сетевая аналитическая страница доступна только администратору."""
    if not session_is_valid(request):
        return RedirectResponse("/login", status_code=status.HTTP_303_SEE_OTHER)
    if not principal_from_session(request).is_admin:
        return FileResponse(STATIC / "access-denied.html", status_code=403)
    return FileResponse(STATIC / name)


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return _page(request, "index.html")


@app.get("/accuracy", response_class=HTMLResponse)
async def accuracy(request: Request):
    return _admin_page(request, "accuracy.html")


@app.get("/comparison", response_class=HTMLResponse)
async def comparison(request: Request):
    return _admin_page(request, "comparison.html")


@app.get("/analytics", response_class=HTMLResponse)
async def analytics(request: Request):
    return _admin_page(request, "analytics.html")


@app.get("/reports", response_class=HTMLResponse)
async def reports(request: Request):
    return _page(request, "reports.html")


@app.get("/chat", response_class=HTMLResponse)
async def chat(request: Request):
    return _page(request, "chat.html")


@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    if session_is_valid(request):
        return RedirectResponse("/", status_code=status.HTTP_303_SEE_OTHER)
    return FileResponse(STATIC / "login.html")


@app.post("/login")
async def login_submit(request: Request, username: str = Form(...), password: str = Form(...)):
    ip = client_ip(request)
    username = username.strip()
    if not ip_allowed(request, settings.allowed_ips):
        logger.warning("Вход с неразрешённого IP: {ip}", ip=ip)
        return JSONResponse({"ok": False, "message": "Доступ с этого адреса запрещён."},
                            status_code=status.HTTP_403_FORBIDDEN)
    wait = _limiter.retry_after(ip, username)
    if wait:
        return JSONResponse(
            {"ok": False, "message": f"Слишком много попыток. Подождите {wait} с и попробуйте снова."},
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        )
    principal = authenticate_user(username, password, settings.users_json)
    if principal is not None:
        # чистится только ведро этого логина: общий счётчик IP не сбрасывается,
        # иначе успех своим кабинетом открывал бы перебор чужого пароля
        _limiter.reset(ip, username)
        request.session["auth_at"] = int(time.time())
        request.session['username'] = principal.username
        request.session['role'] = principal.role
        request.session['point_key'] = principal.point_key
        logger.info('Успешный вход: ip={ip}, user={user}, role={role}', ip=ip, user=principal.username, role=principal.role)
        return JSONResponse({"ok": True, "redirect": "/"})
    _limiter.record_fail(ip, username)
    logger.warning("Неудачный вход: ip={ip}, user={user}", ip=ip, user=username)
    return JSONResponse({"ok": False, "message": "Неверный логин или пароль."},
                        status_code=status.HTTP_401_UNAUTHORIZED)


@app.post("/logout")
async def logout(request: Request):
    request.session.clear()
    return JSONResponse({"ok": True, "redirect": "/login"})


# ----- API --------------------------------------------------------------------

def health_payload() -> dict:
    """Чистая функция пробы живости — её же зовёт интеграционная проверка.

    ok = снимок БД моложе 26 часов И есть прогноз на завтра или позже
    (заявка считается на D+2, «свежий forecast_log» = будущая дата на месте).
    Отдаётся без авторизации, поэтому никаких имён точек и бизнес-чисел."""
    age = data.snapshot_age_hours()
    dates = data.forecast_dates()
    latest = dates[-1] if dates else None
    tomorrow = (date.today() + timedelta(days=1)).isoformat()
    ok = age is not None and age < SNAPSHOT_STALE_H and latest is not None and latest >= tomorrow
    return {
        "ok": ok,
        "version": VERSION,
        "snapshot_age_h": age,
        "latest_forecast": latest,
    }


@app.get("/health")
async def health():
    """Проба для сторожа run-daily.ps1 (curl с localhost) и мониторинга. БЕЗ авторизации."""
    try:
        return JSONResponse(health_payload())
    except Exception:
        logger.exception("health: проба живости упала")
        return JSONResponse({"ok": False, "version": VERSION,
                             "snapshot_age_h": None, "latest_forecast": None})


@app.get("/api/meta")
async def api_meta(request: Request, _: None = Depends(require_auth)):
    """Сводка «что вообще есть»: даты прогнозов, точки, свежесть продаж, статус LLM."""
    if not ip_allowed(request, settings.allowed_ips):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Доступ с этого адреса запрещён.")
    try:
        fresh = copy.deepcopy(data.freshness())
    except Exception:
        # Снимка может ещё не быть (каркас поднят раньше данных) — страница живёт
        logger.exception("meta: не удалось прочитать свежесть из снимка БД")
        fresh = {"points": [], "max_sale_date": None, "freshness": []}
    # snapshot_mtime отдаём ВРЕМЕНЕМ ЛОКАЛЬНОГО ВРЕМЕНИ UTC+10: mtime на диске — серверное
    # (московское) время без пояса, а «когда обновились данные» читает человек
    # на точке. Сдвиг фиксированный, поэтому просто +7 часов, без tz-базы.
    snap_mt = data.snapshot_mtime()
    principal = principal_from_session(request)
    if not principal.is_admin:
        from . import analytics as analytics_mod
        try:
            point_name = analytics_mod.point_name_for_key(principal.point_key or "")
            fresh["points"] = [p for p in fresh["points"] if str(p) == point_name]
            fresh["freshness"] = [
                row for row in fresh["freshness"]
                if str(row.get("point", "")) == point_name
            ]
        except Exception as e:  # noqa: BLE001
            # точка менеджера не нашлась (опечатка в WEB_USERS_JSON, точка на паузе,
            # снимок недоступен) — кабинет должен открыться пустым, а не умереть 500-ми
            # (фикс 18.08, восстановлен при слиянии 07.09)
            logger.warning("meta: точка пользователя {u} не сопоставлена: {e}",
                           u=principal.username, e=e)
            fresh["points"], fresh["freshness"] = [], []
        fresh["max_sale_date"] = max(
            (row.get("last_sale") for row in fresh["freshness"]
             if row.get("last_sale")),
            default=None,
        )
    from .routes_order import _business_today, _operational_date  # noqa: PLC0415
    business_today = _business_today()
    return JSONResponse({
        "dates": data.forecast_dates(),
        # операционный день локального времени UTC+10 и дата РАБОЧЕЙ заявки (завтра, D+1):
        # страница открывает её по умолчанию; правки — завтра и послезавтра,
        # дальние даты помечает как предварительные (см. routes_order)
        "demo_no_auth": demo_no_auth(),
        "business_date": business_today.isoformat(),
        "operational_date": _operational_date(business_today).isoformat(),
        "points": fresh["points"],
        "max_sale_date": fresh["max_sale_date"],
        "freshness": fresh["freshness"],
        "llm_ok": bool(settings.llm_api_key),
        "version": VERSION,
        "snapshot_mtime": ((snap_mt + timedelta(hours=LOCAL_SHIFT_H))
                           .isoformat(timespec="minutes") if snap_mt else None),
        "snapshot_age_h": data.snapshot_age_hours(),
        "access": {
            "role": principal.role,
            "analytics": principal.is_admin,
        },
    })


def main() -> None:
    import uvicorn
    uvicorn.run(
        "web.app:app",
        host=settings.host,
        port=settings.port,
        ssl_certfile=str(settings.tls_cert) if _TLS_ON else None,
        ssl_keyfile=str(settings.tls_key) if _TLS_ON else None,
        proxy_headers=False,
        log_level=settings.log_level.lower(),
    )


if __name__ == "__main__":
    main()
